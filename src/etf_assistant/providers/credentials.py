from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


SERVICE_NAME = "ETFPlanAssistant"


class CredentialStore(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class KeyringCredentialStore:
    def __init__(self) -> None:
        try:
            import keyring
        except ImportError as error:
            raise RuntimeError("Install the 'desktop' extra to use the system credential store") from error
        self.keyring = keyring

    def get(self, key: str) -> str | None:
        return self.keyring.get_password(SERVICE_NAME, key)

    def set(self, key: str, value: str) -> None:
        self.keyring.set_password(SERVICE_NAME, key, value)

    def delete(self, key: str) -> None:
        try:
            self.keyring.delete_password(SERVICE_NAME, key)
        except self.keyring.errors.PasswordDeleteError:
            pass


@dataclass(slots=True)
class MemoryCredentialStore:
    values: dict[str, str]

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class EnvironmentCredentialStore:
    """Headless fallback for CI; environment variables are never exported to backups."""

    PREFIX = "ETF_ASSISTANT_SECRET_"

    def get(self, key: str) -> str | None:
        return os.environ.get(self.PREFIX + key.upper().replace(".", "_"))

    def set(self, key: str, value: str) -> None:
        raise RuntimeError("environment credential store is read-only")

    def delete(self, key: str) -> None:
        raise RuntimeError("environment credential store is read-only")

