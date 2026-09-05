import asyncio
import ipaddress
import json
import socket
from copy import deepcopy
from datetime import UTC, datetime
from time import perf_counter
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import httpx
import httpx2
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.credentials import (
    CredentialCipher,
    CredentialCipherUnavailable,
    CredentialDecryptionError,
)
from app.core.errors import DomainError
from app.db.models import Tool, ToolVersion, utcnow
from app.schemas.tools import (
    HttpToolConfig,
    McpToolConfig,
    ToolCreate,
    ToolDefinition,
    ToolPage,
    ToolPatch,
    ToolRead,
    ToolTestResult,
    ToolVersionPage,
    ToolVersionRead,
)


class ToolExecutionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message


def credential_headers(auth, credential: str | None) -> dict[str, str]:
    if auth.type == "none":
        return {}
    if not credential:
        raise ToolExecutionError("credential_not_found", "Tool credential is not configured")
    if auth.type == "bearer":
        return {"Authorization": f"Bearer {credential}"}
    return {auth.header_name: credential}


def resolve_argument(arguments: dict, reference: str):
    value = arguments
    for part in reference.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ToolExecutionError(
                "tool_input_invalid",
                f"Mapped input field '{reference}' is missing",
            )
        value = value[part]
    return value


def redact_secret(value, secret: str | None):
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    if isinstance(value, list):
        return [redact_secret(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: redact_secret(item, secret) for key, item in value.items()}
    return value


class OutboundTargetPolicy:
    """Validate fixed tool targets before opening an outbound connection."""

    async def validate(self, url: str, allowed_hosts: list[str]) -> None:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if not hostname or hostname.lower() not in {host.lower() for host in allowed_hosts}:
            raise ToolExecutionError("tool_target_denied", "Tool target is not allowed")
        try:
            addresses = await asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ToolExecutionError(
                "tool_target_unreachable", "Tool target cannot be resolved"
            ) from exc
        if not addresses:
            raise ToolExecutionError("tool_target_unreachable", "Tool target cannot be resolved")
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                raise ToolExecutionError(
                    "tool_target_denied",
                    "Tool target resolves to a non-public address",
                )


class HttpToolExecutor:
    def __init__(self, client_factory=httpx.AsyncClient, target_policy=None):
        self.client_factory = client_factory
        self.target_policy = target_policy or OutboundTargetPolicy()

    async def execute(
        self,
        config: HttpToolConfig,
        arguments: dict,
        credential: str | None,
    ):
        url = config.endpoint
        for placeholder, reference in config.path_params.items():
            marker = "{" + placeholder + "}"
            if marker not in url:
                raise ToolExecutionError(
                    "tool_configuration_invalid",
                    f"Path placeholder '{placeholder}' is not present in endpoint",
                )
            value = resolve_argument(arguments, reference)
            url = url.replace(marker, quote(str(value), safe=""))
        if "{" in url or "}" in url:
            raise ToolExecutionError(
                "tool_configuration_invalid",
                "Endpoint contains unresolved path placeholders",
            )
        await self.target_policy.validate(url, config.allowed_hosts)
        params = {
            name: str(resolve_argument(arguments, reference))
            for name, reference in config.query_params.items()
        }
        headers = credential_headers(config.auth, credential)
        for name, reference in config.header_params.items():
            if name.lower() in {"host", "content-length", "transfer-encoding", "authorization"}:
                raise ToolExecutionError(
                    "tool_configuration_invalid",
                    f"Header '{name}' cannot be mapped from tool input",
                )
            headers[name] = str(resolve_argument(arguments, reference))
        request_kwargs = {
            "method": config.method,
            "url": url,
            "params": params,
            "headers": headers,
        }
        if config.body_mode == "json":
            request_kwargs["json"] = arguments
        status_code = 0
        content_type = ""
        content = bytearray()
        last_error = None
        for attempt in range(config.retry.max_attempts):
            content = bytearray()
            try:
                async with self.client_factory(
                    timeout=config.timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(**request_kwargs) as response:
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > config.response_max_bytes:
                                raise ToolExecutionError(
                                    "tool_response_too_large",
                                    "Tool response exceeded the configured size limit",
                                )
                        status_code = response.status_code
                        content_type = response.headers.get("content-type", "")
                if status_code not in config.retry.retry_statuses:
                    break
                last_error = ToolExecutionError(
                    "tool_upstream_rejected",
                    f"Tool endpoint returned HTTP {status_code}",
                )
            except ToolExecutionError:
                raise
            except httpx.TimeoutException as exc:
                last_error = ToolExecutionError("tool_timeout", "Tool request timed out")
                last_error.__cause__ = exc
            except httpx.HTTPError as exc:
                last_error = ToolExecutionError(
                    "tool_unreachable",
                    "Tool endpoint is unreachable",
                )
                last_error.__cause__ = exc
            if attempt + 1 < config.retry.max_attempts:
                await asyncio.sleep(config.retry.backoff_seconds * (2**attempt))
        if last_error is not None and (
            status_code in config.retry.retry_statuses or status_code == 0
        ):
            raise last_error
        if not 200 <= status_code < 300:
            raise ToolExecutionError(
                "tool_upstream_rejected",
                f"Tool endpoint returned HTTP {status_code}",
            )
        try:
            if "json" in content_type.lower():
                return json.loads(content)
            return bytes(content).decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ToolExecutionError(
                "tool_output_invalid", "Tool returned an invalid response"
            ) from exc


class McpToolExecutor:
    def __init__(self, target_policy=None, client_factory=Client, http_client_factory=None):
        self.target_policy = target_policy or OutboundTargetPolicy()
        self.client_factory = client_factory
        self.http_client_factory = http_client_factory or httpx2.AsyncClient

    async def _connect(self, config: McpToolConfig, credential: str | None):
        await self.target_policy.validate(config.server_url, config.allowed_hosts)
        headers = credential_headers(config.auth, credential)
        http_client = self.http_client_factory(
            headers=headers,
            timeout=config.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        transport = streamable_http_client(config.server_url, http_client=http_client)
        return http_client, transport

    @staticmethod
    async def _list_tools(client) -> list:
        tools = []
        cursor = None
        for _ in range(100):
            page = await client.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if cursor is None:
                return tools
        raise ToolExecutionError(
            "mcp_protocol_error",
            "MCP tool discovery exceeded the pagination limit",
        )

    async def discover(self, config: McpToolConfig, credential: str | None) -> list[dict]:
        http_client, transport = await self._connect(config, credential)
        try:
            async with (
                http_client,
                self.client_factory(
                    transport,
                    read_timeout_seconds=config.timeout_seconds,
                ) as client,
            ):
                tools = await self._list_tools(client)
                return [
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.input_schema,
                        "output_schema": tool.output_schema,
                    }
                    for tool in tools
                ]
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(
                "mcp_connection_failed", "MCP server connection failed"
            ) from exc

    async def execute(
        self,
        config: McpToolConfig,
        arguments: dict,
        credential: str | None,
    ):
        http_client, transport = await self._connect(config, credential)
        try:
            async with (
                http_client,
                self.client_factory(
                    transport,
                    read_timeout_seconds=config.timeout_seconds,
                ) as client,
            ):
                available = await self._list_tools(client)
                if config.remote_tool_name not in {tool.name for tool in available}:
                    raise ToolExecutionError(
                        "mcp_tool_not_found",
                        "Configured tool was not found on the MCP server",
                    )
                result = await client.call_tool(
                    config.remote_tool_name,
                    arguments,
                    read_timeout_seconds=config.timeout_seconds,
                )
                if result.is_error:
                    raise ToolExecutionError("mcp_tool_failed", "MCP tool returned an error")
                if result.structured_content is not None:
                    return result.structured_content
                if len(result.content) == 1 and result.content[0].type == "text":
                    text = result.content[0].text
                    try:
                        return json.loads(text)
                    except ValueError:
                        return text
                return [block.model_dump(mode="json", exclude={"data"}) for block in result.content]
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(
                "mcp_connection_failed", "MCP server connection failed"
            ) from exc


class ToolRuntime:
    def __init__(self, http_executor=None, mcp_executor=None):
        self.http = http_executor or HttpToolExecutor()
        self.mcp = mcp_executor or McpToolExecutor()

    async def execute(self, definition: ToolDefinition, arguments: dict, credential: str | None):
        if definition.tool_type == "http":
            return await self.http.execute(definition.config, arguments, credential)
        return await self.mcp.execute(definition.config, arguments, credential)

    async def discover_mcp(self, definition: ToolDefinition, credential: str | None):
        if definition.tool_type != "mcp":
            raise ToolExecutionError("not_mcp_tool", "Tool is not backed by an MCP server")
        return await self.mcp.discover(definition.config, credential)


class ToolService:
    def __init__(self, session: Session, workspace_id: str, cipher=None, runtime=None):
        self.session = session
        self.workspace_id = workspace_id
        self.cipher = cipher or CredentialCipher.from_base64_key(None)
        self.runtime = runtime or ToolRuntime()

    def get(self, tool_id: UUID, *, lock=False):
        query = select(Tool).where(Tool.id == tool_id, Tool.workspace_id == self.workspace_id)
        if lock:
            query = query.with_for_update()
        tool = self.session.scalar(query)
        if tool is None:
            raise DomainError(404, "tool_not_found", "Tool not found")
        return tool

    @staticmethod
    def serialize(tool: Tool):
        return ToolRead.model_validate(
            {
                **tool.draft,
                "id": tool.id,
                "workspace_id": tool.workspace_id,
                "credential_configured": bool(tool.credential_ciphertext),
                "latest_version": tool.latest_version,
                "created_at": tool.created_at,
                "updated_at": tool.updated_at,
            }
        )

    @staticmethod
    def serialize_version(version: ToolVersion):
        return ToolVersionRead.model_validate(version, from_attributes=True)

    def commit(self):
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DomainError(409, "conflict", "Tool name or version already exists") from exc

    def create(self, data: ToolCreate):
        tool_id = uuid4()
        ciphertext = None
        if data.credential is not None:
            try:
                ciphertext = self.cipher.encrypt_tool(
                    tool_id,
                    data.credential.get_secret_value(),
                )
            except CredentialCipherUnavailable as exc:
                raise DomainError(
                    503,
                    "credential_encryption_unavailable",
                    "Credential encryption is not configured",
                ) from exc
        definition = ToolDefinition.model_validate(data.model_dump(exclude={"credential"}))
        tool = Tool(
            id=tool_id,
            workspace_id=self.workspace_id,
            name=definition.name,
            tool_type=definition.tool_type,
            draft=definition.model_dump(mode="json"),
            credential_ciphertext=ciphertext,
            enabled=definition.enabled,
        )
        self.session.add(tool)
        self.commit()
        return self.serialize(tool)

    def update(self, tool_id: UUID, data: ToolPatch):
        tool = self.get(tool_id, lock=True)
        draft = deepcopy(tool.draft)
        draft.update(data.model_dump(mode="json", exclude_unset=True, exclude={"credential"}))
        try:
            definition = ToolDefinition.model_validate(draft)
        except ValidationError as exc:
            raise DomainError(
                422,
                "validation_error",
                "Invalid tool configuration",
                exc.errors(include_context=False, include_input=False),
            ) from exc
        if "credential" in data.model_fields_set:
            try:
                tool.credential_ciphertext = self.cipher.encrypt_tool(
                    tool.id,
                    data.credential.get_secret_value(),
                )
            except CredentialCipherUnavailable as exc:
                raise DomainError(
                    503,
                    "credential_encryption_unavailable",
                    "Credential encryption is not configured",
                ) from exc
        tool.name = definition.name
        tool.tool_type = definition.tool_type
        tool.enabled = definition.enabled
        tool.draft = definition.model_dump(mode="json")
        tool.updated_at = utcnow()
        self.commit()
        return self.serialize(tool)

    def list(self, offset, limit, name=None, tool_type=None, enabled=None):
        filters = [Tool.workspace_id == self.workspace_id]
        if name is not None:
            filters.append(Tool.name.contains(name, autoescape=True))
        if tool_type is not None:
            filters.append(Tool.tool_type == tool_type)
        if enabled is not None:
            filters.append(Tool.enabled == enabled)
        total = self.session.scalar(select(func.count()).select_from(Tool).where(*filters))
        rows = self.session.scalars(
            select(Tool)
            .where(*filters)
            .order_by(Tool.created_at, Tool.id)
            .offset(offset)
            .limit(limit)
        )
        return ToolPage(
            items=[self.serialize(row) for row in rows],
            total=total,
            offset=offset,
            limit=limit,
        )

    def publish(self, tool_id: UUID):
        tool = self.get(tool_id, lock=True)
        definition = ToolDefinition.model_validate(tool.draft)
        if definition.config.auth.type != "none" and not tool.credential_ciphertext:
            raise DomainError(
                422,
                "publish_validation_failed",
                "Authenticated tool requires a credential",
                [{"field": "credential", "message": "Tool credential is required"}],
            )
        version = ToolVersion(
            tool_id=tool.id,
            workspace_id=self.workspace_id,
            version=(tool.latest_version or 0) + 1,
            snapshot=deepcopy(tool.draft),
        )
        self.session.add(version)
        tool.latest_version = version.version
        tool.updated_at = utcnow()
        self.commit()
        return self.serialize_version(version)

    def versions(self, tool_id: UUID, offset, limit):
        self.get(tool_id)
        filters = [
            ToolVersion.tool_id == tool_id,
            ToolVersion.workspace_id == self.workspace_id,
        ]
        total = self.session.scalar(select(func.count()).select_from(ToolVersion).where(*filters))
        rows = self.session.scalars(
            select(ToolVersion)
            .where(*filters)
            .order_by(ToolVersion.version.desc())
            .offset(offset)
            .limit(limit)
        )
        return ToolVersionPage(
            items=[self.serialize_version(row) for row in rows],
            total=total,
            offset=offset,
            limit=limit,
        )

    def version(self, tool_id: UUID, number: int):
        self.get(tool_id)
        version = self.session.scalar(
            select(ToolVersion).where(
                ToolVersion.tool_id == tool_id,
                ToolVersion.workspace_id == self.workspace_id,
                ToolVersion.version == number,
            )
        )
        if version is None:
            raise DomainError(404, "tool_version_not_found", "Tool version not found")
        return self.serialize_version(version)

    def resolve_credential(self, tool: Tool) -> str | None:
        if not tool.credential_ciphertext:
            return None
        try:
            return self.cipher.decrypt_tool(tool.id, tool.credential_ciphertext)
        except CredentialCipherUnavailable as exc:
            raise ToolExecutionError(
                "credential_encryption_unavailable",
                "Credential encryption is not configured",
            ) from exc
        except CredentialDecryptionError as exc:
            raise ToolExecutionError(
                "credential_decryption_failed",
                "Stored tool credential could not be decrypted",
            ) from exc

    async def test(self, tool_id: UUID, arguments: dict):
        tool = self.get(tool_id)
        definition = ToolDefinition.model_validate(tool.draft)
        started = perf_counter()
        tested_at = datetime.now(UTC)

        def result(success, code, message, output=None):
            return ToolTestResult(
                tool_id=tool.id,
                success=success,
                code=code,
                message=message,
                output=output,
                duration_ms=round((perf_counter() - started) * 1000, 3),
                tested_at=tested_at,
            )

        validation_errors = list(
            Draft202012Validator(definition.input_schema).iter_errors(arguments)
        )
        if validation_errors:
            return result(False, "tool_input_invalid", "Tool input does not match its schema")
        try:
            credential = self.resolve_credential(tool)
            output = await self.runtime.execute(
                definition,
                arguments,
                credential,
            )
            if definition.output_schema is not None:
                errors = list(Draft202012Validator(definition.output_schema).iter_errors(output))
                if errors:
                    raise ToolExecutionError(
                        "tool_output_invalid",
                        "Tool output does not match its schema",
                    )
            return result(True, "ok", "Tool call succeeded", redact_secret(output, credential))
        except ToolExecutionError as exc:
            return result(False, exc.code, exc.safe_message)

    async def discover_mcp(self, tool_id: UUID):
        tool = self.get(tool_id)
        definition = ToolDefinition.model_validate(tool.draft)
        try:
            return await self.runtime.discover_mcp(definition, self.resolve_credential(tool))
        except ToolExecutionError as exc:
            raise DomainError(422, exc.code, exc.safe_message) from exc
