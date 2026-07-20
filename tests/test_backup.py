import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from etf_assistant.backup import BackupError, export_backup, merge_backup, restore_backup, validate_backup
from etf_assistant.db import Database
from etf_assistant.domain import Plan
from etf_assistant.strategy import default_strategy


class BackupTests(TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _source(self) -> tuple[Database, Plan]:
        database = Database(self.root / "source.sqlite")
        database.initialize()
        plan = Plan("A500", "022459", "159361", Decimal("600"))
        database.add_plan_with_strategy(plan, default_strategy(plan.id))
        return database, plan

    def test_export_validate_and_restore(self) -> None:
        source, plan = self._source()
        package = export_backup(source, self.root / "backup.etfbak")
        manifest = validate_backup(package)
        self.assertFalse(manifest["contains_secrets"])
        target = Database(self.root / "target.sqlite")
        restore_backup(package, target)
        self.assertEqual(target.list_plans()[0].id, plan.id)
        self.assertEqual(source.count_rows(), target.count_rows())

    def test_merge_is_idempotent(self) -> None:
        source, _ = self._source()
        package = export_backup(source, self.root / "backup.etfbak")
        target = Database(self.root / "target.sqlite")
        target.initialize()
        merge_backup(package, target)
        first = target.count_rows()
        merge_backup(package, target)
        self.assertEqual(first, target.count_rows())

    def test_tampered_package_is_rejected(self) -> None:
        source, _ = self._source()
        package = export_backup(source, self.root / "backup.etfbak")
        altered = self.root / "altered.etfbak"
        altered.write_bytes(package.read_bytes())
        with zipfile.ZipFile(altered, "a") as archive:
            archive.writestr("settings.json", b"tampered")
        with self.assertRaises(BackupError):
            validate_backup(altered)

