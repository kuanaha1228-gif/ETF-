from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator
from uuid import UUID, uuid4

from .domain import (
    DecisionPeakType,
    DeliveryState,
    EffectiveMode,
    EventState,
    ExecutionMode,
    ExecutionStatus,
    Plan,
    PlanStatus,
    RecoveryLevel,
    Strategy,
    StrategyLevel,
    TakeProfitBasisStatus,
    TakeProfitLevel,
    ValuationStatus,
    ValuationType,
)


SCHEMA_VERSION = 7
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
    current_amount TEXT NOT NULL,
    invest_weekday INTEGER NOT NULL,
    recurring_enabled INTEGER NOT NULL,
    drawdown_enabled INTEGER NOT NULL,
    enabled INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
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

CREATE TABLE IF NOT EXISTS plan_configuration_versions (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    version INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
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
    max_executions INTEGER,
    cycle_amount_cap TEXT,
    cycle_multiplier_cap TEXT,
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
    recovery_last_trading_date TEXT,
    cycle_peak TEXT,
    budget_started_at TEXT,
    paused_for_review INTEGER NOT NULL DEFAULT 0,
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
    rolling_20d_high_date TEXT,
    decision_peak_type TEXT,
    decision_peak_price TEXT,
    decision_peak_date TEXT,
    threshold_snapshot TEXT,
    multiplier_snapshot TEXT,
    execution_mode_snapshot TEXT,
    regular_amount TEXT NOT NULL,
    extra_amount TEXT NOT NULL,
    total_amount TEXT NOT NULL,
    take_profit_cycle_id TEXT,
    take_profit_level_id TEXT,
    take_profit_cost_basis TEXT,
    take_profit_target_price TEXT,
    sell_ratio_snapshot TEXT,
    planned_sell_units TEXT,
    actual_sell_units TEXT,
    recurring_amount_before TEXT,
    recurring_amount_after TEXT,
    valuation_status TEXT,
    valuation_type TEXT,
    estimated_nav TEXT,
    reference_nav TEXT,
    reference_nav_date TEXT,
    signal_previous_close TEXT,
    signal_intraday_return TEXT,
    official_nav TEXT,
    official_nav_date TEXT,
    reconciled_at TEXT,
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

CREATE TABLE IF NOT EXISTS market_snapshots (
    plan_id TEXT PRIMARY KEY REFERENCES plans(id) ON DELETE CASCADE,
    quote_price TEXT NOT NULL,
    quote_time TEXT NOT NULL,
    daily_change TEXT,
    highest_close TEXT,
    highest_close_date TEXT,
    drawdown TEXT,
    cycle_peak TEXT,
    cycle_drawdown TEXT,
    strategy_stage TEXT NOT NULL DEFAULT 'normal',
    decision_peak_type TEXT,
    decision_peak_price TEXT,
    decision_peak_date TEXT,
    take_profit_peak TEXT,
    take_profit_peak_date TEXT,
    take_profit_drawdown TEXT,
    updated_at TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS take_profit_levels (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    profit_rate TEXT NOT NULL,
    sell_ratio TEXT NOT NULL,
    sell_all INTEGER NOT NULL DEFAULT 0,
    next_recurring_amount TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(plan_id, profit_rate)
);

CREATE TABLE IF NOT EXISTS recovery_levels (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    drawdown TEXT NOT NULL,
    recurring_amount TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(plan_id, drawdown)
);

CREATE TABLE IF NOT EXISTS take_profit_cycles (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    cost_basis TEXT,
    actual_units TEXT NOT NULL DEFAULT '0',
    basis_status TEXT NOT NULL DEFAULT 'draft',
    effective_date TEXT,
    take_profit_peak TEXT,
    take_profit_peak_date TEXT,
    previous_official_nav TEXT,
    previous_official_nav_date TEXT,
    valuation_type TEXT NOT NULL DEFAULT 'standard',
    basis_review_status TEXT NOT NULL DEFAULT 'needs_review',
    state TEXT NOT NULL DEFAULT 'preparing',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS one_current_take_profit_cycle_per_plan
ON take_profit_cycles(plan_id) WHERE state IN ('preparing', 'active', 'recovering');

CREATE TABLE IF NOT EXISTS fund_nav_records (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    nav_date TEXT NOT NULL,
    official_nav TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(plan_id, nav_date)
);

CREATE TABLE IF NOT EXISTS take_profit_executions (
    id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES take_profit_cycles(id),
    level_id TEXT NOT NULL REFERENCES take_profit_levels(id),
    event_id TEXT NOT NULL REFERENCES events(id),
    actual_sell_units TEXT NOT NULL,
    quote_price TEXT NOT NULL,
    recurring_amount_before TEXT NOT NULL,
    recurring_amount_after TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    UNIQUE(cycle_id, level_id)
);

CREATE TABLE IF NOT EXISTS recovery_executions (
    id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES take_profit_cycles(id),
    level_id TEXT NOT NULL REFERENCES recovery_levels(id),
    event_id TEXT NOT NULL REFERENCES events(id),
    recurring_amount_before TEXT NOT NULL,
    recurring_amount_after TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    UNIQUE(cycle_id, level_id)
);

CREATE TABLE IF NOT EXISTS daily_summaries (
    id TEXT PRIMARY KEY,
    check_run_id TEXT,
    trading_date TEXT NOT NULL,
    scheduled_check INTEGER NOT NULL,
    email_recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    attempted_at TEXT,
    sent_at TEXT,
    error_summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(trading_date, scheduled_check, email_recipient)
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
            else:
                if current < 2:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS market_snapshots (
                            plan_id TEXT PRIMARY KEY REFERENCES plans(id) ON DELETE CASCADE,
                            quote_price TEXT NOT NULL,
                            quote_time TEXT NOT NULL,
                            daily_change TEXT,
                            highest_close TEXT,
                            drawdown TEXT,
                            updated_at TEXT NOT NULL
                        )
                        """
                    )
                    connection.execute("PRAGMA user_version = 2")
                if current < 3:
                    columns = {
                        row["name"]
                        for row in connection.execute("PRAGMA table_info(drawdown_cycles)")
                    }
                    if "recovery_last_trading_date" not in columns:
                        connection.execute(
                            """
                            ALTER TABLE drawdown_cycles
                            ADD COLUMN recovery_last_trading_date TEXT
                            """
                        )
                    connection.execute("PRAGMA user_version = 3")
                if current < 4:
                    cycle_columns = {
                        row["name"]
                        for row in connection.execute("PRAGMA table_info(drawdown_cycles)")
                    }
                    if "cycle_peak" not in cycle_columns:
                        connection.execute(
                            "ALTER TABLE drawdown_cycles ADD COLUMN cycle_peak TEXT"
                        )
                    snapshot_columns = {
                        row["name"]
                        for row in connection.execute("PRAGMA table_info(market_snapshots)")
                    }
                    if "cycle_peak" not in snapshot_columns:
                        connection.execute(
                            "ALTER TABLE market_snapshots ADD COLUMN cycle_peak TEXT"
                        )
                    if "cycle_drawdown" not in snapshot_columns:
                        connection.execute(
                            "ALTER TABLE market_snapshots ADD COLUMN cycle_drawdown TEXT"
                        )
                    connection.execute("PRAGMA user_version = 4")
                if current < 5:
                    level_columns = {
                        row["name"]
                        for row in connection.execute("PRAGMA table_info(strategy_levels)")
                    }
                    for column, definition in (
                        ("max_executions", "INTEGER"),
                        ("cycle_amount_cap", "TEXT"),
                        ("cycle_multiplier_cap", "TEXT"),
                    ):
                        if column not in level_columns:
                            connection.execute(
                                f"ALTER TABLE strategy_levels ADD COLUMN {column} {definition}"
                            )
                    cycle_columns = {
                        row["name"]
                        for row in connection.execute("PRAGMA table_info(drawdown_cycles)")
                    }
                    if "budget_started_at" not in cycle_columns:
                        connection.execute(
                            "ALTER TABLE drawdown_cycles ADD COLUMN budget_started_at TEXT"
                        )
                    if "paused_for_review" not in cycle_columns:
                        connection.execute(
                            """
                            ALTER TABLE drawdown_cycles
                            ADD COLUMN paused_for_review INTEGER NOT NULL DEFAULT 0
                            """
                        )
                    connection.execute(
                        """
                        UPDATE drawdown_cycles SET budget_started_at = started_at
                        WHERE budget_started_at IS NULL
                        """
                    )
                    connection.execute(
                        """
                        UPDATE strategy_levels
                        SET max_executions = COALESCE(max_executions, 4),
                            cycle_multiplier_cap = COALESCE(cycle_multiplier_cap, '4')
                        WHERE execution_mode = 'weekly_while_deep'
                        """
                    )
                    connection.execute("PRAGMA user_version = 5")
                if current < 6:
                    plan_columns = {
                        row["name"] for row in connection.execute("PRAGMA table_info(plans)")
                    }
                    if "current_amount" not in plan_columns:
                        connection.execute(
                            "ALTER TABLE plans ADD COLUMN current_amount TEXT NOT NULL DEFAULT '0'"
                        )
                        connection.execute(
                            "UPDATE plans SET current_amount = base_amount WHERE current_amount = '0'"
                        )
                    if "status" not in plan_columns:
                        connection.execute(
                            "ALTER TABLE plans ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
                        )
                    event_columns = {
                        row["name"] for row in connection.execute("PRAGMA table_info(events)")
                    }
                    for column, definition in (
                        ("rolling_20d_high_date", "TEXT"),
                        ("decision_peak_type", "TEXT"),
                        ("decision_peak_price", "TEXT"),
                        ("decision_peak_date", "TEXT"),
                        ("take_profit_cycle_id", "TEXT"),
                        ("take_profit_level_id", "TEXT"),
                        ("take_profit_cost_basis", "TEXT"),
                        ("take_profit_target_price", "TEXT"),
                        ("sell_ratio_snapshot", "TEXT"),
                        ("planned_sell_units", "TEXT"),
                        ("actual_sell_units", "TEXT"),
                        ("recurring_amount_before", "TEXT"),
                        ("recurring_amount_after", "TEXT"),
                    ):
                        if column not in event_columns:
                            connection.execute(
                                f"ALTER TABLE events ADD COLUMN {column} {definition}"
                            )
                    snapshot_columns = {
                        row["name"]
                        for row in connection.execute("PRAGMA table_info(market_snapshots)")
                    }
                    for column, definition in (
                        ("highest_close_date", "TEXT"),
                        ("strategy_stage", "TEXT NOT NULL DEFAULT 'normal'"),
                        ("decision_peak_type", "TEXT"),
                        ("decision_peak_price", "TEXT"),
                        ("decision_peak_date", "TEXT"),
                        ("take_profit_peak", "TEXT"),
                        ("take_profit_peak_date", "TEXT"),
                        ("take_profit_drawdown", "TEXT"),
                    ):
                        if column not in snapshot_columns:
                            connection.execute(
                                f"ALTER TABLE market_snapshots ADD COLUMN {column} {definition}"
                            )
                    recovery_columns = {
                        row["name"]
                        for row in connection.execute("PRAGMA table_info(recovery_levels)")
                    }
                    if recovery_columns and "enabled" not in recovery_columns:
                        connection.execute(
                            """
                            ALTER TABLE recovery_levels
                            ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1
                            """
                        )
                    connection.executescript(SCHEMA_SQL)
                    connection.execute("PRAGMA user_version = 6")
                if current < 7:
                    cycle_columns = {
                        row["name"]
                        for row in connection.execute(
                            "PRAGMA table_info(take_profit_cycles)"
                        )
                    }
                    for column, definition in (
                        ("previous_official_nav", "TEXT"),
                        ("previous_official_nav_date", "TEXT"),
                        ("valuation_type", "TEXT NOT NULL DEFAULT 'standard'"),
                        (
                            "basis_review_status",
                            "TEXT NOT NULL DEFAULT 'needs_review'",
                        ),
                    ):
                        if column not in cycle_columns:
                            connection.execute(
                                f"ALTER TABLE take_profit_cycles ADD COLUMN {column} {definition}"
                            )
                    event_columns = {
                        row["name"]
                        for row in connection.execute("PRAGMA table_info(events)")
                    }
                    for column, definition in (
                        ("valuation_status", "TEXT"),
                        ("valuation_type", "TEXT"),
                        ("estimated_nav", "TEXT"),
                        ("reference_nav", "TEXT"),
                        ("reference_nav_date", "TEXT"),
                        ("signal_previous_close", "TEXT"),
                        ("signal_intraday_return", "TEXT"),
                        ("official_nav", "TEXT"),
                        ("official_nav_date", "TEXT"),
                        ("reconciled_at", "TEXT"),
                    ):
                        if column not in event_columns:
                            connection.execute(
                                f"ALTER TABLE events ADD COLUMN {column} {definition}"
                            )
                    connection.executescript(SCHEMA_SQL)
                    connection.execute("PRAGMA user_version = 7")

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
                    base_amount, current_amount, invest_weekday, recurring_enabled,
                    drawdown_enabled, enabled, status, active_strategy_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(plan.id), plan.name, plan.purchase_code, plan.purchase_name,
                    plan.signal_code, plan.signal_name, str(plan.base_amount),
                    str(plan.recurring_amount),
                    plan.invest_weekday, int(plan.recurring_enabled),
                    int(plan.drawdown_enabled), int(plan.enabled), plan.status.value,
                    str(strategy.id), now, now,
                ),
            )
            self._insert_strategy(connection, strategy)
            connection.execute(
                """
                INSERT INTO plan_configuration_versions
                    (id, plan_id, version, snapshot_json, created_at)
                VALUES (?, ?, 1, ?, ?)
                """,
                (
                    str(uuid4()), str(plan.id),
                    json.dumps(
                        {
                            "base_amount": str(plan.base_amount),
                            "current_amount": str(plan.recurring_amount),
                            "invest_weekday": plan.invest_weekday,
                            "recurring_enabled": plan.recurring_enabled,
                            "drawdown_enabled": plan.drawdown_enabled,
                        },
                        sort_keys=True,
                    ),
                    now,
                ),
            )

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
                    interval_weeks, enabled, amount_cap, max_executions,
                    cycle_amount_cap, cycle_multiplier_cap, note, position
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(level.id), str(strategy.id), str(level.threshold), str(level.multiplier),
                    level.execution_mode.value, level.phases, level.interval_weeks,
                    int(level.enabled), str(level.amount_cap) if level.amount_cap else None,
                    level.max_executions,
                    str(level.cycle_amount_cap) if level.cycle_amount_cap else None,
                    str(level.cycle_multiplier_cap) if level.cycle_multiplier_cap else None,
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

    def list_plans(self, *, include_archived: bool = False) -> list[Plan]:
        with self.read() as connection:
            query = "SELECT * FROM plans"
            parameters: tuple[object, ...] = ()
            if not include_archived:
                query += " WHERE status = ?"
                parameters = (PlanStatus.ACTIVE.value,)
            rows = connection.execute(query + " ORDER BY created_at", parameters).fetchall()
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
                    current_amount = ?, invest_weekday = ?, recurring_enabled = ?,
                    drawdown_enabled = ?, enabled = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    plan.name, plan.purchase_code, plan.purchase_name,
                    plan.signal_code, plan.signal_name, str(plan.base_amount),
                    str(plan.recurring_amount), plan.invest_weekday,
                    int(plan.recurring_enabled), int(plan.drawdown_enabled), int(plan.enabled),
                    plan.status.value,
                    utc_now_text(), str(plan.id),
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError("plan not found")
            version = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1
                FROM plan_configuration_versions WHERE plan_id = ?
                """,
                (str(plan.id),),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO plan_configuration_versions
                    (id, plan_id, version, snapshot_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()), str(plan.id), version,
                    json.dumps(
                        {
                            "base_amount": str(plan.base_amount),
                            "current_amount": str(plan.recurring_amount),
                            "invest_weekday": plan.invest_weekday,
                            "recurring_enabled": plan.recurring_enabled,
                            "drawdown_enabled": plan.drawdown_enabled,
                        },
                        sort_keys=True,
                    ),
                    utc_now_text(),
                ),
            )

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

    def events_for_plan_date(
        self, plan_id: UUID, trading_date: date
    ) -> list[sqlite3.Row]:
        with self.read() as connection:
            return connection.execute(
                """
                SELECT * FROM events
                WHERE plan_id = ? AND trading_date = ?
                ORDER BY created_at
                """,
                (str(plan_id), trading_date.isoformat()),
            ).fetchall()

    def recurring_event_exists(self, plan_id: UUID, trading_date: date) -> bool:
        with self.read() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM events
                WHERE plan_id = ? AND trading_date = ?
                  AND CAST(regular_amount AS NUMERIC) > 0
                LIMIT 1
                """,
                (str(plan_id), trading_date.isoformat()),
            ).fetchone()
        return row is not None

    def recent_daily_summaries(self, limit: int = 30) -> list[sqlite3.Row]:
        with self.read() as connection:
            return connection.execute(
                """
                SELECT * FROM daily_summaries
                ORDER BY created_at DESC LIMIT ?
                """,
                (max(limit, 1),),
            ).fetchall()

    def save_market_snapshot(
        self,
        *,
        plan_id: UUID,
        quote_price: Decimal,
        quote_time: datetime,
        daily_change: Decimal | None,
        highest_close: Decimal | None,
        drawdown: Decimal | None,
        cycle_peak: Decimal | None = None,
        cycle_drawdown: Decimal | None = None,
        highest_close_date: date | None = None,
        strategy_stage: str = "normal",
        decision_peak_type: str | None = None,
        decision_peak_price: Decimal | None = None,
        decision_peak_date: date | None = None,
        take_profit_peak: Decimal | None = None,
        take_profit_drawdown: Decimal | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO market_snapshots (
                    plan_id, quote_price, quote_time, daily_change,
                    highest_close, highest_close_date, drawdown, cycle_peak, cycle_drawdown,
                    strategy_stage, decision_peak_type, decision_peak_price,
                    decision_peak_date, take_profit_peak, take_profit_drawdown, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    quote_price = excluded.quote_price,
                    quote_time = excluded.quote_time,
                    daily_change = excluded.daily_change,
                    highest_close = excluded.highest_close,
                    highest_close_date = excluded.highest_close_date,
                    drawdown = excluded.drawdown,
                    cycle_peak = excluded.cycle_peak,
                    cycle_drawdown = excluded.cycle_drawdown,
                    strategy_stage = excluded.strategy_stage,
                    decision_peak_type = excluded.decision_peak_type,
                    decision_peak_price = excluded.decision_peak_price,
                    decision_peak_date = excluded.decision_peak_date,
                    take_profit_peak = excluded.take_profit_peak,
                    take_profit_drawdown = excluded.take_profit_drawdown,
                    updated_at = excluded.updated_at
                """,
                (
                    str(plan_id),
                    str(quote_price),
                    quote_time.isoformat(),
                    str(daily_change) if daily_change is not None else None,
                    str(highest_close) if highest_close is not None else None,
                    highest_close_date.isoformat() if highest_close_date else None,
                    str(drawdown) if drawdown is not None else None,
                    str(cycle_peak) if cycle_peak is not None else None,
                    str(cycle_drawdown) if cycle_drawdown is not None else None,
                    strategy_stage,
                    decision_peak_type,
                    str(decision_peak_price) if decision_peak_price is not None else None,
                    decision_peak_date.isoformat() if decision_peak_date else None,
                    str(take_profit_peak) if take_profit_peak is not None else None,
                    str(take_profit_drawdown) if take_profit_drawdown is not None else None,
                    utc_now_text(),
                ),
            )

    def market_snapshots(self) -> list[sqlite3.Row]:
        with self.read() as connection:
            return connection.execute(
                "SELECT * FROM market_snapshots ORDER BY updated_at DESC"
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
            "plans", "plan_configuration_versions", "strategy_versions",
            "strategy_levels", "drawdown_cycles",
            "events", "notification_deliveries", "check_runs", "market_snapshots", "settings",
            "message_templates", "audit_log", "take_profit_levels", "recovery_levels",
            "take_profit_cycles", "take_profit_executions", "recovery_executions",
            "fund_nav_records", "daily_summaries",
        )
        with self.read() as connection:
            return {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }

    def set_setting(self, key: str, value: str) -> None:
        lowered = key.lower()
        if any(marker in lowered for marker in SENSITIVE_SETTING_MARKERS):
            raise ValueError("sensitive values must be stored outside the SQLite database")
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

    def start_check_run(self, started_at: datetime) -> UUID:
        run_id = uuid4()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO check_runs (
                    id, started_at, trading_date, state, checked_plans, created_events
                ) VALUES (?, ?, ?, 'running', 0, 0)
                """,
                (
                    str(run_id),
                    started_at.isoformat(timespec="seconds"),
                    started_at.date().isoformat(),
                ),
            )
        return run_id

    def finish_check_run(
        self,
        run_id: UUID,
        *,
        state: str,
        checked_plans: int = 0,
        created_events: int = 0,
        error_summary: str | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE check_runs
                SET finished_at = ?, state = ?, error_summary = ?,
                    checked_plans = ?, created_events = ?
                WHERE id = ?
                """,
                (
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    state,
                    error_summary,
                    checked_plans,
                    created_events,
                    str(run_id),
                ),
            )

    def latest_check_run(self) -> sqlite3.Row | None:
        with self.read() as connection:
            return connection.execute(
                "SELECT * FROM check_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()

    @staticmethod
    def _row_to_plan(row: sqlite3.Row) -> Plan:
        return Plan(
            id=UUID(row["id"]), name=row["name"], purchase_code=row["purchase_code"],
            purchase_name=row["purchase_name"], signal_code=row["signal_code"],
            signal_name=row["signal_name"], base_amount=Decimal(row["base_amount"]),
            current_amount=Decimal(row["current_amount"]),
            invest_weekday=row["invest_weekday"], recurring_enabled=bool(row["recurring_enabled"]),
            drawdown_enabled=bool(row["drawdown_enabled"]), enabled=bool(row["enabled"]),
            status=PlanStatus(row["status"]),
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
                    max_executions=level["max_executions"],
                    cycle_amount_cap=(
                        Decimal(level["cycle_amount_cap"])
                        if level["cycle_amount_cap"] else None
                    ),
                    cycle_multiplier_cap=(
                        Decimal(level["cycle_multiplier_cap"])
                        if level["cycle_multiplier_cap"] else None
                    ),
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
        rolling_20d_high_date: date | None = None,
        decision_peak_type: DecisionPeakType | None = None,
        decision_peak_price: Decimal | None = None,
        decision_peak_date: date | None = None,
        take_profit_cycle_id: UUID | None = None,
        take_profit_level_id: UUID | None = None,
        take_profit_cost_basis: Decimal | None = None,
        take_profit_target_price: Decimal | None = None,
        sell_ratio_snapshot: Decimal | None = None,
        planned_sell_units: Decimal | None = None,
        recurring_amount_before: Decimal | None = None,
        recurring_amount_after: Decimal | None = None,
        valuation_status: ValuationStatus | None = None,
        valuation_type: ValuationType | None = None,
        estimated_nav: Decimal | None = None,
        reference_nav: Decimal | None = None,
        reference_nav_date: date | None = None,
        signal_previous_close: Decimal | None = None,
        signal_intraday_return: Decimal | None = None,
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
                    highest_close, drawdown, rolling_20d_high_date, decision_peak_type,
                    decision_peak_price, decision_peak_date,
                    threshold_snapshot, multiplier_snapshot,
                    execution_mode_snapshot, regular_amount, extra_amount, total_amount,
                    take_profit_cycle_id, take_profit_level_id, take_profit_cost_basis,
                    take_profit_target_price, sell_ratio_snapshot, planned_sell_units,
                    recurring_amount_before, recurring_amount_after,
                    valuation_status, valuation_type, estimated_nav, reference_nav, reference_nav_date,
                    signal_previous_close, signal_intraday_return,
                    state, execution_status, idempotency_key, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    str(event_id), str(plan_id), str(cycle_id) if cycle_id else None,
                    str(strategy_id), str(level_id) if level_id else None, event_type,
                    trading_date, week_key, str(quote_price), quote_time.isoformat(),
                    str(daily_change) if daily_change is not None else None,
                    str(highest_close), str(drawdown),
                    rolling_20d_high_date.isoformat() if rolling_20d_high_date else None,
                    decision_peak_type.value if decision_peak_type else None,
                    str(decision_peak_price) if decision_peak_price is not None else None,
                    decision_peak_date.isoformat() if decision_peak_date else None,
                    str(threshold_snapshot) if threshold_snapshot is not None else None,
                    str(multiplier_snapshot) if multiplier_snapshot is not None else None,
                    execution_mode_snapshot, str(regular_amount), str(extra_amount),
                    str(regular_amount + extra_amount),
                    str(take_profit_cycle_id) if take_profit_cycle_id else None,
                    str(take_profit_level_id) if take_profit_level_id else None,
                    str(take_profit_cost_basis) if take_profit_cost_basis is not None else None,
                    str(take_profit_target_price) if take_profit_target_price is not None else None,
                    str(sell_ratio_snapshot) if sell_ratio_snapshot is not None else None,
                    str(planned_sell_units) if planned_sell_units is not None else None,
                    (
                        str(recurring_amount_before)
                        if recurring_amount_before is not None else None
                    ),
                    (
                        str(recurring_amount_after)
                        if recurring_amount_after is not None else None
                    ),
                    valuation_status.value if valuation_status else None,
                    valuation_type.value if valuation_type else None,
                    str(estimated_nav) if estimated_nav is not None else None,
                    str(reference_nav) if reference_nav is not None else None,
                    reference_nav_date.isoformat() if reference_nav_date else None,
                    (
                        str(signal_previous_close)
                        if signal_previous_close is not None else None
                    ),
                    (
                        str(signal_intraday_return)
                        if signal_intraday_return is not None else None
                    ),
                    state.value,
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
                    """
                    UPDATE events
                    SET state = CASE
                            WHEN state = ? THEN state
                            ELSE ?
                        END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        EventState.SUPPRESSED.value,
                        EventState.NOTIFIED.value,
                        now,
                        str(event_id),
                    ),
                )

    def get_or_create_active_cycle(
        self, plan_id: UUID, strategy_id: UUID, cycle_peak: Decimal | None = None
    ) -> UUID:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM drawdown_cycles WHERE plan_id = ? AND state = 'active'",
                (str(plan_id),),
            ).fetchone()
            if row:
                if row["cycle_peak"] is None and cycle_peak is not None:
                    connection.execute(
                        "UPDATE drawdown_cycles SET cycle_peak = ? WHERE id = ?",
                        (str(cycle_peak), row["id"]),
                    )
                return UUID(row["id"])
            cycle_id = uuid4()
            now = utc_now_text()
            connection.execute(
                """
                INSERT INTO drawdown_cycles (
                    id, plan_id, strategy_id, started_at, cycle_peak,
                    budget_started_at, state
                ) VALUES (?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    str(cycle_id), str(plan_id), str(strategy_id), now,
                    str(cycle_peak) if cycle_peak is not None else None, now,
                ),
            )
            return cycle_id

    def get_active_cycle(self, plan_id: UUID) -> sqlite3.Row | None:
        with self.read() as connection:
            return connection.execute(
                "SELECT * FROM drawdown_cycles WHERE plan_id = ? AND state = 'active'",
                (str(plan_id),),
            ).fetchone()

    def resolve_cycle_peak(self, plan_id: UUID, fallback: Decimal) -> Decimal:
        """Return the immutable peak, recovering legacy cycles from event snapshots."""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM drawdown_cycles WHERE plan_id = ? AND state = 'active'",
                (str(plan_id),),
            ).fetchone()
            if row is None:
                raise KeyError("active cycle not found")
            if row["cycle_peak"] is not None:
                return Decimal(row["cycle_peak"])
            values = connection.execute(
                "SELECT highest_close FROM events WHERE cycle_id = ?",
                (row["id"],),
            ).fetchall()
            peak = max(
                (Decimal(value["highest_close"]) for value in values),
                default=fallback,
            )
            connection.execute(
                "UPDATE drawdown_cycles SET cycle_peak = ? WHERE id = ?",
                (str(peak), row["id"]),
            )
            return peak

    def cycle_budget_usage(self, cycle_id: UUID) -> tuple[int, Decimal, Decimal]:
        with self.read() as connection:
            cycle = connection.execute(
                "SELECT budget_started_at, started_at FROM drawdown_cycles WHERE id = ?",
                (str(cycle_id),),
            ).fetchone()
            if cycle is None:
                raise KeyError("cycle not found")
            rows = connection.execute(
                """
                SELECT extra_amount, multiplier_snapshot FROM events
                WHERE cycle_id = ? AND execution_mode_snapshot = 'weekly_while_deep'
                  AND created_at >= COALESCE(?, ?)
                  AND state IN (?, ?, ?)
                """,
                (
                    str(cycle_id), cycle["budget_started_at"], cycle["started_at"],
                    EventState.NOTIFIED.value,
                    EventState.IGNORED.value, EventState.POSTPONED.value,
                ),
            ).fetchall()
        return (
            len(rows),
            sum((Decimal(row["extra_amount"]) for row in rows), Decimal("0")),
            sum(
                (Decimal(row["multiplier_snapshot"]) for row in rows),
                Decimal("0"),
            ),
        )

    def set_cycle_paused(self, cycle_id: UUID, paused: bool) -> None:
        with self.transaction() as connection:
            if paused:
                connection.execute(
                    "UPDATE drawdown_cycles SET paused_for_review = 1 WHERE id = ?",
                    (str(cycle_id),),
                )
            else:
                connection.execute(
                    """
                    UPDATE drawdown_cycles
                    SET paused_for_review = 0, budget_started_at = ?
                    WHERE id = ? AND state = 'active'
                    """,
                    (
                        datetime.now().astimezone().isoformat(timespec="microseconds"),
                        str(cycle_id),
                    ),
                )

    def resume_cycle(self, plan_id: UUID) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE drawdown_cycles
                SET paused_for_review = 0, budget_started_at = ?
                WHERE plan_id = ? AND state = 'active' AND paused_for_review = 1
                """,
                (
                    datetime.now().astimezone().isoformat(timespec="microseconds"),
                    str(plan_id),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("该基金当前没有等待确认的补仓周期")

    def update_recovery(self, plan_id: UUID, recovered: bool, trading_date: date) -> bool:
        """End a cycle after recovery checks on two distinct trading dates."""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM drawdown_cycles WHERE plan_id = ? AND state = 'active'",
                (str(plan_id),),
            ).fetchone()
            if row is None:
                return False
            value = trading_date.isoformat()
            if not recovered:
                connection.execute(
                    """
                    UPDATE drawdown_cycles
                    SET recovery_checks = 0, recovery_last_trading_date = NULL
                    WHERE id = ?
                    """,
                    (row["id"],),
                )
                return False
            if row["recovery_last_trading_date"] == value:
                return False
            checks = row["recovery_checks"] + 1
            if checks < 2:
                connection.execute(
                    """
                    UPDATE drawdown_cycles
                    SET recovery_checks = ?, recovery_last_trading_date = ?
                    WHERE id = ?
                    """,
                    (checks, value, row["id"]),
                )
                return False
            now = utc_now_text()
            connection.execute(
                """
                UPDATE drawdown_cycles
                SET state = 'ended', ended_at = ?, recovery_checks = ?,
                    recovery_last_trading_date = ?
                WHERE id = ?
                """,
                (now, checks, value, row["id"]),
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
        """Return delivered alerts by threshold across strategy versions."""
        with self.read() as connection:
            return connection.execute(
                """
                SELECT e.* FROM events e
                WHERE e.cycle_id = ? AND e.threshold_snapshot = ?
                  AND (
                    e.state IN (?, ?, ?)
                    OR (
                      e.state = ?
                      AND EXISTS (
                        SELECT 1 FROM notification_deliveries d
                        WHERE d.event_id = e.id AND d.state = ?
                      )
                    )
                  )
                ORDER BY e.trading_date, e.created_at
                """,
                (
                    str(cycle_id),
                    str(threshold),
                    EventState.NOTIFIED.value,
                    EventState.IGNORED.value,
                    EventState.POSTPONED.value,
                    EventState.SUPPRESSED.value,
                    DeliveryState.SENT.value,
                ),
            ).fetchall()

    def highest_notified_threshold(self, cycle_id: UUID) -> Decimal | None:
        with self.read() as connection:
            rows = connection.execute(
                """
                SELECT e.threshold_snapshot FROM events e
                WHERE e.cycle_id = ? AND e.threshold_snapshot IS NOT NULL
                  AND (
                    e.state IN (?, ?, ?)
                    OR (
                      e.state = ?
                      AND EXISTS (
                        SELECT 1 FROM notification_deliveries d
                        WHERE d.event_id = e.id AND d.state = ?
                      )
                    )
                  )
                """,
                (
                    str(cycle_id),
                    EventState.NOTIFIED.value,
                    EventState.IGNORED.value,
                    EventState.POSTPONED.value,
                    EventState.SUPPRESSED.value,
                    DeliveryState.SENT.value,
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

    def archive_plan(self, plan_id: UUID) -> None:
        now = utc_now_text()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM plans WHERE id = ?", (str(plan_id),)
            ).fetchone()
            if row is None:
                raise KeyError("plan not found")
            if row["status"] == PlanStatus.ARCHIVED.value:
                return
            snapshot = dict(row)
            connection.execute(
                """
                UPDATE plans
                SET status = ?, enabled = 0, updated_at = ?
                WHERE id = ?
                """,
                (PlanStatus.ARCHIVED.value, now, str(plan_id)),
            )
            connection.execute(
                """
                INSERT INTO audit_log
                    (id, action, entity_type, entity_id, details_json, created_at)
                VALUES (?, 'plan_archived', 'plan', ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(plan_id),
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
    def replace_take_profit_rules(
        self,
        plan_id: UUID,
        take_profit_levels: tuple[TakeProfitLevel, ...],
        recovery_levels: tuple[RecoveryLevel, ...],
    ) -> None:
        for level in take_profit_levels:
            level.validate()
        for level in recovery_levels:
            level.validate()
        if len({level.profit_rate for level in take_profit_levels}) != len(take_profit_levels):
            raise ValueError("take-profit rates must be unique")
        if len({level.drawdown for level in recovery_levels}) != len(recovery_levels):
            raise ValueError("recovery drawdowns must be unique")
        now = utc_now_text()
        with self.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM plans WHERE id = ?", (str(plan_id),)
            ).fetchone() is None:
                raise KeyError("plan not found")
            connection.execute(
                "UPDATE take_profit_levels SET enabled = 0 WHERE plan_id = ?",
                (str(plan_id),),
            )
            for position, level in enumerate(
                sorted(take_profit_levels, key=lambda item: item.profit_rate)
            ):
                existing = connection.execute(
                    """
                    SELECT id FROM take_profit_levels
                    WHERE plan_id = ? AND profit_rate = ?
                    """,
                    (str(plan_id), str(level.profit_rate)),
                ).fetchone()
                level_id = existing["id"] if existing else str(level.id)
                connection.execute(
                    """
                    INSERT INTO take_profit_levels (
                        id, plan_id, profit_rate, sell_ratio, sell_all,
                        next_recurring_amount, enabled, position, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(plan_id, profit_rate) DO UPDATE SET
                        sell_ratio = excluded.sell_ratio,
                        sell_all = excluded.sell_all,
                        next_recurring_amount = excluded.next_recurring_amount,
                        enabled = excluded.enabled,
                        position = excluded.position
                    """,
                    (
                        level_id, str(plan_id), str(level.profit_rate),
                        str(level.sell_ratio), int(level.sell_all),
                        str(level.next_recurring_amount), int(level.enabled), position, now,
                    ),
                )
            connection.execute(
                "UPDATE recovery_levels SET enabled = 0 WHERE plan_id = ?",
                (str(plan_id),),
            )
            for position, level in enumerate(
                sorted(recovery_levels, key=lambda item: item.drawdown)
            ):
                connection.execute(
                    """
                    INSERT INTO recovery_levels (
                        id, plan_id, drawdown, recurring_amount, enabled, position, created_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(plan_id, drawdown) DO UPDATE SET
                        recurring_amount = excluded.recurring_amount,
                        enabled = 1,
                        position = excluded.position
                    """,
                    (
                        str(level.id), str(plan_id), str(level.drawdown),
                        (
                            str(level.recurring_amount)
                            if level.recurring_amount is not None else None
                        ),
                        position, now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO audit_log
                    (id, action, entity_type, entity_id, details_json, created_at)
                VALUES (?, 'take_profit_rules_updated', 'plan', ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(plan_id),
                    json.dumps(
                        {
                            "take_profit_levels": [
                                {
                                    "profit_rate": str(level.profit_rate),
                                    "sell_ratio": str(level.sell_ratio),
                                    "sell_all": level.sell_all,
                                    "next_recurring_amount": str(
                                        level.next_recurring_amount
                                    ),
                                }
                                for level in take_profit_levels
                            ],
                            "recovery_levels": [
                                {
                                    "drawdown": str(level.drawdown),
                                    "recurring_amount": (
                                        str(level.recurring_amount)
                                        if level.recurring_amount is not None else None
                                    ),
                                }
                                for level in recovery_levels
                            ],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )
            version = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1
                FROM plan_configuration_versions WHERE plan_id = ?
                """,
                (str(plan_id),),
            ).fetchone()[0]
            plan = connection.execute(
                "SELECT * FROM plans WHERE id = ?", (str(plan_id),)
            ).fetchone()
            connection.execute(
                """
                INSERT INTO plan_configuration_versions
                    (id, plan_id, version, snapshot_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()), str(plan_id), version,
                    json.dumps(
                        {
                            "base_amount": plan["base_amount"],
                            "current_amount": plan["current_amount"],
                            "take_profit_levels": [
                                {
                                    "profit_rate": str(level.profit_rate),
                                    "sell_ratio": str(level.sell_ratio),
                                    "sell_all": level.sell_all,
                                    "next_recurring_amount": str(
                                        level.next_recurring_amount
                                    ),
                                }
                                for level in take_profit_levels
                            ],
                            "recovery_levels": [
                                {
                                    "drawdown": str(level.drawdown),
                                    "recurring_amount": (
                                        str(level.recurring_amount)
                                        if level.recurring_amount is not None else None
                                    ),
                                }
                                for level in recovery_levels
                            ],
                        },
                        sort_keys=True,
                    ),
                    now,
                ),
            )

    def take_profit_levels(self, plan_id: UUID) -> tuple[TakeProfitLevel, ...]:
        with self.read() as connection:
            rows = connection.execute(
                """
                SELECT * FROM take_profit_levels
                WHERE plan_id = ? AND enabled = 1
                ORDER BY position, CAST(profit_rate AS NUMERIC)
                """,
                (str(plan_id),),
            ).fetchall()
        return tuple(
            TakeProfitLevel(
                id=UUID(row["id"]),
                profit_rate=Decimal(row["profit_rate"]),
                sell_ratio=Decimal(row["sell_ratio"]),
                sell_all=bool(row["sell_all"]),
                next_recurring_amount=Decimal(row["next_recurring_amount"]),
                enabled=bool(row["enabled"]),
                position=row["position"],
            )
            for row in rows
        )

    def recovery_levels(self, plan_id: UUID) -> tuple[RecoveryLevel, ...]:
        with self.read() as connection:
            rows = connection.execute(
                """
                SELECT * FROM recovery_levels
                WHERE plan_id = ? AND enabled = 1
                ORDER BY position, CAST(drawdown AS NUMERIC)
                """,
                (str(plan_id),),
            ).fetchall()
        return tuple(
            RecoveryLevel(
                id=UUID(row["id"]),
                drawdown=Decimal(row["drawdown"]),
                recurring_amount=(
                    Decimal(row["recurring_amount"])
                    if row["recurring_amount"] is not None else None
                ),
                position=row["position"],
            )
            for row in rows
        )

    def current_take_profit_cycle(self, plan_id: UUID) -> sqlite3.Row | None:
        with self.read() as connection:
            return connection.execute(
                """
                SELECT * FROM take_profit_cycles
                WHERE plan_id = ? AND state IN ('preparing', 'active', 'recovering')
                ORDER BY created_at DESC LIMIT 1
                """,
                (str(plan_id),),
            ).fetchone()

    def save_take_profit_basis(
        self,
        plan_id: UUID,
        *,
        actual_units: Decimal,
        cost_basis: Decimal,
        lock: bool,
        confirm_correction: bool = False,
        previous_official_nav: Decimal | None = None,
        previous_official_nav_date: date | None = None,
        valuation_type: ValuationType = ValuationType.STANDARD,
    ) -> UUID:
        if actual_units < 0 or cost_basis <= 0:
            raise ValueError("actual units cannot be negative and cost basis must be positive")
        if (previous_official_nav is None) != (previous_official_nav_date is None):
            raise ValueError("官方净值与净值日期必须同时填写")
        if previous_official_nav is not None and previous_official_nav <= 0:
            raise ValueError("上一期场外官方净值必须大于 0")
        if previous_official_nav_date and previous_official_nav_date > date.today():
            raise ValueError("官方净值日期不能晚于今天")
        if lock and previous_official_nav is None:
            raise ValueError("锁定止盈监控前请填写上一期场外官方净值和日期")
        now = utc_now_text()
        with self.transaction() as connection:
            cycle = connection.execute(
                """
                SELECT * FROM take_profit_cycles
                WHERE plan_id = ? AND state IN ('preparing', 'active', 'recovering')
                ORDER BY created_at DESC LIMIT 1
                """,
                (str(plan_id),),
            ).fetchone()
            if cycle is None:
                cycle_id = uuid4()
                connection.execute(
                    """
                    INSERT INTO take_profit_cycles (
                        id, plan_id, cost_basis, actual_units, basis_status,
                        effective_date, previous_official_nav,
                        previous_official_nav_date, valuation_type,
                        basis_review_status, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preparing', ?, ?)
                    """,
                    (
                        str(cycle_id), str(plan_id), str(cost_basis), str(actual_units),
                        (
                            TakeProfitBasisStatus.LOCKED.value
                            if lock else TakeProfitBasisStatus.DRAFT.value
                        ),
                        date.today().isoformat() if lock else None,
                        (
                            str(previous_official_nav)
                            if previous_official_nav is not None else None
                        ),
                        (
                            previous_official_nav_date.isoformat()
                            if previous_official_nav_date else None
                        ),
                        valuation_type.value,
                        "confirmed" if previous_official_nav is not None else "needs_review",
                        now, now,
                    ),
                )
                old = None
            else:
                cycle_id = UUID(cycle["id"])
                if cycle["basis_status"] == TakeProfitBasisStatus.SUPERSEDED.value:
                    raise ValueError("completed basis cannot be edited during recovery")
                locked = cycle["basis_status"] == TakeProfitBasisStatus.LOCKED.value
                basis_changed = (
                    locked and Decimal(cycle["cost_basis"]) != cost_basis
                )
                if basis_changed and not confirm_correction:
                    raise ValueError("locked basis requires confirmed correction")
                old = {
                    "cost_basis": cycle["cost_basis"],
                    "actual_units": cycle["actual_units"],
                    "basis_status": cycle["basis_status"],
                    "previous_official_nav": cycle["previous_official_nav"],
                    "previous_official_nav_date": cycle["previous_official_nav_date"],
                    "valuation_type": cycle["valuation_type"],
                }
                connection.execute(
                    """
                    UPDATE take_profit_cycles
                    SET cost_basis = ?, actual_units = ?, basis_status = ?,
                        previous_official_nav = ?,
                        previous_official_nav_date = ?,
                        valuation_type = ?,
                        basis_review_status = ?,
                        effective_date = CASE
                            WHEN ? = 'locked' THEN COALESCE(effective_date, ?)
                            ELSE NULL
                        END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        str(cost_basis), str(actual_units),
                        (
                            TakeProfitBasisStatus.LOCKED.value
                            if lock or locked else TakeProfitBasisStatus.DRAFT.value
                        ),
                        (
                            str(previous_official_nav)
                            if previous_official_nav is not None
                            else cycle["previous_official_nav"]
                        ),
                        (
                            previous_official_nav_date.isoformat()
                            if previous_official_nav_date
                            else cycle["previous_official_nav_date"]
                        ),
                        valuation_type.value,
                        (
                            "confirmed"
                            if previous_official_nav is not None
                            or cycle["previous_official_nav"] is not None
                            else "needs_review"
                        ),
                        (
                            TakeProfitBasisStatus.LOCKED.value
                            if lock or locked else TakeProfitBasisStatus.DRAFT.value
                        ),
                        date.today().isoformat(), now, str(cycle_id),
                    ),
                )
            connection.execute(
                """
                INSERT INTO audit_log
                    (id, action, entity_type, entity_id, details_json, created_at)
                VALUES (?, ?, 'take_profit_cycle', ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    (
                        "take_profit_basis_corrected"
                        if old and basis_changed
                        else "take_profit_holdings_updated"
                        if old
                        else "take_profit_basis_saved"
                    ),
                    str(cycle_id),
                    json.dumps(
                        {
                            "old": old,
                            "new": {
                                "cost_basis": str(cost_basis),
                                "actual_units": str(actual_units),
                                "previous_official_nav": (
                                    str(previous_official_nav)
                                    if previous_official_nav is not None else None
                                ),
                                "previous_official_nav_date": (
                                    previous_official_nav_date.isoformat()
                                    if previous_official_nav_date else None
                                ),
                                "valuation_type": valuation_type.value,
                                "basis_status": (
                                    TakeProfitBasisStatus.LOCKED.value
                                    if lock or (old and old["basis_status"] == "locked")
                                    else TakeProfitBasisStatus.DRAFT.value
                                ),
                            },
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )
        return cycle_id

    def record_official_nav(
        self, plan_id: UUID, *, nav_date: date, official_nav: Decimal
    ) -> int:
        if official_nav <= 0:
            raise ValueError("官方净值必须大于 0")
        if nav_date > date.today():
            raise ValueError("官方净值日期不能晚于今天")
        now = utc_now_text()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO fund_nav_records (
                    id, plan_id, nav_date, official_nav, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id, nav_date) DO UPDATE SET
                    official_nav = excluded.official_nav,
                    updated_at = excluded.updated_at
                """,
                (
                    str(uuid4()), str(plan_id), nav_date.isoformat(),
                    str(official_nav), now, now,
                ),
            )
            rows = connection.execute(
                """
                SELECT id, take_profit_target_price FROM events
                WHERE plan_id = ? AND trading_date = ?
                  AND event_type = 'take_profit'
                """,
                (str(plan_id), nav_date.isoformat()),
            ).fetchall()
            for row in rows:
                status = (
                    ValuationStatus.NAV_CONFIRMED
                    if official_nav >= Decimal(row["take_profit_target_price"])
                    else ValuationStatus.NAV_NOT_REACHED
                )
                connection.execute(
                    """
                    UPDATE events
                    SET valuation_status = ?, official_nav = ?,
                        official_nav_date = ?, reconciled_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        status.value, str(official_nav), nav_date.isoformat(),
                        now, now, row["id"],
                    ),
                )
            connection.execute(
                """
                UPDATE take_profit_cycles
                SET previous_official_nav = ?,
                    previous_official_nav_date = ?,
                    basis_review_status = 'confirmed',
                    updated_at = ?
                WHERE plan_id = ?
                  AND state IN ('preparing', 'active', 'recovering')
                """,
                (str(official_nav), nav_date.isoformat(), now, str(plan_id)),
            )
            connection.execute(
                """
                INSERT INTO audit_log
                    (id, action, entity_type, entity_id, details_json, created_at)
                VALUES (?, 'official_nav_recorded', 'plan', ?, ?, ?)
                """,
                (
                    str(uuid4()), str(plan_id),
                    json.dumps(
                        {
                            "nav_date": nav_date.isoformat(),
                            "official_nav": str(official_nav),
                            "reconciled_events": len(rows),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )
        return len(rows)

    def latest_official_nav(self, plan_id: UUID) -> sqlite3.Row | None:
        with self.read() as connection:
            return connection.execute(
                """
                SELECT * FROM fund_nav_records
                WHERE plan_id = ? ORDER BY nav_date DESC LIMIT 1
                """,
                (str(plan_id),),
            ).fetchone()

    def executed_take_profit_level_ids(self, cycle_id: UUID) -> set[UUID]:
        with self.read() as connection:
            rows = connection.execute(
                "SELECT level_id FROM take_profit_executions WHERE cycle_id = ?",
                (str(cycle_id),),
            ).fetchall()
        return {UUID(row["level_id"]) for row in rows}

    def executed_recovery_level_ids(self, cycle_id: UUID) -> set[UUID]:
        with self.read() as connection:
            rows = connection.execute(
                "SELECT level_id FROM recovery_executions WHERE cycle_id = ?",
                (str(cycle_id),),
            ).fetchall()
        return {UUID(row["level_id"]) for row in rows}

    def update_take_profit_peak(
        self, cycle_id: UUID, price: Decimal, trading_date: date
    ) -> Decimal:
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT take_profit_peak, take_profit_peak_date
                FROM take_profit_cycles WHERE id = ?
                """,
                (str(cycle_id),),
            ).fetchone()
            if row is None:
                raise KeyError("take-profit cycle not found")
            peak = max(
                price,
                Decimal(row["take_profit_peak"])
                if row["take_profit_peak"] is not None else price,
            )
            connection.execute(
                """
                UPDATE take_profit_cycles
                SET take_profit_peak = ?,
                    take_profit_peak_date = CASE
                        WHEN take_profit_peak IS NULL
                          OR CAST(take_profit_peak AS NUMERIC) < CAST(? AS NUMERIC)
                        THEN ?
                        ELSE take_profit_peak_date
                    END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    str(peak), str(price), trading_date.isoformat(),
                    utc_now_text(), str(cycle_id),
                ),
            )
        return peak

    def confirm_take_profit_execution(
        self,
        event_id: UUID,
        *,
        actual_sell_units: Decimal,
        executed_at: datetime | None = None,
    ) -> None:
        if actual_sell_units < 0:
            raise ValueError("actual sell units cannot be negative")
        now = utc_now_text()
        execution_time = (executed_at or datetime.now().astimezone()).isoformat(
            timespec="seconds"
        )
        with self.transaction() as connection:
            event = connection.execute(
                "SELECT * FROM events WHERE id = ?", (str(event_id),)
            ).fetchone()
            if event is None or event["event_type"] != "take_profit":
                raise ValueError("event is not a take-profit event")
            if event["execution_status"] == ExecutionStatus.EXECUTED.value:
                raise ValueError("take-profit event was already executed")
            cycle = connection.execute(
                "SELECT * FROM take_profit_cycles WHERE id = ?",
                (event["take_profit_cycle_id"],),
            ).fetchone()
            level = connection.execute(
                "SELECT * FROM take_profit_levels WHERE id = ?",
                (event["take_profit_level_id"],),
            ).fetchone()
            if cycle is None or level is None:
                raise RuntimeError("take-profit snapshot is incomplete")
            holdings = Decimal(cycle["actual_units"])
            if actual_sell_units > holdings:
                raise ValueError("actual sell units cannot exceed current holdings")
            if bool(level["sell_all"]) and actual_sell_units != holdings:
                raise ValueError("final take-profit level must sell all current holdings")
            before = Decimal(event["recurring_amount_before"])
            after = Decimal(level["next_recurring_amount"])
            remaining = holdings - actual_sell_units
            peak = max(
                Decimal(event["quote_price"]),
                Decimal(cycle["take_profit_peak"])
                if cycle["take_profit_peak"] is not None else Decimal("0"),
            )
            connection.execute(
                """
                INSERT INTO take_profit_executions (
                    id, cycle_id, level_id, event_id, actual_sell_units, quote_price,
                    recurring_amount_before, recurring_amount_after, executed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()), cycle["id"], level["id"], str(event_id),
                    str(actual_sell_units), event["quote_price"], str(before), str(after),
                    execution_time,
                ),
            )
            final = bool(level["sell_all"])
            connection.execute(
                """
                UPDATE take_profit_cycles
                SET actual_units = ?, take_profit_peak = ?,
                    take_profit_peak_date = ?,
                    basis_status = ?, state = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    str(remaining), str(peak), event["trading_date"],
                    (
                        TakeProfitBasisStatus.SUPERSEDED.value
                        if final else TakeProfitBasisStatus.LOCKED.value
                    ),
                    "recovering" if final else "active",
                    now, cycle["id"],
                ),
            )
            connection.execute(
                """
                UPDATE events
                SET execution_status = ?, actual_sell_units = ?, executed_at = ?,
                    recurring_amount_after = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    ExecutionStatus.EXECUTED.value, str(actual_sell_units),
                    execution_time, str(after), now, str(event_id),
                ),
            )
            connection.execute(
                "UPDATE plans SET current_amount = ?, updated_at = ? WHERE id = ?",
                (str(after), now, event["plan_id"]),
            )
            connection.execute(
                """
                UPDATE drawdown_cycles
                SET state = 'closed_by_take_profit', ended_at = ?
                WHERE plan_id = ? AND state = 'active'
                """,
                (now, event["plan_id"]),
            )
            connection.execute(
                """
                INSERT INTO audit_log
                    (id, action, entity_type, entity_id, details_json, created_at)
                VALUES (?, 'take_profit_executed', 'event', ?, ?, ?)
                """,
                (
                    str(uuid4()), str(event_id),
                    json.dumps(
                        {
                            "actual_sell_units": str(actual_sell_units),
                            "holdings_before": str(holdings),
                            "holdings_after": str(remaining),
                            "recurring_amount_before": str(before),
                            "recurring_amount_after": str(after),
                            "take_profit_peak": str(peak),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )

    def apply_recovery(
        self,
        *,
        cycle_id: UUID,
        level_id: UUID,
        event_id: UUID,
        recurring_amount: Decimal,
    ) -> None:
        now = utc_now_text()
        with self.transaction() as connection:
            cycle = connection.execute(
                "SELECT * FROM take_profit_cycles WHERE id = ?", (str(cycle_id),)
            ).fetchone()
            if cycle is None:
                raise KeyError("take-profit cycle not found")
            plan = connection.execute(
                "SELECT current_amount, base_amount FROM plans WHERE id = ?",
                (cycle["plan_id"],),
            ).fetchone()
            if plan is None:
                raise KeyError("plan not found")
            before = Decimal(plan["current_amount"])
            connection.execute(
                """
                INSERT INTO recovery_executions (
                    id, cycle_id, level_id, event_id, recurring_amount_before,
                    recurring_amount_after, triggered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()), str(cycle_id), str(level_id), str(event_id),
                    str(before), str(recurring_amount), now,
                ),
            )
            connection.execute(
                "UPDATE plans SET current_amount = ?, updated_at = ? WHERE id = ?",
                (str(recurring_amount), now, cycle["plan_id"]),
            )
            if (
                recurring_amount >= Decimal(plan["base_amount"])
                and cycle["state"] == "recovering"
            ):
                connection.execute(
                    "UPDATE take_profit_cycles SET state = 'completed', updated_at = ? WHERE id = ?",
                    (now, str(cycle_id)),
                )
                connection.execute(
                    """
                    INSERT INTO take_profit_cycles (
                        id, plan_id, actual_units, basis_status, state, created_at, updated_at
                    ) VALUES (?, ?, '0', 'draft', 'preparing', ?, ?)
                    """,
                    (str(uuid4()), cycle["plan_id"], now, now),
                )

    def create_daily_summary(
        self,
        *,
        check_run_id: UUID | None,
        trading_date: date,
        recipient: str,
        subject: str,
        body: str,
        snapshot: dict[str, object],
    ) -> tuple[UUID, bool]:
        summary_id = uuid4()
        now = utc_now_text()
        with self.transaction() as connection:
            existing = connection.execute(
                """
                SELECT id FROM daily_summaries
                WHERE trading_date = ? AND scheduled_check = 1 AND email_recipient = ?
                """,
                (trading_date.isoformat(), recipient),
            ).fetchone()
            if existing:
                return UUID(existing["id"]), False
            connection.execute(
                """
                INSERT INTO daily_summaries (
                    id, check_run_id, trading_date, scheduled_check, email_recipient,
                    subject, body, snapshot_json, state, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'daily_summary_pending', ?, ?)
                """,
                (
                    str(summary_id), str(check_run_id) if check_run_id else None,
                    trading_date.isoformat(), recipient, subject, body,
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True), now, now,
                ),
            )
        return summary_id, True

    def daily_summary_row(self, summary_id: UUID) -> sqlite3.Row:
        with self.read() as connection:
            row = connection.execute(
                "SELECT * FROM daily_summaries WHERE id = ?", (str(summary_id),)
            ).fetchone()
        if row is None:
            raise KeyError("daily summary not found")
        return row

    def mark_daily_summary(
        self, summary_id: UUID, *, sent: bool, error_summary: str | None = None
    ) -> None:
        now = utc_now_text()
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE daily_summaries
                SET state = ?, attempts = attempts + 1, attempted_at = ?,
                    sent_at = CASE WHEN ? THEN ? ELSE sent_at END,
                    error_summary = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    "daily_summary_sent" if sent else "daily_summary_failed",
                    now, int(sent), now, error_summary, now, str(summary_id),
                ),
            )

    def mark_events_from_daily_summary(
        self, event_ids: tuple[UUID, ...], *, sent: bool
    ) -> None:
        if not event_ids:
            return
        next_state = (
            EventState.NOTIFIED.value if sent else EventState.DELIVERY_FAILED.value
        )
        now = utc_now_text()
        with self.transaction() as connection:
            for event_id in event_ids:
                connection.execute(
                    """
                    UPDATE events
                    SET state = CASE
                            WHEN state = ? THEN state
                            WHEN ? = ? AND EXISTS (
                                SELECT 1 FROM notification_deliveries d
                                WHERE d.event_id = events.id AND d.state = ?
                            ) THEN ?
                            ELSE ?
                        END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        EventState.SUPPRESSED.value,
                        next_state,
                        EventState.DELIVERY_FAILED.value,
                        DeliveryState.SENT.value,
                        EventState.NOTIFIED.value,
                        next_state,
                        now,
                        str(event_id),
                    ),
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
