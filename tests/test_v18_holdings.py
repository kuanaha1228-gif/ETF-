import sqlite3
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from etf_assistant.db import Database
from etf_assistant.domain import Plan, Quote, TakeProfitLevel
from etf_assistant.providers.market import StaticMarketProvider, WeekdayCalendar
from etf_assistant.service import DailyCheckService
from etf_assistant.strategy import default_strategy


class V18HoldingTests(TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "test.sqlite")
        self.database.initialize()
        self.plan = Plan("联接基金", "019666", "159992", Decimal("100"))
        self.strategy = default_strategy(self.plan.id)
        self.database.add_plan_with_strategy(self.plan, self.strategy)
        self.database.save_take_profit_basis(
            self.plan.id,
            actual_units=Decimal("100"),
            cost_basis=Decimal("1"),
            lock=False,
        )
        self.today = date(2026, 7, 30)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _buy_event(self, value: date, key: str):
        return self.database.create_event(
            plan_id=self.plan.id,
            strategy_id=self.strategy.id,
            level_id=None,
            event_type="recurring",
            trading_date=value.isoformat(),
            week_key=f"{value.year}-W{value.isocalendar().week:02d}",
            quote_price=Decimal("1"),
            quote_time=datetime.combine(
                value, datetime.min.time(), tzinfo=timezone.utc
            ),
            daily_change=Decimal("0"),
            highest_close=Decimal("1"),
            drawdown=Decimal("0"),
            threshold_snapshot=None,
            multiplier_snapshot=None,
            execution_mode_snapshot=None,
            regular_amount=Decimal("100"),
            extra_amount=Decimal("0"),
            idempotency_key=key,
        )[0]

    def test_confirmed_investment_waits_for_exact_nav_then_updates_cost(self) -> None:
        event_id = self._buy_event(self.today, "buy-pending")
        status = self.database.confirm_investment(
            event_id,
            gross_amount=Decimal("100"),
            fee_amount=Decimal("1"),
            nav_date=self.today,
        )
        self.assertEqual(status, "pending_nav")
        self.assertEqual(self.database.current_position(self.plan.id)["actual_units"], "100")

        self.database.record_official_nav(
            self.plan.id,
            nav_date=self.today - timedelta(days=1),
            official_nav=Decimal("2"),
        )
        self.assertEqual(
            self.database.holding_transactions(self.plan.id)[0]["status"],
            "pending_nav",
        )

        affected = self.database.record_official_nav(
            self.plan.id,
            nav_date=self.today,
            official_nav=Decimal("2"),
            daily_change=Decimal("1.08"),
            source="eastmoney",
        )
        self.assertEqual(affected, 1)
        position = self.database.current_position(self.plan.id)
        self.assertEqual(Decimal(position["actual_units"]), Decimal("149.5"))
        self.assertEqual(Decimal(position["total_cost"]), Decimal("200"))
        self.assertEqual(
            Decimal(position["average_cost"]),
            Decimal("200") / Decimal("149.5"),
        )
        with self.assertRaisesRegex(ValueError, "不能重复"):
            self.database.confirm_investment(
                event_id,
                gross_amount=Decimal("100"),
                fee_amount=Decimal("1"),
                nav_date=self.today,
            )

    def test_take_profit_uses_average_cost_after_confirmed_investment(self) -> None:
        self.database.replace_take_profit_rules(
            self.plan.id,
            (TakeProfitLevel(Decimal("20"), Decimal("30"), Decimal("50")),),
            (),
        )
        self.database.record_official_nav(
            self.plan.id,
            nav_date=self.today,
            official_nav=Decimal("2"),
        )
        event_id = self._buy_event(self.today, "buy-settled")
        self.assertEqual(
            self.database.confirm_investment(
                event_id,
                gross_amount=Decimal("100"),
                fee_amount=Decimal("0"),
                nav_date=self.today,
            ),
            "settled",
        )
        now = datetime(2026, 7, 31, 14, 50, tzinfo=timezone.utc)
        market = StaticMarketProvider(
            {
                self.plan.signal_code: Quote(
                    self.plan.signal_code, Decimal("1.6"), now
                )
            },
            {self.plan.signal_code: [Decimal("2")] * 20},
        )
        result = DailyCheckService(
            self.database, market, WeekdayCalendar(), []
        ).run(now)
        self.assertEqual(len(result.created_events), 1)
        event = self.database.event_row(result.created_events[0])
        position = self.database.current_position(self.plan.id)
        self.assertEqual(event["event_type"], "take_profit")
        self.assertEqual(
            Decimal(event["take_profit_cost_basis"]),
            Decimal(position["average_cost"]),
        )
        self.assertEqual(Decimal(event["planned_sell_units"]), Decimal("45"))

    def test_schema_seven_migrates_locked_holdings_without_loss(self) -> None:
        path = Path(self.temp.name) / "schema7.sqlite"
        legacy = Database(path)
        legacy.initialize()
        plan = Plan("旧基金", "012345", "159999", Decimal("300"))
        legacy.add_plan_with_strategy(plan, default_strategy(plan.id))
        legacy.save_take_profit_basis(
            plan.id,
            actual_units=Decimal("88.125"),
            cost_basis=Decimal("1.2345"),
            lock=False,
        )
        connection = sqlite3.connect(path)
        connection.execute("DROP TABLE portfolio_positions")
        connection.execute("DROP TABLE holding_transactions")
        connection.execute("PRAGMA user_version = 7")
        connection.commit()
        connection.close()
        legacy.initialize()
        position = legacy.current_position(plan.id)
        self.assertEqual(position["actual_units"], "88.125")
        self.assertEqual(position["average_cost"], "1.2345")
        self.assertEqual(
            Decimal(position["total_cost"]),
            Decimal("88.125") * Decimal("1.2345"),
        )
