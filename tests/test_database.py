import tempfile
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from etf_assistant.db import Database
from etf_assistant.domain import EffectiveMode, Plan, Strategy, StrategyLevel
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
        self.assertFalse(self.database.update_recovery(plan.id, True))
        self.assertTrue(self.database.update_recovery(plan.id, True))
        self.assertEqual(self.database.get_active_strategy(plan.id).id, pending.id)

    def test_sensitive_setting_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.database.set_setting("wechat.webhook", "must-not-enter-sqlite")
