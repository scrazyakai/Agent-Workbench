import os
from copy import deepcopy
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID

import httpx
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
from app.db.models import ModelConnection, utcnow
from app.schemas.model_connections import (
    ConnectionTestResult,
    ModelConnectionConfig,
    ModelConnectionCreate,
    ModelConnectionPatch,
    ModelConnectionRead,
)


class OpenAICompatibleConnectionTester:
    def __init__(self, client_factory=httpx.Client, cipher: CredentialCipher | None = None):
        self.client_factory = client_factory
        self.cipher = cipher or CredentialCipher.from_base64_key(None)

    def resolve_secret(self, connection: ModelConnection) -> str | None:
        if connection.credential_ciphertext:
            return self.cipher.decrypt(connection.id, connection.credential_ciphertext)
        if connection.credential_ref and connection.credential_ref.startswith("env://"):
            variable = connection.credential_ref.removeprefix("env://")
            return os.environ.get(variable) if variable else None
        return None

    def test(self, connection: ModelConnection) -> ConnectionTestResult:
        started = perf_counter()
        tested_at = datetime.now(UTC)

        def result(success: bool, code: str, message: str):
            return ConnectionTestResult(
                connection_id=connection.id,
                success=success,
                code=code,
                message=message,
                latency_ms=round((perf_counter() - started) * 1000, 3),
                tested_at=tested_at,
            )

        try:
            secret = self.resolve_secret(connection)
        except CredentialCipherUnavailable:
            return result(
                False,
                "credential_encryption_unavailable",
                "Credential encryption is not configured",
            )
        except CredentialDecryptionError:
            return result(
                False,
                "credential_decryption_failed",
                "Stored credential could not be decrypted",
            )
        if not secret:
            return result(False, "credential_not_found", "Credential is not available")

        try:
            with self.client_factory(
                timeout=connection.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = client.get(
                    f"{connection.base_url}/models",
                    headers={"Authorization": f"Bearer {secret}"},
                )
        except httpx.HTTPError:
            return result(False, "provider_unreachable", "Model provider is unreachable")

        if response.status_code in {401, 403}:
            return result(False, "authentication_failed", "Model provider rejected credentials")
        if not 200 <= response.status_code < 300:
            return result(False, "invalid_response", "Model provider returned an invalid response")
        try:
            payload = response.json()
            models = payload["data"]
            if not isinstance(models, list):
                raise TypeError
            model_ids = {
                item["id"]
                for item in models
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
        except (ValueError, KeyError, TypeError):
            return result(False, "invalid_response", "Model provider returned an invalid response")
        if connection.model_name not in model_ids:
            return result(False, "model_not_found", "Configured model was not found")
        return result(True, "ok", "Connection succeeded")


class ModelConnectionService:
    def __init__(self, session: Session, workspace_id: str, tester=None, cipher=None):
        self.session = session
        self.workspace_id = workspace_id
        self.tester = tester or OpenAICompatibleConnectionTester()
        self.cipher = cipher or CredentialCipher.from_base64_key(None)

    def get(self, connection_id: UUID, *, lock=False):
        query = select(ModelConnection).where(
            ModelConnection.id == connection_id,
            ModelConnection.workspace_id == self.workspace_id,
        )
        if lock:
            query = query.with_for_update()
        connection = self.session.scalar(query)
        if connection is None:
            raise DomainError(404, "model_connection_not_found", "Model connection not found")
        return connection

    @staticmethod
    def serialize(connection):
        return ModelConnectionRead.model_validate(
            {
                "id": connection.id,
                "workspace_id": connection.workspace_id,
                "name": connection.name,
                "provider": connection.provider,
                "model_name": connection.model_name,
                "base_url": connection.base_url,
                "timeout_seconds": connection.timeout_seconds,
                "enabled": connection.enabled,
                "credential_configured": bool(
                    connection.credential_ciphertext or connection.credential_ref
                ),
                "created_at": connection.created_at,
                "updated_at": connection.updated_at,
            }
        )

    def commit(self):
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DomainError(409, "conflict", "Model connection name already exists") from exc

    def create(self, data: ModelConnectionCreate):
        connection_id = UUID(bytes=os.urandom(16), version=4)
        try:
            encrypted_key = self.cipher.encrypt(connection_id, data.api_key.get_secret_value())
        except CredentialCipherUnavailable as exc:
            raise DomainError(
                503,
                "credential_encryption_unavailable",
                "Credential encryption is not configured",
            ) from exc
        connection = ModelConnection(
            id=connection_id,
            workspace_id=self.workspace_id,
            **data.model_dump(mode="json", exclude={"api_key"}),
            credential_ref=None,
            credential_ciphertext=encrypted_key,
        )
        self.session.add(connection)
        self.commit()
        return self.serialize(connection)

    def update(self, connection_id: UUID, data: ModelConnectionPatch):
        connection = self.get(connection_id, lock=True)
        values = {field: getattr(connection, field) for field in ModelConnectionConfig.model_fields}
        patch = data.model_dump(mode="json", exclude_unset=True, exclude={"api_key"})
        values.update(patch)
        try:
            validated = ModelConnectionConfig.model_validate(deepcopy(values))
        except ValidationError as exc:
            raise DomainError(
                422,
                "validation_error",
                "Invalid model connection",
                exc.errors(include_context=False, include_input=False),
            ) from exc
        for field, value in validated.model_dump(mode="json").items():
            setattr(connection, field, value)
        if "api_key" in data.model_fields_set:
            try:
                connection.credential_ciphertext = self.cipher.encrypt(
                    connection.id, data.api_key.get_secret_value()
                )
            except CredentialCipherUnavailable as exc:
                raise DomainError(
                    503,
                    "credential_encryption_unavailable",
                    "Credential encryption is not configured",
                ) from exc
            connection.credential_ref = None
        connection.updated_at = utcnow()
        self.commit()
        return self.serialize(connection)

    def list(self, offset, limit, name=None, enabled=None):
        filters = [ModelConnection.workspace_id == self.workspace_id]
        if name is not None:
            filters.append(ModelConnection.name.contains(name, autoescape=True))
        if enabled is not None:
            filters.append(ModelConnection.enabled == enabled)
        total = self.session.scalar(
            select(func.count()).select_from(ModelConnection).where(*filters)
        )
        rows = self.session.scalars(
            select(ModelConnection)
            .where(*filters)
            .order_by(ModelConnection.created_at, ModelConnection.id)
            .offset(offset)
            .limit(limit)
        )
        return {
            "items": [self.serialize(row) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def test(self, connection_id: UUID):
        return self.tester.test(self.get(connection_id))
