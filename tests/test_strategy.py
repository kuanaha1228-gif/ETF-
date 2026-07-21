from decimal import Decimal
from unittest import TestCase
from uuid import uuid4

from etf_assistant.domain import ExecutionMode, Strategy, StrategyLevel
from etf_assistant.strategy import (
    STRATEGY_TEMPLATES,
    calculate_drawdown,
    choose_level,
    default_strategy,
    evaluate,
    strategy_from_template,
)


class StrategyTests(TestCase):
    def test_default_strategy_matches_prd(self) -> None:
        strategy = default_strategy(uuid4())
        self.assertEqual(
            [(level.threshold, level.multiplier) for level in strategy.levels],
            [
                (Decimal("4"), Decimal("1")),
                (Decimal("7"), Decimal("1")),
                (Decimal("10"), Decimal("2")),
                (Decimal("15"), Decimal("1")),
                (Decimal("22"), Decimal("1")),
            ],
        )
        self.assertEqual(strategy.levels[3].execution_mode, ExecutionMode.PHASED)
        self.assertEqual(strategy.levels[4].execution_mode, ExecutionMode.WEEKLY_WHILE_DEEP)
        self.assertEqual(strategy.levels[4].max_executions, 4)
        self.assertEqual(strategy.levels[4].cycle_multiplier_cap, Decimal("4"))

    def test_all_initialization_templates_match_product_rules(self) -> None:
        expected = {
            "stable": ("3", "5", "8", "12", "18"),
            "core": ("4", "7", "10", "15", "22"),
            "growth": ("5", "9", "14", "20", "28"),
            "sector": ("6", "11", "17", "25", "35"),
        }
        self.assertEqual(set(STRATEGY_TEMPLATES), set(expected))
        for name, thresholds in expected.items():
            strategy = strategy_from_template(uuid4(), name)
            self.assertEqual(
                tuple(str(level.threshold) for level in strategy.levels), thresholds
            )
            self.assertEqual(strategy.levels[-1].max_executions, 4)

    def test_drawdown_uses_unrounded_value(self) -> None:
        drawdown, highest = calculate_drawdown(Decimal("95.004"), [Decimal("100")])
        self.assertEqual(highest, Decimal("100"))
        self.assertEqual(drawdown, Decimal("4.99600"))

    def test_custom_high_volatility_levels(self) -> None:
        plan_id = uuid4()
        strategy = Strategy(
            plan_id=plan_id,
            version=1,
            levels=(
                StrategyLevel(Decimal("12"), Decimal("1.5")),
                StrategyLevel(Decimal("20"), Decimal("2.25")),
            ),
        )
        decision = evaluate(
            current_price=Decimal("79"),
            completed_closes=[Decimal("100")],
            base_amount=Decimal("300"),
            strategy=strategy,
        )
        self.assertEqual(decision.level.threshold, Decimal("20"))
        self.assertEqual(decision.extra_amount, Decimal("675.00"))

    def test_fast_cross_selects_only_highest_level(self) -> None:
        strategy = default_strategy(uuid4())
        self.assertEqual(choose_level(Decimal("10.1"), strategy).threshold, Decimal("10"))

    def test_duplicate_enabled_threshold_rejected(self) -> None:
        strategy = Strategy(
            plan_id=uuid4(),
            version=1,
            levels=(
                StrategyLevel(Decimal("5"), Decimal("1")),
                StrategyLevel(Decimal("5"), Decimal("2")),
            ),
        )
        with self.assertRaises(ValueError):
            strategy.validate()
