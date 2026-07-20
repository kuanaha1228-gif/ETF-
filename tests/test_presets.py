import tempfile
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from etf_assistant.db import Database
from etf_assistant.presets import PRD_PLAN_PRESETS, install_prd_plan_presets


class PresetTests(TestCase):
    def test_prd_plans_are_installed_once_with_default_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "test.sqlite")
            database.initialize()

            self.assertEqual(install_prd_plan_presets(database), 4)
            self.assertEqual(install_prd_plan_presets(database), 0)
            plans = database.list_plans()
            self.assertEqual(len(plans), len(PRD_PLAN_PRESETS))
            self.assertEqual(
                [(plan.purchase_code, plan.signal_code, plan.base_amount) for plan in plans],
                [
                    ("022459", "159361", Decimal("600")),
                    ("019632", "159516", Decimal("250")),
                    ("019666", "516080", Decimal("200")),
                    ("014415", "516670", Decimal("300")),
                ],
            )
            for plan in plans:
                strategy = database.get_active_strategy(plan.id)
                self.assertEqual(
                    [(level.threshold, level.multiplier) for level in strategy.levels],
                    [
                        (Decimal("5"), Decimal("1")),
                        (Decimal("8"), Decimal("1")),
                        (Decimal("10"), Decimal("2")),
                        (Decimal("15"), Decimal("1")),
                        (Decimal("20"), Decimal("1")),
                    ],
                )
