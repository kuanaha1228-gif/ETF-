from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import app_data_dir
from .db import Database
from .providers.credentials import CredentialStore, LocalCredentialStore
from .providers.market import AkshareMarketProvider, AkshareTradingCalendar
from .providers.notify import (
    NotificationProvider,
    SmtpEmailNotifier,
    desktop_notifier,
)
from .service import CheckResult, DailyCheckService


SHANGHAI = ZoneInfo("Asia/Shanghai")


def setting_bool(database: Database, key: str, default: bool = False) -> bool:
    value = database.get_setting(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_notifiers(
    database: Database, credentials: CredentialStore | None = None
) -> list[NotificationProvider]:
    credentials = credentials or LocalCredentialStore()
    notifiers: list[NotificationProvider] = []
    if setting_bool(database, "notification.desktop.enabled", True):
        notifiers.append(desktop_notifier())
    if setting_bool(database, "notification.email.enabled"):
        password = credentials.get("smtp.password")
        required = {
            "smtp.host": database.get_setting("smtp.host", "") or "",
            "smtp.username": database.get_setting("smtp.username", "") or "",
            "smtp.sender": database.get_setting("smtp.sender", "") or "",
            "smtp.recipient": database.get_setting("smtp.recipient", "") or "",
        }
        if password and all(required.values()):
            notifiers.append(
                SmtpEmailNotifier(
                    host=required["smtp.host"],
                    port=int(database.get_setting("smtp.port", "465") or "465"),
                    username=required["smtp.username"],
                    password=password,
                    sender=required["smtp.sender"],
                    recipient=required["smtp.recipient"],
                    use_ssl=setting_bool(database, "smtp.use_ssl", True),
                )
            )
    return notifiers


def notification_configuration_errors(
    database: Database, credentials: CredentialStore | None = None
) -> tuple[str, ...]:
    credentials = credentials or LocalCredentialStore()
    errors: list[str] = []
    if setting_bool(database, "notification.email.enabled"):
        required = ("smtp.host", "smtp.username", "smtp.sender", "smtp.recipient")
        missing = [key for key in required if not database.get_setting(key)]
        if not credentials.get("smtp.password"):
            missing.append("smtp.password")
        if missing:
            errors.append("email needs configuration: " + ", ".join(missing))
    return tuple(errors)


@dataclass(slots=True)
class ProcessLock:
    path: Path
    stale_seconds: int = 600
    acquired: bool = False

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            age = datetime.now().timestamp() - self.path.stat().st_mtime
            try:
                owner = json.loads(self.path.read_text(encoding="utf-8"))
                pid = int(owner["pid"])
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                pid = 0
            running = False
            if pid:
                try:
                    os.kill(pid, 0)
                    running = True
                except ProcessLookupError:
                    pass
                except PermissionError:
                    running = True
            if running and age <= self.stale_seconds:
                raise RuntimeError("another daily check is already running")
            self.path.unlink(missing_ok=True)
        try:
            descriptor = self.path.open("x", encoding="utf-8")
        except FileExistsError as error:
            raise RuntimeError("another daily check is already running") from error
        descriptor.write(json.dumps({"pid": os.getpid(), "started_at": str(datetime.now().astimezone())}))
        descriptor.close()
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False


def in_execution_window(now: datetime) -> bool:
    local = now.astimezone(SHANGHAI)
    return time(14, 45) <= local.time().replace(tzinfo=None) <= time(15, 0)


def run_daily_check(
    database: Database,
    *,
    now: datetime | None = None,
    force: bool = False,
    credentials: CredentialStore | None = None,
) -> CheckResult:
    database.initialize()
    now = (now or datetime.now(tz=SHANGHAI)).astimezone(SHANGHAI)
    if not force and not in_execution_window(now):
        raise RuntimeError("MISSED_EXECUTION_WINDOW: scheduled checks must run between 14:45 and 15:00")
    lock_path = app_data_dir() / "run" / "daily_check.lock"
    with ProcessLock(lock_path):
        configuration_errors = notification_configuration_errors(database, credentials)
        service = DailyCheckService(
            database=database,
            market=AkshareMarketProvider(),
            calendar=AkshareTradingCalendar(),
            notifiers=build_notifiers(database, credentials),
        )
        result = service.run(now)
        return replace(result, errors=result.errors + configuration_errors)
