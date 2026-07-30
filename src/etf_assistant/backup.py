from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from . import __version__
from .db import Database, SCHEMA_VERSION, SENSITIVE_SETTING_MARKERS


FORMAT_VERSION = 1
EXPECTED_FILES = {"manifest.json", "data.sqlite", "settings.json", "templates.json", "checksums.sha256", "README.txt"}


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_export_rows(database_path: Path, table: str) -> list[dict[str, object]]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]
        if table == "settings":
            rows = [
                row for row in rows
                if not any(marker in str(row.get("key", "")).lower() for marker in SENSITIVE_SETTING_MARKERS)
            ]
        return rows
    finally:
        connection.close()


def export_backup(database: Database, destination: str | Path) -> Path:
    database.initialize()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")

    with tempfile.TemporaryDirectory(prefix="etf-export-") as temp_name:
        temp = Path(temp_name)
        snapshot = temp / "data.sqlite"
        source = database.connect()
        target = sqlite3.connect(snapshot)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

        settings = _read_export_rows(snapshot, "settings")
        templates = _read_export_rows(snapshot, "message_templates")
        (temp / "settings.json").write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (temp / "templates.json").write_text(
            json.dumps(templates, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (temp / "README.txt").write_text(
            "ETF Plan Assistant migration package. Credentials are intentionally excluded.\n"
            "Reconfigure the SMTP credential after import.\n",
            encoding="utf-8",
        )
        manifest = {
            "format_version": FORMAT_VERSION,
            "app_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_os": platform.system(),
            "timezone": "Asia/Shanghai",
            "contains_secrets": False,
        }
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        checksum_targets = ["manifest.json", "data.sqlite", "settings.json", "templates.json", "README.txt"]
        checksum_lines = [f"{_sha256(temp / name)}  {name}" for name in checksum_targets]
        (temp / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

        try:
            with zipfile.ZipFile(temporary_output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name in sorted(EXPECTED_FILES):
                    archive.write(temp / name, arcname=name)
            validate_backup(temporary_output)
            os.replace(temporary_output, destination)
        finally:
            temporary_output.unlink(missing_ok=True)
    return destination


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    for member in archive.infolist():
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise BackupError("backup contains an unsafe path")
        if member.is_dir():
            continue
        target = destination.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def _parse_checksums(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or not digest or not name:
            raise BackupError("invalid checksums file")
        result[name] = digest
    return result


def validate_backup(path: str | Path) -> dict[str, object]:
    path = Path(path)
    if not path.is_file():
        raise BackupError("backup file does not exist")
    with tempfile.TemporaryDirectory(prefix="etf-validate-") as temp_name:
        temp = Path(temp_name)
        try:
            with zipfile.ZipFile(path, "r") as archive:
                names = {item.filename for item in archive.infolist() if not item.is_dir()}
                if names != EXPECTED_FILES:
                    raise BackupError("backup file list is incomplete or unexpected")
                _safe_extract(archive, temp)
        except zipfile.BadZipFile as error:
            raise BackupError("backup is not a valid migration package") from error

        checksums = _parse_checksums(temp / "checksums.sha256")
        if set(checksums) != EXPECTED_FILES - {"checksums.sha256"}:
            raise BackupError("checksum file list does not match package")
        for name, expected in checksums.items():
            if _sha256(temp / name) != expected:
                raise BackupError(f"checksum mismatch: {name}")

        manifest = json.loads((temp / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format_version") != FORMAT_VERSION:
            raise BackupError("unsupported backup format version")
        if manifest.get("schema_version", 0) > SCHEMA_VERSION:
            raise BackupError("backup database schema is newer than this application")
        if manifest.get("contains_secrets") is not False:
            raise BackupError("backup must not contain secrets")

        connection = sqlite3.connect(temp / "data.sqlite")
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("backup database failed integrity check")
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if schema_version > SCHEMA_VERSION:
                raise BackupError("backup database schema is unsupported")
        finally:
            connection.close()
        return manifest


def _extract_database(backup_path: Path, destination: Path) -> dict[str, object]:
    manifest = validate_backup(backup_path)
    with zipfile.ZipFile(backup_path, "r") as archive, archive.open("data.sqlite") as source:
        with destination.open("wb") as output:
            shutil.copyfileobj(source, output)
    return manifest


def restore_backup(backup_path: str | Path, target: Database) -> Path | None:
    backup_path = Path(backup_path)
    target.path.parent.mkdir(parents=True, exist_ok=True)
    rollback_path: Path | None = None
    with tempfile.TemporaryDirectory(prefix="etf-restore-", dir=target.path.parent) as temp_name:
        incoming = Path(temp_name) / "incoming.sqlite"
        _extract_database(backup_path, incoming)
        incoming_db = Database(incoming)
        incoming_db.initialize()
        if not incoming_db.integrity_check():
            raise BackupError("restored database failed integrity check")

        if target.path.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            rollback_path = target.path.with_name(f"{target.path.stem}.pre_import_{stamp}.sqlite")
            shutil.copy2(target.path, rollback_path)
        replacement = target.path.with_name(f".{target.path.name}.{uuid4().hex}.incoming")
        try:
            shutil.copy2(incoming, replacement)
            os.replace(replacement, target.path)
        finally:
            replacement.unlink(missing_ok=True)
    return rollback_path


MERGE_TABLES = (
    "plans", "plan_configuration_versions", "strategy_versions",
    "strategy_levels", "drawdown_cycles",
    "take_profit_levels", "recovery_levels", "take_profit_cycles",
    "fund_nav_records", "events",
    "take_profit_executions", "recovery_executions",
    "notification_deliveries", "check_runs", "market_snapshots", "settings",
    "message_templates", "daily_summaries", "audit_log",
)


def merge_backup(backup_path: str | Path, target: Database, conflict: str = "local") -> None:
    if conflict not in {"local", "import"}:
        raise ValueError("conflict must be 'local' or 'import'")
    target.initialize()
    with tempfile.TemporaryDirectory(prefix="etf-merge-") as temp_name:
        incoming = Path(temp_name) / "incoming.sqlite"
        _extract_database(Path(backup_path), incoming)
        incoming_db = Database(incoming)
        incoming_db.initialize()
        connection = target.connect()
        try:
            connection.execute("ATTACH DATABASE ? AS incoming", (str(incoming),))
            try:
                connection.execute("BEGIN IMMEDIATE")
                for table in MERGE_TABLES:
                    columns = [
                        row[1]
                        for row in connection.execute(f"PRAGMA main.table_info({table})").fetchall()
                    ]
                    names = ", ".join(f'"{column}"' for column in columns)
                    connection.execute(
                        f"INSERT OR IGNORE INTO main.{table} ({names}) "
                        f"SELECT {names} FROM incoming.{table}"
                    )
                if conflict == "import":
                    plan_columns = [
                        "name", "purchase_code", "purchase_name", "signal_code", "signal_name",
                        "base_amount", "current_amount", "invest_weekday",
                        "recurring_enabled", "drawdown_enabled", "enabled", "status",
                        "active_strategy_id", "pending_strategy_id", "updated_at",
                    ]
                    assignments = ", ".join(
                        f'"{column}" = (SELECT i."{column}" FROM incoming.plans i WHERE i.id = plans.id)'
                        for column in plan_columns
                    )
                    connection.execute(
                        f"UPDATE plans SET {assignments} WHERE id IN (SELECT id FROM incoming.plans)"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.execute("DETACH DATABASE incoming")
        finally:
            connection.close()
