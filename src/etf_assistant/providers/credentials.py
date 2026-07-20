from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import app_data_dir


class CredentialStore(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class LocalCredentialStore:
    """Local credential file that never invokes an operating-system password dialog."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else app_data_dir() / "data" / "credentials.json"

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {str(key): str(value) for key, value in payload.items()}

    def _write(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, self.path)

    def get(self, key: str) -> str | None:
        return self._read().get(key)

    def set(self, key: str, value: str) -> None:
        values = self._read()
        values[key] = value
        self._write(values)

    def delete(self, key: str) -> None:
        values = self._read()
        if key in values:
            values.pop(key)
            self._write(values)


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
