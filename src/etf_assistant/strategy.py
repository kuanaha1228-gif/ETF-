from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from .domain import (
    DatedClose,
    ExecutionMode,
    RecoveryLevel,
    Strategy,
    StrategyLevel,
    TakeProfitLevel,
    TriggerDecision,
)


MONEY_QUANT = Decimal("0.01")


STRATEGY_TEMPLATES = {
    "stable": ("稳健 / 低波", ("3", "5", "8", "12", "18")),
    "core": ("核心宽基", ("4", "7", "10", "15", "22")),
    "growth": ("高波宽基", ("5", "9", "14", "20", "28")),
    "sector": ("行业主题", ("6", "11", "17", "25", "35")),
}


def strategy_from_template(plan_id: UUID, template: str, version: int = 1) -> Strategy:
    try:
        _, thresholds = STRATEGY_TEMPLATES[template]
    except KeyError as error:
        raise ValueError("unknown strategy template") from error
    return Strategy(
        plan_id=plan_id,
        version=version,
        levels=tuple(
            StrategyLevel(
                Decimal(threshold),
                Decimal("2") if position == 2 else Decimal("1"),
                execution_mode=(
                    ExecutionMode.PHASED
                    if position == 3
                    else ExecutionMode.WEEKLY_WHILE_DEEP
                    if position == 4
                    else ExecutionMode.ONCE
                ),
                phases=2 if position == 3 else 1,
                max_executions=4 if position == 4 else None,
                cycle_multiplier_cap=Decimal("4") if position == 4 else None,
            )
            for position, threshold in enumerate(thresholds)
        ),
    )


def default_strategy(plan_id: UUID, version: int = 1) -> Strategy:
    return strategy_from_template(plan_id, "core", version)


def calculate_drawdown(current_price: Decimal, completed_closes: list[Decimal]) -> tuple[Decimal, Decimal]:
    if current_price <= 0:
        raise ValueError("current_price must be greater than 0")
    valid = [value for value in completed_closes if value > 0]
    if not valid:
        raise ValueError("at least one valid completed close is required")
    highest = max(valid)
    raw = (highest - current_price) / highest * Decimal("100")
    return max(raw, Decimal("0")), highest


def rolling_high(history: list[DatedClose]) -> DatedClose:
    valid = [item for item in history if item.close > 0]
    if not valid:
        raise ValueError("at least one valid completed close is required")
    highest = max(item.close for item in valid)
    return max(
        (item for item in valid if item.close == highest),
        key=lambda item: item.trading_date,
    )


def drawdown_from_peak(current_price: Decimal, peak: Decimal) -> Decimal:
    if current_price <= 0 or peak <= 0:
        raise ValueError("current_price and peak must be greater than 0")
    return max((peak - current_price) / peak * Decimal("100"), Decimal("0"))


def take_profit_target(cost_basis: Decimal, profit_rate: Decimal) -> Decimal:
    if cost_basis <= 0:
        raise ValueError("cost_basis must be greater than 0")
    return cost_basis * (Decimal("1") + profit_rate / Decimal("100"))


def estimate_linked_fund_nav(
    previous_official_nav: Decimal,
    signal_previous_close: Decimal,
    signal_current_price: Decimal,
) -> tuple[Decimal, Decimal]:
    if min(previous_official_nav, signal_previous_close, signal_current_price) <= 0:
        raise ValueError("NAV and signal prices must be greater than 0")
    signal_return = signal_current_price / signal_previous_close - Decimal("1")
    return previous_official_nav * (Decimal("1") + signal_return), signal_return


def planned_sell_units(
    actual_units: Decimal, level: TakeProfitLevel
) -> Decimal:
    level.validate()
    if actual_units < 0:
        raise ValueError("actual_units cannot be negative")
    if level.sell_all:
        return actual_units
    return actual_units * level.sell_ratio / Decimal("100")


def next_recovery_level(
    drawdown: Decimal,
    levels: tuple[RecoveryLevel, ...],
    triggered_ids: set[UUID],
) -> RecoveryLevel | None:
    eligible = [
        level
        for level in levels
        if level.id not in triggered_ids
        and level.recurring_amount is not None
        and drawdown >= level.drawdown
    ]
    return max(eligible, key=lambda item: item.drawdown) if eligible else None


def choose_level(drawdown: Decimal, strategy: Strategy) -> StrategyLevel | None:
    strategy.validate()
    eligible = [
        level for level in strategy.levels if level.enabled and drawdown >= level.threshold
    ]
    return max(eligible, key=lambda item: item.threshold) if eligible else None


def planned_amount(base_amount: Decimal, level: StrategyLevel | None) -> Decimal:
    if level is None:
        return Decimal("0.00")
    value = base_amount * level.multiplier
    if level.amount_cap is not None:
        value = min(value, level.amount_cap)
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def evaluate(
    *, current_price: Decimal, completed_closes: list[Decimal], base_amount: Decimal, strategy: Strategy
) -> TriggerDecision:
    drawdown, highest = calculate_drawdown(current_price, completed_closes)
    level = choose_level(drawdown, strategy)
    return TriggerDecision(
        level=level,
        drawdown=drawdown,
        highest_close=highest,
        extra_amount=planned_amount(base_amount, level),
    )
