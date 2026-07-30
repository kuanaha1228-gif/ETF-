from datetime import date
from decimal import Decimal
from unittest import TestCase

from etf_assistant.domain import (
    DatedClose,
    RecoveryLevel,
    Strategy,
    StrategyLevel,
    TakeProfitLevel,
)
from etf_assistant.strategy import (
    drawdown_from_peak,
    next_recovery_level,
    planned_sell_units,
    estimate_linked_fund_nav,
    rolling_high,
    take_profit_target,
)


class V17StrategyTests(TestCase):
    def test_linked_fund_nav_uses_signal_return_not_signal_price(self) -> None:
        estimated, signal_return = estimate_linked_fund_nav(
            Decimal("1.000"), Decimal("1.200"), Decimal("1.224")
        )
        self.assertEqual(estimated, Decimal("1.02000"))
        self.assertEqual(signal_return, Decimal("0.02"))

    def test_duplicate_drawdown_level_error_is_actionable(self) -> None:
        strategy = Strategy(
            plan_id=StrategyLevel(Decimal("5"), Decimal("1")).id,
            version=1,
            levels=(
                StrategyLevel(Decimal("5"), Decimal("1")),
                StrategyLevel(Decimal("5.00"), Decimal("2")),
            ),
        )
        with self.assertRaisesRegex(ValueError, "回撤档位 5% 重复，请修改或删除"):
            strategy.validate()

    def test_equal_rolling_high_uses_latest_trading_date(self) -> None:
        result = rolling_high(
            [
                DatedClose(date(2026, 7, 1), Decimal("1.234567")),
                DatedClose(date(2026, 7, 2), Decimal("1.100000")),
                DatedClose(date(2026, 7, 3), Decimal("1.234567")),
            ]
        )
        self.assertEqual(result.trading_date, date(2026, 7, 3))

    def test_take_profit_targets_keep_unrounded_precision(self) -> None:
        self.assertEqual(
            take_profit_target(Decimal("1.123456"), Decimal("20")),
            Decimal("1.3481472"),
        )
        self.assertLess(
            Decimal("1.3481471"),
            take_profit_target(Decimal("1.123456"), Decimal("20")),
        )

    def test_sell_ratio_uses_execution_time_holdings(self) -> None:
        level = TakeProfitLevel(
            Decimal("35"), Decimal("30"), Decimal("50")
        )
        self.assertEqual(
            planned_sell_units(Decimal("770.25"), level),
            Decimal("231.075"),
        )
        final = TakeProfitLevel(
            Decimal("50"), Decimal("100"), Decimal("0"), sell_all=True
        )
        self.assertEqual(planned_sell_units(Decimal("539.175"), final), Decimal("539.175"))

    def test_recovery_does_not_reapply_a_triggered_level(self) -> None:
        first = RecoveryLevel(Decimal("10"), Decimal("150"))
        second = RecoveryLevel(Decimal("20"), Decimal("300"))
        self.assertEqual(
            next_recovery_level(
                Decimal("21"), (first, second), {first.id}
            ),
            second,
        )
        self.assertEqual(drawdown_from_peak(Decimal("0.8"), Decimal("1")), Decimal("20.0"))
