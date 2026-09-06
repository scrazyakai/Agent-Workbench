"""LangChain model integration; SDK owns request construction and SSE decoding."""

import json
from contextlib import aclosing
from dataclasses import dataclass

import httpx
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI
from langsmith import tracing_context
from openai import APIConnectionError, APIError, APIStatusError

from app.services.worker import DeterministicRunError, RetryableRunError


def sanitize(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [sanitize(item, secrets) for item in value]
    if isinstance(value, dict):
        return {sanitize(key, secrets): sanitize(item, secrets) for key, item in value.items()}
    return value


class StreamRedactor:
    """Retain possible credential prefixes, including matches spanning chunks."""

    def __init__(self, secrets, emit):
        self.secrets = [secret for secret in secrets if secret]
        self.emit = emit
        self.pending = ""

    def feed(self, text, *, final=False):
        self.pending += text
        cut = len(self.pending)
        if not final:
            for secret in self.secrets:
                for size in range(1, min(len(secret), len(self.pending) + 1)):
                    if self.pending.endswith(secret[:size]):
                        cut = min(cut, len(self.pending) - size)
            # A retained suffix may intersect a complete occurrence of another secret.
            changed = True
            while changed:
                changed = False
                for secret in self.secrets:
                    start = self.pending.rfind(secret, 0)
                    if 0 <= start < cut < start + len(secret):
                        cut = start
                        changed = True
        if cut:
            self.emit(sanitize(self.pending[:cut], self.secrets))
            self.pending = self.pending[cut:]


@dataclass
class ModelReply:
    message: dict
    usage: dict


class LangChainModelClient:
    def __init__(self, client_factory=httpx.AsyncClient, model_factory=ChatOpenAI):
        self.client_factory = client_factory
        self.model_factory = model_factory

    async def complete(self, *, model, secret, messages, functions, settings, max_tokens, emit):
        timeout = min(model["timeout_seconds"], settings["timeout_seconds"])
        aggregate = None
        size = 0
        try:
            # Transport configuration only; LangChain/SDK construct and send requests.
            with httpx.Client(timeout=timeout, follow_redirects=False) as sync_transport:
                async with self.client_factory(
                    timeout=timeout, follow_redirects=False
                ) as transport:
                    llm = self.model_factory(
                        model=model["model_name"],
                        api_key=secret,
                        base_url=model["base_url"],
                        temperature=settings["temperature"],
                        max_tokens=max_tokens,
                        timeout=timeout,
                        max_retries=0,
                        streaming=True,
                        stream_usage=True,
                        use_responses_api=False,
                        output_version="v0",
                        http_socket_options=(),
                        http_client=sync_transport,
                        http_async_client=transport,
                    )
                    runnable = (
                        llm.bind_tools(functions, parallel_tool_calls=False) if functions else llm
                    )
                    # Do not implicitly upload local runtime data via host tracing settings.
                    with tracing_context(enabled=False):
                        async with aclosing(runnable.astream(messages)) as stream:
                            async for chunk in stream:
                                if not isinstance(chunk, AIMessageChunk):
                                    raise ValueError("Unexpected model message")
                                # Bound decoded content. Wire framing/buffering belongs to SDK.
                                size += len(
                                    json.dumps(
                                        {
                                            "content": chunk.content,
                                            "extra": chunk.additional_kwargs,
                                            "tools": chunk.tool_call_chunks,
                                        },
                                        ensure_ascii=False,
                                    ).encode()
                                )
                                if size > 2_097_152:
                                    raise DeterministicRunError(
                                        "model_response_too_large",
                                        "Decoded model response exceeded 2 MiB",
                                    )
                                if chunk.additional_kwargs.get("refusal"):
                                    raise DeterministicRunError(
                                        "model_refused", "Model declined this request"
                                    )
                                if chunk.text:
                                    emit(chunk.text)
                                aggregate = chunk if aggregate is None else aggregate + chunk
        except APIStatusError as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                raise RetryableRunError(
                    "provider_unavailable", "Model provider is temporarily unavailable"
                ) from exc
            if exc.status_code in {401, 403}:
                raise DeterministicRunError(
                    "model_authentication_failed", "Model provider rejected credentials"
                ) from exc
            raise DeterministicRunError(
                "model_request_rejected", "Model provider rejected the request"
            ) from exc
        except (APIConnectionError, httpx.HTTPError) as exc:
            raise RetryableRunError(
                "provider_unreachable", "Model provider connection failed"
            ) from exc
        except APIError as exc:
            raise RetryableRunError("model_stream_error", "Model stream failed") from exc
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise DeterministicRunError(
                "invalid_model_response", "Model provider returned invalid streaming data"
            ) from exc
        return self.reply(aggregate)

    @staticmethod
    def reply(message):
        finished = message.response_metadata.get("finish_reason") if message else None
        if finished not in {"stop", "tool_calls"}:
            raise DeterministicRunError(
                "incomplete_model_response", "Model response did not finish normally"
            )
        if message.invalid_tool_calls:
            raise DeterministicRunError(
                "invalid_tool_arguments", "Model returned malformed tool arguments"
            )
        calls = message.tool_call_chunks
        if (finished == "tool_calls") != bool(calls):
            raise DeterministicRunError(
                "invalid_tool_calls", "Model returned inconsistent tool calls"
            )
        # LangChain assembles fragments. Validate strict JSON so incomplete arguments
        # repaired by its partial JSON parser cannot cause an external tool invocation.
        try:
            ids = [call["id"] for call in calls]
            if len(calls) > 100 or any(not item for item in ids) or len(ids) != len(set(ids)):
                raise ValueError("Invalid identifiers")
            for call in calls:
                if not call["name"]:
                    raise ValueError("Invalid function")
                if not isinstance(json.loads(call["args"]), dict):
                    raise ValueError("Arguments must be objects")
        except (ValueError, TypeError, KeyError) as exc:
            raise DeterministicRunError(
                "invalid_tool_calls", "Model returned invalid tool calls"
            ) from exc
        output = {"role": "assistant", "content": message.text or None}
        if calls:
            output["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": call["args"],
                    },
                }
                for call in calls
            ]
        elif not message.text:
            # Some compatible SDKs omit refusal-only deltas; never report empty success.
            raise DeterministicRunError("empty_model_response", "Model returned no answer")
        usage = message.usage_metadata
        measured = {"unmeasured_calls": 1}
        if usage is not None:
            keys = ("input_tokens", "output_tokens", "total_tokens")
            if (
                not all(type(usage.get(key)) is int and usage[key] >= 0 for key in keys)
                or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]
            ):
                raise DeterministicRunError(
                    "invalid_model_response", "Model provider returned invalid usage"
                )
            measured = {
                "prompt_tokens": usage["input_tokens"],
                "completion_tokens": usage["output_tokens"],
                "total_tokens": usage["total_tokens"],
            }
        return ModelReply(output, measured)
