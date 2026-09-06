"""Checkpointed, read-only agent loop. Credentials never enter persisted state."""

import asyncio
import json
import time
from copy import deepcopy
from typing import TypedDict
from uuid import UUID

from jsonschema import Draft202012Validator
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from sqlalchemy import select

from app.core.credentials import (
    CredentialCipher,
    CredentialCipherUnavailable,
    CredentialDecryptionError,
)
from app.db.models import Checkpoint, ModelConnection, StepExecution, Tool
from app.schemas.tools import ToolDefinition
from app.services.model_client import LangChainModelClient, StreamRedactor, sanitize
from app.services.model_connections import OpenAICompatibleConnectionTester
from app.services.tools import ToolExecutionError, ToolRuntime, ToolService
from app.services.worker import DeterministicRunError, StepOutcome


class AgentGraphState(TypedDict):
    runtime: dict
    executed: int
    stopped: bool


class AgentExecutor:
    def __init__(self, worker):
        self.worker = worker
        key = worker.settings.credential_encryption_key
        self.cipher = CredentialCipher.from_base64_key(key.get_secret_value() if key else None)
        self.model_client = worker.model_client or LangChainModelClient()
        self.tools = worker.tool_runtime or ToolRuntime()

    @staticmethod
    def tool_name(binding):
        return f"tool_{UUID(binding['tool_id']).hex}_v{binding['version']}"

    @staticmethod
    def validate(schema, value, code):
        if schema is not None and not Draft202012Validator(schema).is_valid(value):
            raise DeterministicRunError(code, "Value does not match the configured JSON Schema")

    def execute(self, run_id, *, max_steps=None):
        self.worker.check_live(run_id)
        with self.worker.session_factory() as session:
            run = self.worker._locked_owned_run(session, run_id)
            snapshot = deepcopy(run.config_snapshot)
            state = self.load_runtime(session, run_id)
            if state is None:
                self.validate(snapshot["agent"]["input_schema"], run.input, "input_schema_invalid")
                prompt = snapshot["agent"]["system_prompt"]
                if snapshot["agent"].get("output_schema") is not None:
                    prompt += "\nReturn your final answer as JSON matching this schema:\n"
                    prompt += json.dumps(snapshot["agent"]["output_schema"])
                state = {
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": json.dumps(run.input, ensure_ascii=False)},
                    ],
                    "pending": [],
                    "turn": 0,
                    "tool_index": 0,
                }
        graph = self.build_graph(run_id, snapshot, max_steps=max_steps)
        # No implicit LangSmith upload of prompts, tool results, or decrypted credentials.
        with tracing_context(enabled=False):
            graph.invoke(
                {"runtime": state, "executed": 0, "stopped": False},
                config={"recursion_limit": snapshot["agent"]["execution_limits"]["max_steps"] + 2},
            )

    @staticmethod
    def load_runtime(session, run_id):
        checkpoint = session.scalar(
            select(Checkpoint)
            .where(
                Checkpoint.run_id == run_id,
            )
            .order_by(Checkpoint.sequence.desc())
            .limit(1)
        )
        return deepcopy(checkpoint.state.get("runtime")) if checkpoint else None

    def build_graph(self, run_id, snapshot, *, max_steps=None):
        def route(state: AgentGraphState):
            if state["stopped"] or (max_steps is not None and state["executed"] >= max_steps):
                return END
            return "tool" if state["runtime"]["pending"] else "model"

        def advance(graph_state: AgentGraphState, kind: str):
            self.worker.check_live(run_id)
            state = deepcopy(graph_state["runtime"])
            if kind == "tool":
                key = f"tool_{state['turn']}_{state['tool_index']}"

                def handler(rid):
                    return self.tool_step(rid, snapshot, state)

                reserve = 0
                metadata = {"function": state["pending"][0]["function"]["name"]}
            else:
                with self.worker.session_factory() as session:
                    usage = dict(self.worker._locked_owned_run(session, run_id).usage)
                key = f"model_{state['turn']}"
                functions = self.functions(snapshot)
                # Conservative UTF-8 byte reservation, not a tokenizer/price estimate.
                input_reserve = (
                    len(json.dumps([state["messages"], functions], ensure_ascii=False).encode())
                    + 512
                )
                available = snapshot["agent"]["execution_limits"]["token_budget"] - usage.get(
                    "charged_tokens", 0
                )
                max_tokens = min(
                    snapshot["agent"]["model_config"]["max_output_tokens"],
                    available - input_reserve,
                )
                if max_tokens < 1:
                    raise DeterministicRunError(
                        "token_budget_exceeded", "Insufficient token budget for the next model call"
                    )
                reserve = input_reserve + max_tokens

                def handler(rid):
                    return self.model_step(rid, snapshot, state, key, functions, max_tokens)

                metadata = {"model": snapshot["model"]["model_name"], "reserved_tokens": reserve}
            continued = self.worker._run_step(
                run_id, key, handler, kind=kind, reserved_tokens=reserve, metadata=metadata
            )
            with self.worker.session_factory() as session:
                run, _ = self.worker._context(session, run_id)
                # Read back only the committed state, never advance from uncommitted output.
                return {
                    "runtime": self.load_runtime(session, run_id) or state,
                    "executed": graph_state["executed"] + 1,
                    "stopped": not continued or run.status not in {"running", "cancelling"},
                }

        builder = StateGraph(AgentGraphState)
        builder.add_node("model", lambda state: advance(state, "model"))
        builder.add_node("tool", lambda state: advance(state, "tool"))
        routes = {"model": "model", "tool": "tool", END: END}
        builder.add_conditional_edges(START, route, routes)
        builder.add_conditional_edges("model", route, routes)
        builder.add_conditional_edges("tool", route, routes)
        # Existing fenced SQL transactions persist each node with its StepExecution.
        # Do not add an independently committed LangGraph checkpointer alongside them.
        return builder.compile()

    def functions(self, snapshot):
        functions = []
        for binding in snapshot["tools"]:
            definition = ToolDefinition.model_validate(binding["definition"])
            # Unsupported bindings are not exposed as callable model capabilities.
            if not self.read_only(definition):
                continue
            functions.append(
                {
                    "type": "function",
                    "function": {
                        "name": self.tool_name(binding),
                        "description": definition.description,
                        "parameters": definition.input_schema,
                    },
                }
            )
        return functions

    @staticmethod
    def read_only(definition):
        return (
            definition.enabled
            and definition.risk_level == "read"
            and not definition.requires_approval
            and (definition.tool_type == "mcp" or definition.config.method == "GET")
        )

    def credentials(self, snapshot):
        secrets = []
        with self.worker.session_factory() as session:
            connection = session.scalar(
                select(ModelConnection).where(
                    ModelConnection.id == UUID(snapshot["model"]["connection_id"]),
                    ModelConnection.workspace_id == self.worker.workspace_id,
                )
            )
            if connection is None or not connection.enabled:
                raise DeterministicRunError(
                    "model_unavailable", "Model connection is disabled or unavailable"
                )
            if connection.base_url != snapshot["model"]["base_url"]:
                raise DeterministicRunError(
                    "model_connection_changed", "Model endpoint changed; create a new run"
                )
            try:
                secret = OpenAICompatibleConnectionTester(cipher=self.cipher).resolve_secret(
                    connection
                )
                if not secret:
                    raise DeterministicRunError(
                        "credential_not_found", "Model credential is unavailable"
                    )
                secrets.append(secret)
                for binding in snapshot["tools"]:
                    tool = session.scalar(
                        select(Tool).where(
                            Tool.id == UUID(binding["tool_id"]),
                            Tool.workspace_id == self.worker.workspace_id,
                        )
                    )
                    if tool:
                        secrets.append(
                            ToolService(
                                session, self.worker.workspace_id, cipher=self.cipher
                            ).resolve_credential(tool)
                        )
            except (
                CredentialCipherUnavailable,
                CredentialDecryptionError,
                ToolExecutionError,
            ) as exc:
                raise DeterministicRunError(
                    "credential_unavailable", "Stored credentials could not be resolved"
                ) from exc
        return secret, secrets

    def model_step(self, run_id, snapshot, state, key, functions, max_tokens):
        secret, secrets = self.credentials(snapshot)
        with self.worker.session_factory() as session:
            attempt = session.scalar(
                select(StepExecution.attempt_count).where(
                    StepExecution.run_id == run_id, StepExecution.step_key == key
                )
            )
        pending_text = ""
        last_emit = time.monotonic()

        def emit_text(delta, *, final=False):
            nonlocal pending_text, last_emit
            pending_text += delta
            if pending_text and (
                final or len(pending_text) >= 128 or time.monotonic() - last_emit >= 0.2
            ):
                self.worker.emit(
                    run_id,
                    "model_output_delta",
                    {
                        "step": key,
                        "attempt": attempt,
                        "delta": pending_text,
                    },
                )
                pending_text = ""
                last_emit = time.monotonic()

        redactor = StreamRedactor(secrets, emit_text)
        settings = snapshot["agent"]["model_config"]

        async def operation():
            async with asyncio.timeout(
                min(settings["timeout_seconds"], snapshot["model"]["timeout_seconds"])
            ):
                return await self.model_client.complete(
                    model=snapshot["model"],
                    secret=secret,
                    messages=state["messages"],
                    functions=functions,
                    settings=settings,
                    max_tokens=max_tokens,
                    emit=redactor.feed,
                )

        try:
            reply = self.worker.guarded(run_id, operation)
        except TimeoutError as exc:
            raise DeterministicRunError(
                "model_timed_out", "Model call time limit exceeded"
            ) from exc
        redactor.feed("", final=True)
        emit_text("", final=True)
        message = sanitize(reply.message, secrets)
        state["messages"].append(message)
        state["pending"] = deepcopy(message.get("tool_calls", []))
        state["turn"] += 1
        state["tool_index"] = 0
        result = None
        if not state["pending"]:
            text = message.get("content") or ""
            schema = snapshot["agent"].get("output_schema")
            structured = None
            if schema is not None:
                try:
                    structured = json.loads(text)
                except ValueError as exc:
                    raise DeterministicRunError(
                        "output_schema_invalid", "Model output is not valid JSON"
                    ) from exc
                self.validate(schema, structured, "output_schema_invalid")
            result = {"text": text, "structured": structured}
        # Never persist credentials even if a user included one in the initial input.
        return StepOutcome(
            sanitize(state, secrets),
            result=result,
            usage=reply.usage,
            summary={
                "model": snapshot["model"]["model_name"],
                "tool_calls": len(state["pending"]),
                "usage": reply.usage,
            },
        )

    def tool_step(self, run_id, snapshot, state):
        call = state["pending"][0]
        binding = next(
            (
                item
                for item in snapshot["tools"]
                if self.tool_name(item) == call["function"]["name"]
            ),
            None,
        )
        if binding is None:
            raise DeterministicRunError("unbound_tool", "Model requested an unbound tool")
        definition = ToolDefinition.model_validate(binding["definition"])
        if not self.read_only(definition):
            raise DeterministicRunError(
                "tool_requires_approval", "Only read-only, approval-free tools may run"
            )
        try:
            arguments = json.loads(call["function"]["arguments"])
        except (ValueError, TypeError) as exc:
            raise DeterministicRunError(
                "invalid_tool_arguments", "Tool arguments must be valid JSON"
            ) from exc
        if not isinstance(arguments, dict):
            raise DeterministicRunError(
                "invalid_tool_arguments", "Tool arguments must be an object"
            )
        self.validate(definition.input_schema, arguments, "invalid_tool_arguments")
        _, secrets = self.credentials(snapshot)
        with self.worker.session_factory() as session:
            tool = session.scalar(
                select(Tool).where(
                    Tool.id == UUID(binding["tool_id"]),
                    Tool.workspace_id == self.worker.workspace_id,
                )
            )
            if tool is None or not tool.enabled:
                raise DeterministicRunError("tool_unavailable", "Tool is disabled or unavailable")
            current = ToolDefinition.model_validate(tool.draft)
            if not self.read_only(current):
                raise DeterministicRunError(
                    "tool_requires_approval", "Tool policy no longer permits automatic execution"
                )
            # Do not send a newly rotated credential to an obsolete destination.
            endpoint = "endpoint" if definition.tool_type == "http" else "server_url"
            if current.tool_type != definition.tool_type or getattr(
                current.config, endpoint
            ) != getattr(definition.config, endpoint):
                raise DeterministicRunError(
                    "tool_endpoint_changed", "Tool endpoint changed; publish and create a new run"
                )
            credential = ToolService(
                session, self.worker.workspace_id, cipher=self.cipher
            ).resolve_credential(tool)

        async def operation():
            async with asyncio.timeout(definition.config.timeout_seconds):
                return await self.tools.execute(definition, arguments, credential)

        try:
            output = self.worker.guarded(run_id, operation)
        except (ToolExecutionError, TimeoutError) as exc:
            raise DeterministicRunError(
                "tool_execution_failed", "Read-only tool call failed"
            ) from exc
        self.validate(definition.output_schema, output, "tool_output_schema_invalid")
        output = sanitize(output, secrets)
        encoded = json.dumps(output, ensure_ascii=False)
        if len(encoded.encode()) > 262144:
            raise DeterministicRunError("tool_result_too_large", "Tool result exceeded 256 KiB")
        state["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": encoded})
        state["pending"].pop(0)
        state["tool_index"] += 1
        return StepOutcome(
            sanitize(state, secrets),
            summary={
                "tool_id": binding["tool_id"],
                "version": binding["version"],
                "name": definition.name,
                "output_bytes": len(encoded.encode()),
            },
        )
