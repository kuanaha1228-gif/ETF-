from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from .domain import ExecutionMode, Strategy, StrategyLevel, TriggerDecision


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
