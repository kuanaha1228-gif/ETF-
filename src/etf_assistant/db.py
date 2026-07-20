from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator
from uuid import UUID, uuid4

from .domain import (
    DeliveryState,
    EffectiveMode,
    EventState,
    ExecutionMode,
    ExecutionStatus,
    Plan,
    Strategy,
    StrategyLevel,
)


SCHEMA_VERSION = 1
SENSITIVE_SETTING_MARKERS = ("password", "secret", "token", "webhook", "authorization")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    purchase_code TEXT NOT NULL,
    purchase_name TEXT NOT NULL DEFAULT '',
    signal_code TEXT NOT NULL,
    signal_name TEXT NOT NULL DEFAULT '',
    base_amount TEXT NOT NULL,
    invest_weekday INTEGER NOT NULL,
    recurring_enabled INTEGER NOT NULL,
    drawdown_enabled INTEGER NOT NULL,
    enabled INTEGER NOT NULL,
    active_strategy_id TEXT,
    pending_strategy_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    version INTEGER NOT NULL,
    effective_mode TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(plan_id, version)
);

CREATE TABLE IF NOT EXISTS strategy_levels (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL REFERENCES strategy_versions(id) ON DELETE CASCADE,
    threshold TEXT NOT NULL,
    multiplier TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    phases INTEGER NOT NULL,
    interval_weeks INTEGER NOT NULL,
    enabled INTEGER NOT NULL,
    amount_cap TEXT,
    note TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL,
    UNIQUE(strategy_id, threshold)
);

CREATE TABLE IF NOT EXISTS drawdown_cycles (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    strategy_id TEXT NOT NULL REFERENCES strategy_versions(id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    recovery_checks INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'active'
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_cycle_per_plan
ON drawdown_cycles(plan_id) WHERE state = 'active';

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    cycle_id TEXT REFERENCES drawdown_cycles(id),
    strategy_id TEXT NOT NULL REFERENCES strategy_versions(id),
    level_id TEXT REFERENCES strategy_levels(id),
    event_type TEXT NOT NULL,
    trading_date TEXT NOT NULL,
    week_key TEXT NOT NULL,
    quote_price TEXT NOT NULL,
    quote_time TEXT NOT NULL,
    daily_change TEXT,
    highest_close TEXT NOT NULL,
    drawdown TEXT NOT NULL,
    threshold_snapshot TEXT,
    multiplier_snapshot TEXT,
    execution_mode_snapshot TEXT,
    regular_amount TEXT NOT NULL,
    extra_amount TEXT NOT NULL,
    total_amount TEXT NOT NULL,
    state TEXT NOT NULL,
    execution_status TEXT NOT NULL,
    executed_amount TEXT,
    executed_at TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    state TEXT NOT NULL,
    attempted_at TEXT,
    sent_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT,
    UNIQUE(event_id, channel)
);

CREATE TABLE IF NOT EXISTS check_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    trading_date TEXT,
    state TEXT NOT NULL,
    error_summary TEXT,
    checked_plans INTEGER NOT NULL DEFAULT 0,
    created_events INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    channel TEXT NOT NULL,
    event_type TEXT NOT NULL,
    subject_template TEXT NOT NULL,
    body_template TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(name, channel, event_type)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


def utc_now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.transaction() as connection:
            current = connection.execute("PRAGMA user_version").fetchone()[0]
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {current} is newer than supported {SCHEMA_VERSION}"
                )
            if current == 0:
                connection.executescript(SCHEMA_SQL)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def integrity_check(self) -> bool:
        with self.read() as connection:
            return connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def add_plan_with_strategy(self, plan: Plan, strategy: Strategy) -> None:
        plan.validate()
        strategy.validate()
        if strategy.plan_id != plan.id:
            raise ValueError("strategy.plan_id must match plan.id")
        now = utc_now_text()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO plans (
                    id, name, purchase_code, purchase_name, signal_code, signal_name,
                    base_amount, invest_weekday, recurring_enabled, drawdown_enabled,
                    enabled, active_strategy_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(plan.id), plan.name, plan.purchase_code, plan.purchase_name,
                    plan.signal_code, plan.signal_name, str(plan.base_amount),
                    plan.invest_weekday, int(plan.recurring_enabled),
                    int(plan.drawdown_enabled), int(plan.enabled), str(strategy.id), now, now,
                ),
            )
            self._insert_strategy(connection, strategy)

    def _insert_strategy(self, connection: sqlite3.Connection, strategy: Strategy) -> None:
        connection.execute(
            """
            INSERT INTO strategy_versions (id, plan_id, version, effective_mode, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(strategy.id), str(strategy.plan_id), strategy.version,
                strategy.effective_mode.value, strategy.created_at.astimezone().isoformat(),
            ),
        )
        for position, level in enumerate(sorted(strategy.levels, key=lambda item: item.threshold)):
            connection.execute(
                """
                INSERT INTO strategy_levels (
                    id, strategy_id, threshold, multiplier, execution_mode, phases,
                    interval_weeks, enabled, amount_cap, note, position
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(level.id), str(strategy.id), str(level.threshold), str(level.multiplier),
                    level.execution_mode.value, level.phases, level.interval_weeks,
                    int(level.enabled), str(level.amount_cap) if level.amount_cap else None,
                    level.note, position,
                ),
            )

    def save_strategy(self, strategy: Strategy) -> None:
        strategy.validate()
        with self.transaction() as connection:
            plan = connection.execute(
                "SELECT active_strategy_id FROM plans WHERE id = ?", (str(strategy.plan_id),)
            ).fetchone()
            if plan is None:
                raise KeyError("plan not found")
            self._insert_strategy(connection, strategy)
            column = (
                "active_strategy_id"
                if strategy.effective_mode is EffectiveMode.IMMEDIATE
                else "pending_strategy_id"
            )
            connection.execute(
                f"UPDATE plans SET {column} = ?, updated_at = ? WHERE id = ?",
                (str(strategy.id), utc_now_text(), str(strategy.plan_id)),
            )

    def list_plans(self) -> list[Plan]:
        with self.read() as connection:
            rows = connection.execute("SELECT * FROM plans ORDER BY created_at").fetchall()
        return [self._row_to_plan(row) for row in rows]

    def get_plan(self, plan_id: UUID) -> Plan:
        with self.read() as connection:
            row = connection.execute("SELECT * FROM plans WHERE id = ?", (str(plan_id),)).fetchone()
        if row is None:
            raise KeyError("plan not found")
        return self._row_to_plan(row)

    def update_plan(self, plan: Plan) -> None:
        plan.validate()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE plans SET
                    name = ?, purchase_code = ?, purchase_name = ?,
                    signal_code = ?, signal_name = ?, base_amount = ?,
                    invest_weekday = ?, recurring_enabled = ?,
                    drawdown_enabled = ?, enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    plan.name, plan.purchase_code, plan.purchase_name,
                    plan.signal_code, plan.signal_name, str(plan.base_amount),
                    plan.invest_weekday, int(plan.recurring_enabled),
                    int(plan.drawdown_enabled), int(plan.enabled),
                    utc_now_text(), str(plan.id),
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError("plan not found")

    def recent_events(self, limit: int = 100) -> list[sqlite3.Row]:
        if limit < 1:
            return []
        with self.read() as connection:
            return connection.execute(
                """
                SELECT e.*, p.name AS plan_name, p.purchase_code, p.signal_code
                FROM events e
                JOIN plans p ON p.id = e.plan_id
                ORDER BY e.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def get_active_strategy(self, plan_id: UUID) -> Strategy:
        with self.read() as connection:
            row = connection.execute(
                """
                SELECT s.* FROM strategy_versions s
                JOIN plans p ON p.active_strategy_id = s.id
                WHERE p.id = ?
                """,
                (str(plan_id),),
            ).fetchone()
            if row is None:
                raise KeyError("active strategy not found")
            levels = connection.execute(
                "SELECT * FROM strategy_levels WHERE strategy_id = ? ORDER BY position",
                (row["id"],),
            ).fetchall()
        return self._rows_to_strategy(row, levels)

    def count_rows(self) -> dict[str, int]:
        tables = (
            "plans", "strategy_versions", "strategy_levels", "drawdown_cycles",
            "events", "notification_deliveries", "check_runs", "settings",
            "message_templates", "audit_log",
        )
        with self.read() as connection:
            return {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }

    def set_setting(self, key: str, value: str) -> None:
        lowered = key.lower()
        if any(marker in lowered for marker in SENSITIVE_SETTING_MARKERS):
            raise ValueError("sensitive values must be stored in the operating-system credential store")
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, utc_now_text()),
            )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.read() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    @staticmethod
    def _row_to_plan(row: sqlite3.Row) -> Plan:
        return Plan(
            id=UUID(row["id"]), name=row["name"], purchase_code=row["purchase_code"],
            purchase_name=row["purchase_name"], signal_code=row["signal_code"],
            signal_name=row["signal_name"], base_amount=Decimal(row["base_amount"]),
            invest_weekday=row["invest_weekday"], recurring_enabled=bool(row["recurring_enabled"]),
            drawdown_enabled=bool(row["drawdown_enabled"]), enabled=bool(row["enabled"]),
        )

    @staticmethod
    def _rows_to_strategy(row: sqlite3.Row, levels: list[sqlite3.Row]) -> Strategy:
        return Strategy(
            id=UUID(row["id"]), plan_id=UUID(row["plan_id"]), version=row["version"],
            effective_mode=EffectiveMode(row["effective_mode"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            levels=tuple(
                StrategyLevel(
                    id=UUID(level["id"]), threshold=Decimal(level["threshold"]),
                    multiplier=Decimal(level["multiplier"]),
                    execution_mode=ExecutionMode(level["execution_mode"]),
                    phases=level["phases"], interval_weeks=level["interval_weeks"],
                    enabled=bool(level["enabled"]),
                    amount_cap=Decimal(level["amount_cap"]) if level["amount_cap"] else None,
                    note=level["note"],
                )
                for level in levels
            ),
        )

    def create_event(
        self,
        *,
        plan_id: UUID,
        strategy_id: UUID,
        level_id: UUID | None,
        event_type: str,
        trading_date: str,
        week_key: str,
        quote_price: Decimal,
        quote_time: datetime,
        daily_change: Decimal | None,
        highest_close: Decimal,
        drawdown: Decimal,
        threshold_snapshot: Decimal | None,
        multiplier_snapshot: Decimal | None,
        execution_mode_snapshot: str | None,
        regular_amount: Decimal,
        extra_amount: Decimal,
        idempotency_key: str,
        state: EventState = EventState.CREATED,
        cycle_id: UUID | None = None,
    ) -> tuple[UUID, bool]:
        event_id = uuid4()
        now = utc_now_text()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM events WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing:
                return UUID(existing["id"]), False
            connection.execute(
                """
                INSERT INTO events (
                    id, plan_id, cycle_id, strategy_id, level_id, event_type,
                    trading_date, week_key, quote_price, quote_time, daily_change,
                    highest_close, drawdown, threshold_snapshot, multiplier_snapshot,
                    execution_mode_snapshot, regular_amount, extra_amount, total_amount,
                    state, execution_status, idempotency_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event_id), str(plan_id), str(cycle_id) if cycle_id else None,
                    str(strategy_id), str(level_id) if level_id else None, event_type,
                    trading_date, week_key, str(quote_price), quote_time.isoformat(),
                    str(daily_change) if daily_change is not None else None,
                    str(highest_close), str(drawdown),
                    str(threshold_snapshot) if threshold_snapshot is not None else None,
                    str(multiplier_snapshot) if multiplier_snapshot is not None else None,
                    execution_mode_snapshot, str(regular_amount), str(extra_amount),
                    str(regular_amount + extra_amount), state.value,
                    ExecutionStatus.UNKNOWN.value, idempotency_key, now, now,
                ),
            )
        return event_id, True

    def record_delivery(
        self,
        *,
        event_id: UUID,
        channel: str,
        state: DeliveryState,
        error_summary: str | None = None,
    ) -> None:
        now = utc_now_text()
        sent_at = now if state is DeliveryState.SENT else None
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO notification_deliveries (
                    id, event_id, channel, state, attempted_at, sent_at, attempts, error_summary
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(event_id, channel) DO UPDATE SET
                    state = excluded.state,
                    attempted_at = excluded.attempted_at,
                    sent_at = COALESCE(excluded.sent_at, notification_deliveries.sent_at),
                    attempts = notification_deliveries.attempts + 1,
                    error_summary = excluded.error_summary
                """,
                (str(uuid4()), str(event_id), channel, state.value, now, sent_at, error_summary),
            )
            if state is DeliveryState.SENT:
                connection.execute(
                    "UPDATE events SET state = ?, updated_at = ? WHERE id = ?",
                    (EventState.NOTIFIED.value, now, str(event_id)),
                )

    def get_or_create_active_cycle(self, plan_id: UUID, strategy_id: UUID) -> UUID:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT id FROM drawdown_cycles WHERE plan_id = ? AND state = 'active'",
                (str(plan_id),),
            ).fetchone()
            if row:
                return UUID(row["id"])
            cycle_id = uuid4()
            connection.execute(
                """
                INSERT INTO drawdown_cycles (id, plan_id, strategy_id, started_at, state)
                VALUES (?, ?, ?, ?, 'active')
                """,
                (str(cycle_id), str(plan_id), str(strategy_id), utc_now_text()),
            )
            return cycle_id

    def get_active_cycle(self, plan_id: UUID) -> sqlite3.Row | None:
        with self.read() as connection:
            return connection.execute(
                "SELECT * FROM drawdown_cycles WHERE plan_id = ? AND state = 'active'",
                (str(plan_id),),
            ).fetchone()

    def update_recovery(self, plan_id: UUID, recovered: bool) -> bool:
        """Return True when a cycle ends after two consecutive recovery checks."""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM drawdown_cycles WHERE plan_id = ? AND state = 'active'",
                (str(plan_id),),
            ).fetchone()
            if row is None:
                return False
            checks = row["recovery_checks"] + 1 if recovered else 0
            if checks < 2:
                connection.execute(
                    "UPDATE drawdown_cycles SET recovery_checks = ? WHERE id = ?",
                    (checks, row["id"]),
                )
                return False
            now = utc_now_text()
            connection.execute(
                """
                UPDATE drawdown_cycles
                SET state = 'ended', ended_at = ?, recovery_checks = ?
                WHERE id = ?
                """,
                (now, checks, row["id"]),
            )
            plan = connection.execute(
                "SELECT pending_strategy_id FROM plans WHERE id = ?", (str(plan_id),)
            ).fetchone()
            if plan and plan["pending_strategy_id"]:
                connection.execute(
                    """
                    UPDATE plans
                    SET active_strategy_id = pending_strategy_id,
                        pending_strategy_id = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, str(plan_id)),
                )
            return True

    def notified_this_week(self, plan_id: UUID, week_key: str) -> bool:
        with self.read() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM events
                WHERE plan_id = ? AND week_key = ?
                  AND state IN (?, ?, ?)
                  AND CAST(extra_amount AS NUMERIC) > 0
                LIMIT 1
                """,
                (
                    str(plan_id),
                    week_key,
                    EventState.NOTIFIED.value,
                    EventState.IGNORED.value,
                    EventState.POSTPONED.value,
                ),
            ).fetchone()
            return row is not None

    def notified_level_events(self, cycle_id: UUID, level_id: UUID) -> list[sqlite3.Row]:
        with self.read() as connection:
            return connection.execute(
                """
                SELECT * FROM events
                WHERE cycle_id = ? AND level_id = ? AND state IN (?, ?, ?)
                ORDER BY trading_date, created_at
                """,
                (
                    str(cycle_id),
                    str(level_id),
                    EventState.NOTIFIED.value,
                    EventState.IGNORED.value,
                    EventState.POSTPONED.value,
                ),
            ).fetchall()

    def notified_threshold_events(
        self, cycle_id: UUID, threshold: Decimal
    ) -> list[sqlite3.Row]:
        """Return successful events by threshold across strategy versions."""
        with self.read() as connection:
            return connection.execute(
                """
                SELECT * FROM events
                WHERE cycle_id = ? AND threshold_snapshot = ? AND state IN (?, ?, ?)
                ORDER BY trading_date, created_at
                """,
                (
                    str(cycle_id),
                    str(threshold),
                    EventState.NOTIFIED.value,
                    EventState.IGNORED.value,
                    EventState.POSTPONED.value,
                ),
            ).fetchall()

    def highest_notified_threshold(self, cycle_id: UUID) -> Decimal | None:
        with self.read() as connection:
            rows = connection.execute(
                """
                SELECT threshold_snapshot FROM events
                WHERE cycle_id = ? AND state IN (?, ?, ?)
                  AND threshold_snapshot IS NOT NULL
                """,
                (
                    str(cycle_id),
                    EventState.NOTIFIED.value,
                    EventState.IGNORED.value,
                    EventState.POSTPONED.value,
                ),
            ).fetchall()
        values = [Decimal(row["threshold_snapshot"]) for row in rows]
        return max(values) if values else None

    def delivery_states(self, event_id: UUID) -> dict[str, DeliveryState]:
        with self.read() as connection:
            rows = connection.execute(
                "SELECT channel, state FROM notification_deliveries WHERE event_id = ?",
                (str(event_id),),
            ).fetchall()
        return {row["channel"]: DeliveryState(row["state"]) for row in rows}

    def event_row(self, event_id: UUID) -> sqlite3.Row:
        with self.read() as connection:
            row = connection.execute("SELECT * FROM events WHERE id = ?", (str(event_id),)).fetchone()
        if row is None:
            raise KeyError("event not found")
        return row

    def mark_event_delivery_failed(self, event_id: UUID) -> None:
        with self.transaction() as connection:
            sent = connection.execute(
                """
                SELECT 1 FROM notification_deliveries
                WHERE event_id = ? AND state = ? LIMIT 1
                """,
                (str(event_id), DeliveryState.SENT.value),
            ).fetchone()
            if sent is None:
                connection.execute(
                    "UPDATE events SET state = ?, updated_at = ? WHERE id = ?",
                    (EventState.DELIVERY_FAILED.value, utc_now_text(), str(event_id)),
                )

    def update_event_action(
        self,
        event_id: UUID,
        *,
        state: EventState | None = None,
        execution_status: ExecutionStatus | None = None,
        executed_amount: Decimal | None = None,
        executed_at: datetime | None = None,
    ) -> None:
        """Record a user action without changing the immutable event snapshot."""
        if state is not None and state not in {
            EventState.IGNORED,
            EventState.POSTPONED,
        }:
            raise ValueError("user action state must be ignored or postponed")
        if execution_status is None and state is None:
            raise ValueError("an event action is required")
        if executed_amount is not None and executed_amount < 0:
            raise ValueError("executed_amount cannot be negative")
        if execution_status is not ExecutionStatus.EXECUTED and executed_amount is not None:
            raise ValueError("executed_amount is only valid for executed events")

        now = utc_now_text()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT state, execution_status FROM events WHERE id = ?",
                (str(event_id),),
            ).fetchone()
            if row is None:
                raise KeyError("event not found")
            next_state = state.value if state is not None else row["state"]
            next_execution = (
                execution_status.value
                if execution_status is not None
                else row["execution_status"]
            )
            execution_time = (
                (executed_at or datetime.now().astimezone()).isoformat(timespec="seconds")
                if execution_status is ExecutionStatus.EXECUTED
                else None
            )
            connection.execute(
                """
                UPDATE events
                SET state = ?, execution_status = ?, executed_amount = ?,
                    executed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_state,
                    next_execution,
                    str(executed_amount) if executed_amount is not None else None,
                    execution_time,
                    now,
                    str(event_id),
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log (id, action, entity_type, entity_id, details_json, created_at)
                VALUES (?, ?, 'event', ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    "event_user_action",
                    str(event_id),
                    json.dumps(
                        {
                            "state": next_state,
                            "execution_status": next_execution,
                            "executed_amount": (
                                str(executed_amount) if executed_amount is not None else None
                            ),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )
