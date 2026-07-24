import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from etf_assistant.db import Database
from etf_assistant.domain import (
    EffectiveMode,
    ExecutionMode,
    Plan,
    Quote,
    Strategy,
    StrategyLevel,
)
from etf_assistant.providers.market import StaticMarketProvider, WeekdayCalendar
from etf_assistant.providers.notify import RecordingNotifier
from etf_assistant.service import DailyCheckService


class DailyCheckTests(TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "test.sqlite")
        self.database.initialize()
        self.plan = Plan(
            "恒生科技", "012349", "513180", Decimal("300"), invest_weekday=0
        )
        self.strategy = Strategy(
            plan_id=self.plan.id,
            version=1,
            levels=(StrategyLevel(Decimal("12"), Decimal("1.5")),),
        )
        self.database.add_plan_with_strategy(self.plan, self.strategy)
        self.now = datetime(2026, 7, 20, 14, 50, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _service(self, notifier: RecordingNotifier) -> DailyCheckService:
        market = StaticMarketProvider(
            quotes={
                "513180": Quote("513180", Decimal("85"), self.now, Decimal("-2.1"))
            },
            closes={"513180": [Decimal("100")] * 20},
        )
        return DailyCheckService(self.database, market, WeekdayCalendar(), [notifier])

    def test_notification_delivery_advances_without_execution_feedback(self) -> None:
        messages: list[tuple[str, str]] = []
        result = self._service(RecordingNotifier("email", messages)).run(self.now)
        self.assertEqual(len(result.created_events), 1)
        row = self.database.event_row(result.created_events[0])
        self.assertEqual(row["state"], "notified")
        self.assertEqual(row["execution_status"], "unknown")
        self.assertEqual(row["extra_amount"], "450.00")
        self.assertEqual(len(messages), 1)
        snapshot = self.database.market_snapshots()[0]
        self.assertEqual(snapshot["quote_price"], "85")
        self.assertEqual(snapshot["highest_close"], "100")

    def test_repeated_run_does_not_send_duplicate_message(self) -> None:
        messages: list[tuple[str, str]] = []
        service = self._service(RecordingNotifier("email", messages))
        service.run(self.now)
        service.run(self.now)
        self.assertEqual(len(messages), 1)
        self.assertEqual(self.database.count_rows()["events"], 1)

    def test_deeper_level_same_week_sends_risk_alert_without_duplicate_investment(self) -> None:
        self.database.update_plan(
            Plan(
                id=self.plan.id,
                name=self.plan.name,
                purchase_code=self.plan.purchase_code,
                signal_code=self.plan.signal_code,
                base_amount=self.plan.base_amount,
                invest_weekday=self.plan.invest_weekday,
                recurring_enabled=False,
            )
        )
        self.database.save_strategy(
            Strategy(
                plan_id=self.plan.id,
                version=2,
                effective_mode=EffectiveMode.IMMEDIATE,
                levels=(
                    StrategyLevel(Decimal("12"), Decimal("1")),
                    StrategyLevel(Decimal("15"), Decimal("2")),
                ),
            )
        )
        messages: list[tuple[str, str]] = []
        first_market = StaticMarketProvider(
            quotes={"513180": Quote("513180", Decimal("87"), self.now)},
            closes={"513180": [Decimal("100")] * 20},
        )
        first = DailyCheckService(
            self.database,
            first_market,
            WeekdayCalendar(),
            [RecordingNotifier("email", messages)],
        ).run(self.now)
        self.assertEqual(
            self.database.event_row(first.created_events[0])["extra_amount"],
            "300.00",
        )

        next_day = self.now + timedelta(days=1)
        deeper_market = StaticMarketProvider(
            quotes={"513180": Quote("513180", Decimal("84"), next_day)},
            closes={"513180": [Decimal("100")] * 20},
        )
        service = DailyCheckService(
            self.database,
            deeper_market,
            WeekdayCalendar(),
            [RecordingNotifier("email", messages)],
        )
        deeper = service.run(next_day)
        row = self.database.event_row(deeper.created_events[0])
        self.assertEqual(row["state"], "suppressed")
        self.assertEqual(row["extra_amount"], "0.00")
        self.assertIn("本周已经发送过一次补仓计划", messages[-1][1])
        self.assertEqual(len(messages), 2)

        service.run(next_day)
        self.assertEqual(len(messages), 2)

    def test_all_delivery_failures_do_not_mark_notified(self) -> None:
        result = self._service(RecordingNotifier("email", [], should_fail=True)).run(self.now)
        row = self.database.event_row(result.created_events[0])
        self.assertEqual(row["state"], "delivery_failed")

    def test_latest_quote_is_saved_when_history_request_fails(self) -> None:
        market = StaticMarketProvider(
            quotes={"513180": Quote("513180", Decimal("86"), self.now, Decimal("1.2"))},
            closes={},
        )
        result = DailyCheckService(
            self.database, market, WeekdayCalendar(), [RecordingNotifier("email", [])]
        ).run(self.now)
        self.assertEqual(len(result.errors), 1)
        snapshot = self.database.market_snapshots()[0]
        self.assertEqual(snapshot["quote_price"], "86")
        self.assertIsNone(snapshot["drawdown"])

    def test_only_failed_channel_is_retried(self) -> None:
        email_messages: list[tuple[str, str]] = []
        desktop_messages: list[tuple[str, str]] = []
        service = self._service(RecordingNotifier("email", email_messages))
        service.notifiers.append(RecordingNotifier("desktop", desktop_messages, should_fail=True))
        result = service.run(self.now)
        event_id = result.created_events[0]
        self.assertEqual(self.database.event_row(event_id)["state"], "notified")

        service.notifiers = [
            RecordingNotifier("email", email_messages),
            RecordingNotifier("desktop", desktop_messages),
        ]
        service.run(self.now)
        self.assertEqual(len(email_messages), 1)
        self.assertEqual(len(desktop_messages), 1)
        self.assertEqual(self.database.delivery_states(event_id)["desktop"].value, "sent")

    def test_immediate_strategy_does_not_replay_same_threshold(self) -> None:
        first = self._service(RecordingNotifier("email", [])).run(self.now)
        self.assertEqual(len(first.created_events), 1)
        changed = Strategy(
            plan_id=self.plan.id,
            version=2,
            levels=(
                StrategyLevel(Decimal("10"), Decimal("2")),
                StrategyLevel(Decimal("12"), Decimal("3")),
            ),
            effective_mode=EffectiveMode.IMMEDIATE,
        )
        self.database.save_strategy(changed)
        next_week = self.now.replace(day=27)
        market = StaticMarketProvider(
            quotes={"513180": Quote("513180", Decimal("85"), next_week)},
            closes={"513180": [Decimal("100")] * 20},
        )
        result = DailyCheckService(
            self.database, market, WeekdayCalendar(), [RecordingNotifier("email", [])]
        ).run(next_week)
        self.assertEqual(len(result.created_events), 1)
        row = self.database.event_row(result.created_events[0])
        self.assertEqual(row["event_type"], "recurring")
        self.assertEqual(row["extra_amount"], "0.00")

    def test_active_cycle_uses_fixed_peak_when_rolling_high_moves_down(self) -> None:
        self._service(RecordingNotifier("email", [])).run(self.now)
        next_week = self.now + timedelta(days=7)
        market = StaticMarketProvider(
            quotes={"513180": Quote("513180", Decimal("82"), next_week)},
            closes={"513180": [Decimal("90")] * 20},
        )
        DailyCheckService(
            self.database, market, WeekdayCalendar(), [RecordingNotifier("email", [])]
        ).run(next_week)

        snapshot = self.database.market_snapshots()[0]
        self.assertEqual(Decimal(snapshot["highest_close"]), Decimal("90"))
        self.assertAlmostEqual(float(snapshot["drawdown"]), 8.888888, places=5)
        self.assertEqual(Decimal(snapshot["cycle_peak"]), Decimal("100"))
        self.assertEqual(Decimal(snapshot["cycle_drawdown"]), Decimal("18.00"))

    def test_continuous_level_pauses_after_four_weeks_until_user_resumes(self) -> None:
        self.database.update_plan(
            Plan(
                id=self.plan.id,
                name=self.plan.name,
                purchase_code=self.plan.purchase_code,
                signal_code=self.plan.signal_code,
                base_amount=self.plan.base_amount,
                invest_weekday=self.plan.invest_weekday,
                recurring_enabled=False,
            )
        )
        limited = Strategy(
            plan_id=self.plan.id,
            version=2,
            effective_mode=EffectiveMode.IMMEDIATE,
            levels=(
                StrategyLevel(
                    Decimal("20"),
                    Decimal("1"),
                    execution_mode=ExecutionMode.WEEKLY_WHILE_DEEP,
                    max_executions=4,
                    cycle_multiplier_cap=Decimal("4"),
                ),
            ),
        )
        self.database.save_strategy(limited)
        messages: list[tuple[str, str]] = []

        for week in range(4):
            now = self.now + timedelta(days=week * 7)
            market = StaticMarketProvider(
                quotes={"513180": Quote("513180", Decimal("75"), now)},
                closes={"513180": [Decimal("100")] * 20},
            )
            DailyCheckService(
                self.database, market, WeekdayCalendar(),
                [RecordingNotifier("email", messages)],
            ).run(now)

        cycle = self.database.get_active_cycle(self.plan.id)
        self.assertTrue(cycle["paused_for_review"])
        self.assertEqual(len(messages), 4)

        blocked_date = self.now + timedelta(days=28)
        blocked_market = StaticMarketProvider(
            quotes={"513180": Quote("513180", Decimal("70"), blocked_date)},
            closes={"513180": [Decimal("100")] * 20},
        )
        blocked = DailyCheckService(
            self.database, blocked_market, WeekdayCalendar(),
            [RecordingNotifier("email", messages)],
        ).run(blocked_date)
        self.assertEqual(len(blocked.created_events), 0)
        self.assertEqual(len(messages), 4)

        self.database.resume_cycle(self.plan.id)
        resumed_date = self.now + timedelta(days=35)
        resumed_market = StaticMarketProvider(
            quotes={"513180": Quote("513180", Decimal("70"), resumed_date)},
            closes={"513180": [Decimal("100")] * 20},
        )
        resumed = DailyCheckService(
            self.database, resumed_market, WeekdayCalendar(),
            [RecordingNotifier("email", messages)],
        ).run(resumed_date)
        self.assertEqual(len(resumed.created_events), 1)
        self.assertEqual(len(messages), 5)
