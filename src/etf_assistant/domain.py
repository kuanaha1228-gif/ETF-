from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4


class ExecutionMode(StrEnum):
    ONCE = "once"
    PHASED = "phased"
    WEEKLY_WHILE_DEEP = "weekly_while_deep"


class EffectiveMode(StrEnum):
    NEXT_CYCLE = "next_cycle"
    IMMEDIATE = "immediate"


class EventState(StrEnum):
    CREATED = "created"
    NOTIFIED = "notified"
    DELIVERY_FAILED = "delivery_failed"
    IGNORED = "ignored"
    POSTPONED = "postponed"
    SUPPRESSED = "suppressed"
    EXPIRED = "expired"


class ExecutionStatus(StrEnum):
    UNKNOWN = "unknown"
    EXECUTED = "executed"
    NOT_EXECUTED = "not_executed"


class DeliveryState(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    RETRYING = "retrying"
    DISABLED = "disabled"


class PlanStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TakeProfitBasisStatus(StrEnum):
    DRAFT = "draft"
    LOCKED = "locked"
    SUPERSEDED = "superseded"


class ValuationStatus(StrEnum):
    ESTIMATED_WARNING = "estimated_warning"
    NAV_CONFIRMED = "nav_confirmed"
    NAV_NOT_REACHED = "nav_not_reached"


class ValuationType(StrEnum):
    STANDARD = "standard"
    QDII_HIGH_ERROR = "qdii_high_error"


class StrategyStage(StrEnum):
    NORMAL = "normal"
    TAKE_PROFIT = "take_profit"
    RECOVERY = "recovery"


class DecisionPeakType(StrEnum):
    ROLLING_20D_HIGH = "rolling_20d_high"
    CYCLE_PEAK = "cycle_peak"
    TAKE_PROFIT_PEAK = "take_profit_peak"


@dataclass(frozen=True, slots=True)
class StrategyLevel:
    threshold: Decimal
    multiplier: Decimal
    execution_mode: ExecutionMode = ExecutionMode.ONCE
    phases: int = 1
    interval_weeks: int = 1
    enabled: bool = True
    amount_cap: Decimal | None = None
    max_executions: int | None = None
    cycle_amount_cap: Decimal | None = None
    cycle_multiplier_cap: Decimal | None = None
    note: str = ""
    id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        if not Decimal("0") < self.threshold < Decimal("100"):
            raise ValueError("threshold must be greater than 0 and less than 100")
        if self.multiplier <= 0:
            raise ValueError("multiplier must be greater than 0")
        if self.multiplier.as_tuple().exponent < -2:
            raise ValueError("multiplier supports at most 2 decimal places")
        if self.execution_mode is ExecutionMode.PHASED:
            if not 2 <= self.phases <= 12:
                raise ValueError("phased levels require 2 to 12 phases")
            if not 1 <= self.interval_weeks <= 52:
                raise ValueError("interval_weeks must be between 1 and 52")
        elif self.phases != 1:
            raise ValueError("non-phased levels must have exactly one phase")
        if self.amount_cap is not None and self.amount_cap <= 0:
            raise ValueError("amount_cap must be greater than 0")
        if self.max_executions is not None and self.max_executions < 1:
            raise ValueError("max_executions must be at least 1")
        if self.cycle_amount_cap is not None and self.cycle_amount_cap <= 0:
            raise ValueError("cycle_amount_cap must be greater than 0")
        if self.cycle_multiplier_cap is not None and self.cycle_multiplier_cap <= 0:
            raise ValueError("cycle_multiplier_cap must be greater than 0")
        if self.execution_mode is ExecutionMode.WEEKLY_WHILE_DEEP:
            if self.max_executions is None:
                raise ValueError("continuous levels require max_executions")
            if self.cycle_amount_cap is None and self.cycle_multiplier_cap is None:
                raise ValueError("continuous levels require a cycle cap")


@dataclass(frozen=True, slots=True)
class Strategy:
    plan_id: UUID
    version: int
    levels: tuple[StrategyLevel, ...]
    effective_mode: EffectiveMode = EffectiveMode.NEXT_CYCLE
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=datetime.now)

    def validate(self) -> None:
        enabled = [level for level in self.levels if level.enabled]
        if not enabled:
            raise ValueError("strategy must contain at least one enabled level")
        thresholds: set[Decimal] = set()
        for level in self.levels:
            level.validate()
            if level.threshold in thresholds:
                raise ValueError(
                    f"回撤档位 {level.threshold.normalize()}% 重复，请修改或删除其中一档"
                )
            thresholds.add(level.threshold)


@dataclass(frozen=True, slots=True)
class Plan:
    name: str
    purchase_code: str
    signal_code: str
    base_amount: Decimal
    purchase_name: str = ""
    signal_name: str = ""
    current_amount: Decimal | None = None
    invest_weekday: int = 3
    recurring_enabled: bool = True
    drawdown_enabled: bool = True
    enabled: bool = True
    status: PlanStatus = PlanStatus.ACTIVE
    id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("plan name is required")
        if len(self.purchase_code) != 6 or not self.purchase_code.isdigit():
            raise ValueError("purchase_code must be a 6-digit code")
        if len(self.signal_code) != 6 or not self.signal_code.isdigit():
            raise ValueError("signal_code must be a 6-digit code")
        if self.base_amount <= 0:
            raise ValueError("base_amount must be greater than 0")
        if self.current_amount is not None and self.current_amount < 0:
            raise ValueError("current_amount cannot be negative")
        if self.invest_weekday not in range(5):
            raise ValueError("invest_weekday must be between Monday(0) and Friday(4)")

    @property
    def recurring_amount(self) -> Decimal:
        return self.current_amount if self.current_amount is not None else self.base_amount


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    price: Decimal
    quoted_at: datetime
    daily_change: Decimal | None = None


@dataclass(frozen=True, slots=True)
class FundNav:
    symbol: str
    nav_date: date
    unit_nav: Decimal
    daily_change: Decimal | None
    source: str
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    level: StrategyLevel | None
    drawdown: Decimal
    highest_close: Decimal
    extra_amount: Decimal


@dataclass(frozen=True, slots=True)
class DatedClose:
    trading_date: date
    close: Decimal


@dataclass(frozen=True, slots=True)
class TakeProfitLevel:
    profit_rate: Decimal
    sell_ratio: Decimal
    next_recurring_amount: Decimal
    sell_all: bool = False
    enabled: bool = True
    position: int = 0
    id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        if not Decimal("0") < self.profit_rate < Decimal("1000"):
            raise ValueError("profit_rate must be greater than 0")
        if self.sell_all:
            if self.sell_ratio != Decimal("100"):
                raise ValueError("sell_all levels must use a 100% sell ratio")
        elif not Decimal("0") < self.sell_ratio < Decimal("100"):
            raise ValueError("sell_ratio must be between 0 and 100")
        if self.next_recurring_amount < 0:
            raise ValueError("next_recurring_amount cannot be negative")


@dataclass(frozen=True, slots=True)
class RecoveryLevel:
    drawdown: Decimal
    recurring_amount: Decimal | None
    position: int = 0
    id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        if not Decimal("0") < self.drawdown < Decimal("100"):
            raise ValueError("drawdown must be between 0 and 100")
        if self.recurring_amount is not None and self.recurring_amount < 0:
            raise ValueError("recurring_amount cannot be negative")
