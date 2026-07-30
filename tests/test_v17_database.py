import sqlite3
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from etf_assistant.db import Database
from etf_assistant.domain import Plan, PlanStatus, RecoveryLevel, TakeProfitLevel
from etf_assistant.strategy import default_strategy


class V17DatabaseTests(TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "test.sqlite")
        self.database.initialize()
        self.plan = Plan("A500", "022459", "159361", Decimal("300"))
        self.strategy = default_strategy(self.plan.id)
        self.database.add_plan_with_strategy(self.plan, self.strategy)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_locked_basis_requires_confirmation_only_when_cost_changes(self) -> None:
        self.database.save_take_profit_basis(
            self.plan.id,
            actual_units=Decimal("1000"),
            cost_basis=Decimal("1"),
            lock=True,
            previous_official_nav=Decimal("1"),
            previous_official_nav_date=date(2026, 7, 29),
        )
        self.database.save_take_profit_basis(
            self.plan.id,
            actual_units=Decimal("1100"),
            cost_basis=Decimal("1"),
            lock=False,
        )
        with self.assertRaisesRegex(ValueError, "confirmed correction"):
            self.database.save_take_profit_basis(
                self.plan.id,
                actual_units=Decimal("1100"),
                cost_basis=Decimal("1.01"),
                lock=False,
            )
        self.database.save_take_profit_basis(
            self.plan.id,
            actual_units=Decimal("1100"),
            cost_basis=Decimal("1.01"),
            lock=False,
            confirm_correction=True,
        )
        cycle = self.database.current_take_profit_cycle(self.plan.id)
        self.assertEqual(cycle["basis_status"], "locked")
        self.assertEqual(cycle["actual_units"], "1100")
        with self.database.read() as connection:
            actions = [
                row[0]
                for row in connection.execute(
                    "SELECT action FROM audit_log ORDER BY created_at"
                )
            ]
        self.assertIn("take_profit_holdings_updated", actions)
        self.assertIn("take_profit_basis_corrected", actions)

    def test_fractional_actual_units_are_preserved(self) -> None:
        self.database.save_take_profit_basis(
            self.plan.id,
            actual_units=Decimal("123.456789"),
            cost_basis=Decimal("1.234567"),
            lock=False,
        )
        cycle = self.database.current_take_profit_cycle(self.plan.id)
        self.assertEqual(cycle["actual_units"], "123.456789")

    def test_archive_is_soft_delete_and_keeps_history(self) -> None:
        self.database.archive_plan(self.plan.id)
        self.assertEqual(self.database.list_plans(), [])
        archived = self.database.get_plan(self.plan.id)
        self.assertEqual(archived.status, PlanStatus.ARCHIVED)
        self.assertFalse(archived.enabled)
        with self.database.read() as connection:
            audit = connection.execute(
                "SELECT details_json FROM audit_log WHERE action = 'plan_archived'"
            ).fetchone()
        self.assertIsNotNone(audit)
        self.assertIn("159361", audit["details_json"])

    def test_take_profit_rule_edits_keep_historical_level_ids(self) -> None:
        old = TakeProfitLevel(Decimal("20"), Decimal("30"), Decimal("200"))
        recovery = RecoveryLevel(Decimal("10"), Decimal("200"))
        self.database.replace_take_profit_rules(
            self.plan.id, (old,), (recovery,)
        )
        old_id = self.database.take_profit_levels(self.plan.id)[0].id
        self.database.replace_take_profit_rules(
            self.plan.id,
            (
                TakeProfitLevel(Decimal("20"), Decimal("25"), Decimal("180")),
                TakeProfitLevel(
                    Decimal("40"), Decimal("100"), Decimal("100"), sell_all=True
                ),
            ),
            (RecoveryLevel(Decimal("20"), Decimal("300")),),
        )
        levels = self.database.take_profit_levels(self.plan.id)
        self.assertEqual(levels[0].id, old_id)
        self.assertEqual(levels[0].sell_ratio, Decimal("25"))
        with self.database.read() as connection:
            versions = connection.execute(
                """
                SELECT COUNT(*) FROM plan_configuration_versions
                WHERE plan_id = ?
                """,
                (str(self.plan.id),),
            ).fetchone()[0]
        self.assertEqual(versions, 3)

    def test_schema_five_migration_preserves_existing_plan(self) -> None:
        path = Path(self.temp.name) / "legacy.sqlite"
        legacy = Database(path)
        legacy.initialize()
        plan = Plan("Legacy", "012349", "513010", Decimal("600"))
        legacy.add_plan_with_strategy(plan, default_strategy(plan.id))
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA user_version = 5")
        connection.commit()
        connection.close()
        legacy.initialize()
        migrated = legacy.get_plan(plan.id)
        self.assertEqual(migrated.base_amount, Decimal("600"))
        self.assertEqual(migrated.current_amount, Decimal("600"))
        self.assertTrue(legacy.integrity_check())

    def test_schema_six_locked_basis_is_preserved_for_review(self) -> None:
        self.database.save_take_profit_basis(
            self.plan.id,
            actual_units=Decimal("88.125"),
            cost_basis=Decimal("1.2345"),
            lock=False,
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE take_profit_cycles SET basis_status = 'locked' WHERE plan_id = ?",
                (str(self.plan.id),),
            )
            connection.execute("PRAGMA user_version = 6")
        self.database.initialize()
        cycle = self.database.current_take_profit_cycle(self.plan.id)
        self.assertEqual(cycle["cost_basis"], "1.2345")
        self.assertEqual(cycle["actual_units"], "88.125")
        self.assertEqual(cycle["basis_status"], "locked")
        self.assertEqual(cycle["basis_review_status"], "needs_review")
        self.assertIsNone(cycle["previous_official_nav"])
