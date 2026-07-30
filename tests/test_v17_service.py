import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase
from uuid import UUID

from etf_assistant.db import Database
from etf_assistant.domain import (
    Plan,
    Quote,
    RecoveryLevel,
    Strategy,
    StrategyLevel,
    TakeProfitLevel,
)
from etf_assistant.providers.market import StaticMarketProvider, WeekdayCalendar
from etf_assistant.providers.notify import RecordingNotifier
from etf_assistant.service import DailyCheckService
from etf_assistant.strategy import default_strategy


class V17ServiceTests(TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "test.sqlite")
        self.database.initialize()
        self.now = datetime(2026, 7, 20, 14, 50, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _market(
        self, symbol: str, price: str, now: datetime, high: str = "1"
    ) -> StaticMarketProvider:
        return StaticMarketProvider(
            {symbol: Quote(symbol, Decimal(price), now)},
            {symbol: [Decimal(high)] * 20},
        )

    def _a500(self) -> Plan:
        plan = Plan(
            "A500", "022459", "159361", Decimal("300"), invest_weekday=0
        )
        self.database.add_plan_with_strategy(plan, default_strategy(plan.id))
        self.database.replace_take_profit_rules(
            plan.id,
            (
                TakeProfitLevel(Decimal("20"), Decimal("30"), Decimal("200")),
                TakeProfitLevel(
                    Decimal("40"), Decimal("100"), Decimal("100"), sell_all=True
                ),
            ),
            (
                RecoveryLevel(Decimal("10"), Decimal("200")),
                RecoveryLevel(Decimal("20"), Decimal("300")),
            ),
        )
        self.database.save_take_profit_basis(
            plan.id,
            actual_units=Decimal("1000"),
            cost_basis=Decimal("1"),
            lock=True,
            previous_official_nav=Decimal("1"),
            previous_official_nav_date=self.now.date() - timedelta(days=1),
        )
        return plan

    def test_take_profit_notification_does_not_mutate_until_execution(self) -> None:
        plan = self._a500()
        messages: list[tuple[str, str]] = []
        result = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "1.2", self.now),
            WeekdayCalendar(),
            [RecordingNotifier("email", messages)],
        ).run(self.now)
        event = self.database.event_row(result.created_events[0])
        cycle = self.database.current_take_profit_cycle(plan.id)
        self.assertEqual(event["planned_sell_units"], "300")
        self.assertEqual(event["execution_status"], "unknown")
        self.assertEqual(cycle["actual_units"], "1000")
        self.assertIsNone(cycle["take_profit_peak"])
        self.assertEqual(self.database.get_plan(plan.id).current_amount, Decimal("300"))

        self.database.save_take_profit_basis(
            plan.id,
            actual_units=Decimal("1100"),
            cost_basis=Decimal("1"),
            lock=False,
        )
        next_day = self.now + timedelta(days=1)
        repeated = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "1.21", next_day),
            WeekdayCalendar(),
            [RecordingNotifier("email", messages)],
        ).run(next_day)
        repeated_event = self.database.event_row(repeated.created_events[0])
        self.assertEqual(repeated_event["planned_sell_units"], "330")
        self.database.confirm_take_profit_execution(
            repeated.created_events[0], actual_sell_units=Decimal("330")
        )
        cycle = self.database.current_take_profit_cycle(plan.id)
        self.assertEqual(cycle["actual_units"], "770")
        self.assertEqual(cycle["take_profit_peak"], "1.21")
        self.assertEqual(self.database.get_plan(plan.id).current_amount, Decimal("200"))

    def test_linked_fund_take_profit_is_estimated_then_reconciled(self) -> None:
        plan = Plan("Linked", "012345", "159999", Decimal("300"))
        self.database.add_plan_with_strategy(plan, default_strategy(plan.id))
        self.database.replace_take_profit_rules(
            plan.id,
            (TakeProfitLevel(Decimal("2"), Decimal("30"), Decimal("150")),),
            (),
        )
        self.database.save_take_profit_basis(
            plan.id,
            actual_units=Decimal("123.456789"),
            cost_basis=Decimal("1"),
            lock=True,
            previous_official_nav=Decimal("1"),
            previous_official_nav_date=self.now.date() - timedelta(days=1),
        )
        market = StaticMarketProvider(
            {plan.signal_code: Quote(plan.signal_code, Decimal("1.224"), self.now)},
            {plan.signal_code: [Decimal("1.2")] * 20},
        )
        messages: list[tuple[str, str]] = []
        result = DailyCheckService(
            self.database, market, WeekdayCalendar(),
            [RecordingNotifier("email", messages)],
        ).run(self.now)
        self.assertEqual(len(result.created_events), 1)
        row = self.database.event_row(result.created_events[0])
        self.assertEqual(row["valuation_status"], "estimated_warning")
        self.assertEqual(Decimal(row["estimated_nav"]), Decimal("1.020"))
        self.assertEqual(row["quote_price"], "1.224")
        self.assertEqual(row["take_profit_target_price"], "1.02")
        self.assertIn("上一期官方净值 1", messages[0][1])
        self.assertIn("场内昨收 1.2", messages[0][1])
        self.assertIn("场外估算净值 1.02", messages[0][1])
        self.assertIn("估值状态 estimated_warning", messages[0][1])

        reconciled = self.database.record_official_nav(
            plan.id, nav_date=self.now.date(), official_nav=Decimal("1.019")
        )
        self.assertEqual(reconciled, 1)
        row = self.database.event_row(result.created_events[0])
        self.assertEqual(row["valuation_status"], "nav_not_reached")
        self.assertEqual(row["execution_status"], "unknown")

        self.database.record_official_nav(
            plan.id, nav_date=self.now.date(), official_nav=Decimal("1.020")
        )
        row = self.database.event_row(result.created_events[0])
        self.assertEqual(row["valuation_status"], "nav_confirmed")
        self.assertEqual(len(self.database.events_for_plan_date(plan.id, self.now.date())), 1)

    def test_legacy_locked_basis_without_official_nav_does_not_trigger(self) -> None:
        plan = Plan(
            "Legacy linked", "012346", "159998", Decimal("300"),
            recurring_enabled=False, drawdown_enabled=False,
        )
        self.database.add_plan_with_strategy(plan, default_strategy(plan.id))
        self.database.replace_take_profit_rules(
            plan.id,
            (TakeProfitLevel(Decimal("20"), Decimal("30"), Decimal("150")),),
            (),
        )
        self.database.save_take_profit_basis(
            plan.id, actual_units=Decimal("100"), cost_basis=Decimal("1"), lock=False
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE take_profit_cycles
                SET basis_status = 'locked', basis_review_status = 'needs_review'
                WHERE plan_id = ?
                """,
                (str(plan.id),),
            )
        result = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "9", self.now, high="1"),
            WeekdayCalendar(),
            [RecordingNotifier("email", [])],
        ).run(self.now)
        self.assertEqual(result.created_events, ())

    def test_first_take_profit_closes_drawdown_cycle_and_recovery_uses_only_tp_peak(self) -> None:
        plan = self._a500()
        strategy = self.database.get_active_strategy(plan.id)
        drawdown_cycle_id = self.database.get_or_create_active_cycle(
            plan.id, strategy.id, Decimal("1.5")
        )
        first_market = StaticMarketProvider(
            {plan.signal_code: Quote(plan.signal_code, Decimal("1.2"), self.now)},
            {plan.signal_code: [Decimal("2")] * 19 + [Decimal("1")]},
        )
        first = DailyCheckService(
            self.database,
            first_market,
            WeekdayCalendar(),
            [RecordingNotifier("email", [])],
        ).run(self.now)
        self.database.confirm_take_profit_execution(
            first.created_events[0], actual_sell_units=Decimal("300")
        )
        with self.database.read() as connection:
            state = connection.execute(
                "SELECT state FROM drawdown_cycles WHERE id = ?",
                (str(drawdown_cycle_id),),
            ).fetchone()[0]
        self.assertEqual(state, "closed_by_take_profit")

        new_high_day = self.now + timedelta(days=1)
        DailyCheckService(
            self.database,
            self._market(plan.signal_code, "1.3", new_high_day, high="2"),
            WeekdayCalendar(),
            [RecordingNotifier("email", [])],
        ).run(new_high_day)
        recovery_day = self.now + timedelta(days=2)
        recovered = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "1.04", recovery_day, high="2"),
            WeekdayCalendar(),
            [RecordingNotifier("email", [])],
        ).run(recovery_day)
        self.assertEqual(len(recovered.created_events), 1)
        event = self.database.event_row(recovered.created_events[0])
        self.assertEqual(event["event_type"], "recovery")
        self.assertEqual(event["decision_peak_type"], "take_profit_peak")
        self.assertEqual(Decimal(event["decision_peak_price"]), Decimal("1.3"))
        self.assertEqual(event["recurring_amount_after"], "300")
        self.assertEqual(self.database.get_plan(plan.id).current_amount, Decimal("300"))
        self.assertIsNone(self.database.get_active_cycle(plan.id))

        cycle = self.database.current_take_profit_cycle(plan.id)
        executed = self.database.executed_take_profit_level_ids(
            UUID(cycle["id"])
        )
        self.assertEqual(len(executed), 1)
        self.assertEqual(cycle["state"], "active")

    def test_final_take_profit_clears_holdings_then_recovery_prepares_new_draft(self) -> None:
        plan = self._a500()
        first = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "1.2", self.now),
            WeekdayCalendar(),
            [RecordingNotifier("email", [])],
        ).run(self.now)
        self.database.confirm_take_profit_execution(
            first.created_events[0], actual_sell_units=Decimal("300")
        )
        self.database.save_take_profit_basis(
            plan.id,
            actual_units=Decimal("800"),
            cost_basis=Decimal("1"),
            lock=False,
        )
        final_day = self.now + timedelta(days=1)
        final = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "1.4", final_day),
            WeekdayCalendar(),
            [RecordingNotifier("email", [])],
        ).run(final_day)
        final_event = self.database.event_row(final.created_events[0])
        self.assertEqual(final_event["planned_sell_units"], "800")
        self.database.confirm_take_profit_execution(
            final.created_events[0], actual_sell_units=Decimal("800")
        )
        cycle = self.database.current_take_profit_cycle(plan.id)
        self.assertEqual(cycle["actual_units"], "0")
        self.assertEqual(cycle["basis_status"], "superseded")
        self.assertEqual(cycle["state"], "recovering")
        self.assertEqual(self.database.get_plan(plan.id).current_amount, Decimal("100"))

        ten_day = self.now + timedelta(days=2)
        ten = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "1.26", ten_day),
            WeekdayCalendar(),
            [RecordingNotifier("email", [])],
        ).run(ten_day)
        self.assertEqual(
            self.database.event_row(ten.created_events[0])["recurring_amount_after"],
            "200",
        )
        twenty_day = self.now + timedelta(days=3)
        twenty = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "1.12", twenty_day),
            WeekdayCalendar(),
            [RecordingNotifier("email", [])],
        ).run(twenty_day)
        self.assertEqual(
            self.database.event_row(twenty.created_events[0])["recurring_amount_after"],
            "300",
        )
        new_cycle = self.database.current_take_profit_cycle(plan.id)
        self.assertEqual(new_cycle["basis_status"], "draft")
        self.assertIsNone(new_cycle["cost_basis"])

    def test_fixed_recurring_is_not_blocked_by_same_week_drawdown(self) -> None:
        plan = Plan(
            "Core",
            "012349",
            "513180",
            Decimal("300"),
            invest_weekday=1,
            recurring_enabled=True,
        )
        strategy = Strategy(
            plan.id,
            1,
            (StrategyLevel(Decimal("10"), Decimal("1")),),
        )
        self.database.add_plan_with_strategy(plan, strategy)
        first = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "0.85", self.now),
            WeekdayCalendar(),
            [RecordingNotifier("email", [])],
        ).run(self.now)
        self.assertEqual(
            self.database.event_row(first.created_events[0])["extra_amount"], "300.00"
        )
        next_day = self.now + timedelta(days=1)
        second = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "0.85", next_day),
            WeekdayCalendar(),
            [RecordingNotifier("email", [])],
        ).run(next_day)
        row = self.database.event_row(second.created_events[0])
        self.assertEqual(row["event_type"], "recurring")
        self.assertEqual(row["regular_amount"], "300")
        self.assertEqual(row["extra_amount"], "0.00")

    def test_manual_refresh_does_not_create_or_suppress_scheduled_events(self) -> None:
        plan = Plan(
            "Core",
            "012349",
            "513180",
            Decimal("300"),
            invest_weekday=0,
        )
        strategy = Strategy(
            plan.id,
            1,
            (StrategyLevel(Decimal("10"), Decimal("1")),),
        )
        self.database.add_plan_with_strategy(plan, strategy)
        messages: list[tuple[str, str]] = []
        service = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "0.85", self.now),
            WeekdayCalendar(),
            [RecordingNotifier("desktop", messages)],
        )

        manual = service.run(self.now, scheduled=False)

        self.assertEqual(manual.created_events, ())
        self.assertEqual(self.database.count_rows()["events"], 0)
        self.assertIsNone(self.database.get_active_cycle(plan.id))
        self.assertEqual(messages, [])
        snapshot = self.database.market_snapshots()[0]
        self.assertEqual(snapshot["quote_price"], "0.85")
        self.assertEqual(snapshot["drawdown"], "15.00")

        scheduled = service.run(self.now, scheduled=True)

        self.assertEqual(len(scheduled.created_events), 1)
        event = self.database.event_row(scheduled.created_events[0])
        self.assertEqual(event["event_type"], "combined")
        self.assertEqual(event["regular_amount"], "300")
        self.assertEqual(event["extra_amount"], "300.00")
        self.assertEqual(len(messages), 1)

    def test_manual_refresh_does_not_advance_take_profit_state(self) -> None:
        plan = self._a500()
        first = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "1.2", self.now),
            WeekdayCalendar(),
            [],
        ).run(self.now)
        self.database.confirm_take_profit_execution(
            first.created_events[0], actual_sell_units=Decimal("300")
        )
        cycle = self.database.current_take_profit_cycle(plan.id)
        self.assertEqual(cycle["take_profit_peak"], "1.2")

        next_day = self.now + timedelta(days=1)
        manual = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "1.3", next_day),
            WeekdayCalendar(),
            [RecordingNotifier("desktop", [])],
        ).run(next_day, scheduled=False)

        self.assertEqual(manual.created_events, ())
        cycle = self.database.current_take_profit_cycle(plan.id)
        self.assertEqual(cycle["take_profit_peak"], "1.2")
        self.assertEqual(self.database.get_plan(plan.id).current_amount, Decimal("200"))

        DailyCheckService(
            self.database,
            self._market(plan.signal_code, "1.3", next_day),
            WeekdayCalendar(),
            [],
        ).run(next_day, scheduled=True)
        cycle = self.database.current_take_profit_cycle(plan.id)
        self.assertEqual(cycle["take_profit_peak"], "1.3")

    def test_manual_checks_do_not_accumulate_cycle_recovery(self) -> None:
        plan = Plan("Core", "012349", "513180", Decimal("300"))
        strategy = Strategy(
            plan.id,
            1,
            (StrategyLevel(Decimal("10"), Decimal("1")),),
        )
        self.database.add_plan_with_strategy(plan, strategy)
        self.database.get_or_create_active_cycle(
            plan.id, strategy.id, Decimal("1")
        )
        for offset in (0, 1):
            now = self.now + timedelta(days=offset)
            DailyCheckService(
                self.database,
                self._market(plan.signal_code, "0.99", now),
                WeekdayCalendar(),
                [],
            ).run(now, scheduled=False)
        self.assertIsNotNone(self.database.get_active_cycle(plan.id))
        for offset in (2, 3):
            now = self.now + timedelta(days=offset)
            DailyCheckService(
                self.database,
                self._market(plan.signal_code, "0.99", now),
                WeekdayCalendar(),
                [],
            ).run(now, scheduled=True)
        self.assertIsNone(self.database.get_active_cycle(plan.id))

    def test_daily_email_is_zero_event_idempotent_and_manual_checks_do_not_send(self) -> None:
        plan = Plan(
            "Quiet",
            "012349",
            "513180",
            Decimal("300"),
            recurring_enabled=False,
            drawdown_enabled=False,
        )
        self.database.add_plan_with_strategy(plan, default_strategy(plan.id))
        messages: list[tuple[str, str]] = []
        service = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "1", self.now),
            WeekdayCalendar(),
            [RecordingNotifier("email", messages)],
        )
        first = service.run(self.now)
        service.run(self.now)
        service.run(self.now, scheduled=False)
        self.assertEqual(len(first.created_events), 0)
        self.assertEqual(len(messages), 1)
        self.assertIn("今日未触发定投、补仓或止盈", messages[0][1])
        self.assertIn("下一止盈价：未设置", messages[0][1])
        self.assertEqual(self.database.count_rows()["daily_summaries"], 1)

    def test_failed_daily_email_retries_the_frozen_snapshot(self) -> None:
        plan = Plan(
            "Quiet",
            "012349",
            "513180",
            Decimal("300"),
            recurring_enabled=False,
            drawdown_enabled=False,
        )
        self.database.add_plan_with_strategy(plan, default_strategy(plan.id))
        service = DailyCheckService(
            self.database,
            self._market(plan.signal_code, "1.00", self.now),
            WeekdayCalendar(),
            [RecordingNotifier("email", [], should_fail=True)],
        )
        failed = service.run(self.now)
        self.assertTrue(failed.errors)
        messages: list[tuple[str, str]] = []
        service.market = self._market(plan.signal_code, "9.99", self.now)
        service.notifiers = [RecordingNotifier("email", messages)]
        service.run(self.now)
        self.assertEqual(len(messages), 1)
        self.assertIn("最新价：1.00", messages[0][1])
        self.assertNotIn("9.99", messages[0][1])
        row = self.database.daily_summary_row(failed.summary_id)
        self.assertEqual(row["state"], "daily_summary_sent")
        self.assertEqual(row["attempts"], 2)
