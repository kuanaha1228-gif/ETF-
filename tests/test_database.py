import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from etf_assistant.db import Database
from etf_assistant.domain import (
    EffectiveMode,
    EventState,
    ExecutionStatus,
    Plan,
    Strategy,
    StrategyLevel,
)
from etf_assistant.strategy import default_strategy


class DatabaseTests(TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "test.sqlite")
        self.database.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_plan_and_custom_strategy_roundtrip(self) -> None:
        plan = Plan("恒生科技", "012349", "513180", Decimal("300"))
        strategy = Strategy(
            plan_id=plan.id,
            version=1,
            levels=(StrategyLevel(Decimal("12"), Decimal("1.5")),),
        )
        self.database.add_plan_with_strategy(plan, strategy)
        loaded = self.database.get_active_strategy(plan.id)
        self.assertEqual(loaded.levels[0].threshold, Decimal("12"))
        self.assertEqual(loaded.levels[0].multiplier, Decimal("1.5"))

    def test_market_snapshot_is_persisted_for_dashboard(self) -> None:
        plan = Plan("A500", "022459", "159361", Decimal("600"))
        self.database.add_plan_with_strategy(plan, default_strategy(plan.id))
        timestamp = datetime.now().astimezone()
        self.database.save_market_snapshot(
            plan_id=plan.id,
            quote_price=Decimal("1.234"),
            quote_time=timestamp,
            daily_change=Decimal("0.8"),
            highest_close=Decimal("1.300"),
            drawdown=Decimal("5.0769"),
        )
        row = self.database.market_snapshots()[0]
        self.assertEqual(row["quote_price"], "1.234")
        self.assertEqual(row["drawdown"], "5.0769")

    def test_schema_one_database_migrates_market_snapshots(self) -> None:
        with self.database.transaction() as connection:
            connection.execute("DROP TABLE market_snapshots")
            connection.execute("PRAGMA user_version = 1")
        self.database.initialize()
        self.assertIn("market_snapshots", self.database.count_rows())
        with self.database.read() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(drawdown_cycles)")
            }
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertIn("recovery_last_trading_date", columns)
        self.assertIn("cycle_peak", columns)
        self.assertIn("paused_for_review", columns)
        self.assertEqual(version, 5)

    def test_next_cycle_strategy_activates_after_recovery(self) -> None:
        plan = Plan("A500", "022459", "159361", Decimal("600"))
        initial = default_strategy(plan.id)
        self.database.add_plan_with_strategy(plan, initial)
        pending = Strategy(
            plan_id=plan.id,
            version=2,
            levels=(StrategyLevel(Decimal("6"), Decimal("1.25")),),
            effective_mode=EffectiveMode.NEXT_CYCLE,
        )
        self.database.save_strategy(pending)
        self.database.get_or_create_active_cycle(plan.id, initial.id)
        self.assertFalse(self.database.update_recovery(plan.id, True, date(2026, 7, 20)))
        self.assertFalse(self.database.update_recovery(plan.id, True, date(2026, 7, 20)))
        self.assertTrue(self.database.update_recovery(plan.id, True, date(2026, 7, 21)))
        self.assertEqual(self.database.get_active_strategy(plan.id).id, pending.id)

    def test_failed_recovery_check_restarts_distinct_day_count(self) -> None:
        plan = Plan("A500", "022459", "159361", Decimal("600"))
        strategy = default_strategy(plan.id)
        self.database.add_plan_with_strategy(plan, strategy)
        self.database.get_or_create_active_cycle(plan.id, strategy.id)

        self.assertFalse(self.database.update_recovery(plan.id, True, date(2026, 7, 20)))
        self.assertFalse(self.database.update_recovery(plan.id, False, date(2026, 7, 21)))
        self.assertFalse(self.database.update_recovery(plan.id, True, date(2026, 7, 22)))
        self.assertTrue(self.database.update_recovery(plan.id, True, date(2026, 7, 23)))

    def test_sensitive_setting_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.database.set_setting("smtp.password", "must-not-enter-sqlite")

    def test_event_action_preserves_snapshot_and_writes_audit(self) -> None:
        plan = Plan("A500", "022459", "159361", Decimal("600"))
        strategy = default_strategy(plan.id)
        self.database.add_plan_with_strategy(plan, strategy)
        level = strategy.levels[2]
        event_id, _ = self.database.create_event(
            plan_id=plan.id,
            strategy_id=strategy.id,
            level_id=level.id,
            event_type="drawdown",
            trading_date="2026-07-20",
            week_key="2026-W30",
            quote_price=Decimal("0.90"),
            quote_time=strategy.created_at,
            daily_change=Decimal("-1.2"),
            highest_close=Decimal("1.00"),
            drawdown=Decimal("10"),
            threshold_snapshot=level.threshold,
            multiplier_snapshot=level.multiplier,
            execution_mode_snapshot=level.execution_mode.value,
            regular_amount=Decimal("0.00"),
            extra_amount=Decimal("1200.00"),
            idempotency_key="event-action-test",
            state=EventState.NOTIFIED,
        )

        self.database.update_event_action(
            event_id,
            execution_status=ExecutionStatus.EXECUTED,
            executed_amount=Decimal("1000.00"),
        )

        row = self.database.event_row(event_id)
        self.assertEqual(row["execution_status"], "executed")
        self.assertEqual(row["executed_amount"], "1000.00")
        self.assertEqual(row["extra_amount"], "1200.00")
        self.assertEqual(self.database.count_rows()["audit_log"], 1)

    def test_event_can_be_ignored_without_assuming_execution(self) -> None:
        plan = Plan("A500", "022459", "159361", Decimal("600"))
        strategy = default_strategy(plan.id)
        self.database.add_plan_with_strategy(plan, strategy)
        event_id, _ = self.database.create_event(
            plan_id=plan.id,
            strategy_id=strategy.id,
            level_id=None,
            event_type="recurring",
            trading_date="2026-07-20",
            week_key="2026-W30",
            quote_price=Decimal("1.00"),
            quote_time=strategy.created_at,
            daily_change=None,
            highest_close=Decimal("1.00"),
            drawdown=Decimal("0"),
            threshold_snapshot=None,
            multiplier_snapshot=None,
            execution_mode_snapshot=None,
            regular_amount=Decimal("600.00"),
            extra_amount=Decimal("0.00"),
            idempotency_key="ignore-event-test",
        )
        self.database.update_event_action(event_id, state=EventState.IGNORED)
        row = self.database.event_row(event_id)
        self.assertEqual(row["state"], "ignored")
        self.assertEqual(row["execution_status"], "unknown")

    def test_ignored_delivered_event_still_occupies_weekly_limit(self) -> None:
        plan = Plan("A500", "022459", "159361", Decimal("600"))
        strategy = default_strategy(plan.id)
        self.database.add_plan_with_strategy(plan, strategy)
        level = strategy.levels[0]
        cycle_id = self.database.get_or_create_active_cycle(plan.id, strategy.id)
        event_id, _ = self.database.create_event(
            plan_id=plan.id,
            strategy_id=strategy.id,
            level_id=level.id,
            event_type="drawdown",
            trading_date="2026-07-20",
            week_key="2026-W30",
            quote_price=Decimal("0.95"),
            quote_time=strategy.created_at,
            daily_change=None,
            highest_close=Decimal("1.00"),
            drawdown=Decimal("5"),
            threshold_snapshot=level.threshold,
            multiplier_snapshot=level.multiplier,
            execution_mode_snapshot=level.execution_mode.value,
            regular_amount=Decimal("0.00"),
            extra_amount=Decimal("600.00"),
            idempotency_key="ignored-weekly-limit-test",
            state=EventState.NOTIFIED,
            cycle_id=cycle_id,
        )
        self.database.update_event_action(event_id, state=EventState.IGNORED)
        self.assertTrue(self.database.notified_this_week(plan.id, "2026-W30"))
        self.assertEqual(
            self.database.highest_notified_threshold(cycle_id), level.threshold
        )

    def test_cycle_peak_is_fixed_and_legacy_peak_uses_highest_event_snapshot(self) -> None:
        plan = Plan("A500", "022459", "159361", Decimal("600"))
        strategy = default_strategy(plan.id)
        self.database.add_plan_with_strategy(plan, strategy)
        cycle_id = self.database.get_or_create_active_cycle(plan.id, strategy.id)
        for index, peak in enumerate((Decimal("1.00"), Decimal("0.90"))):
            self.database.create_event(
                plan_id=plan.id,
                strategy_id=strategy.id,
                level_id=strategy.levels[0].id,
                event_type="drawdown",
                trading_date=f"2026-07-{20 + index}",
                week_key="2026-W30",
                quote_price=Decimal("0.80"),
                quote_time=strategy.created_at,
                daily_change=None,
                highest_close=peak,
                drawdown=Decimal("20"),
                threshold_snapshot=strategy.levels[0].threshold,
                multiplier_snapshot=Decimal("1"),
                execution_mode_snapshot="once",
                regular_amount=Decimal("0"),
                extra_amount=Decimal("600"),
                idempotency_key=f"legacy-peak-{index}",
                cycle_id=cycle_id,
            )
        self.assertEqual(
            self.database.resolve_cycle_peak(plan.id, Decimal("0.85")), Decimal("1.00")
        )
        self.assertEqual(
            Decimal(self.database.get_active_cycle(plan.id)["cycle_peak"]),
            Decimal("1.00"),
        )
