import tempfile
from datetime import datetime
from pathlib import Path
from unittest import TestCase
from zoneinfo import ZoneInfo

from etf_assistant.runtime import ProcessLock, in_execution_window, run_daily_check
from etf_assistant.runtime import build_notifiers, notification_configuration_errors
from etf_assistant.db import Database
from etf_assistant.providers.credentials import LocalCredentialStore, MemoryCredentialStore
from etf_assistant.providers.notify import SmtpEmailNotifier
from etf_assistant.scheduler import macos_plist, windows_task_xml


SHANGHAI = ZoneInfo("Asia/Shanghai")


class RuntimeTests(TestCase):
    def test_local_credentials_do_not_use_an_os_password_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "credentials.json"
            credentials = LocalCredentialStore(path)
            credentials.set("smtp.password", "authorization-code")
            self.assertEqual(credentials.get("smtp.password"), "authorization-code")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            credentials.delete("smtp.password")
            self.assertIsNone(credentials.get("smtp.password"))

    def test_execution_window(self) -> None:
        self.assertTrue(in_execution_window(datetime(2026, 7, 20, 14, 50, tzinfo=SHANGHAI)))
        self.assertFalse(in_execution_window(datetime(2026, 7, 20, 15, 1, tzinfo=SHANGHAI)))

    def test_process_lock_prevents_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "daily.lock"
            with ProcessLock(path):
                with self.assertRaises(RuntimeError):
                    with ProcessLock(path):
                        pass
            self.assertFalse(path.exists())

    def test_legacy_process_lock_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "daily.lock"
            path.write_text("2026-07-20 20:35:41+08:00", encoding="utf-8")
            with ProcessLock(path):
                self.assertIn('"pid"', path.read_text(encoding="utf-8"))
            self.assertFalse(path.exists())

    def test_scheduler_definitions_contain_1450(self) -> None:
        command = ["/app/etf-assistant", "daily-check"]
        plist = macos_plist(command)
        xml = windows_task_xml(command)
        self.assertIn(b"<integer>14</integer>", plist)
        self.assertIn(b"<integer>50</integer>", plist)
        self.assertIn("14:50:00", xml)
        self.assertIn("<Monday/>", xml)

    def test_email_configuration_builds_smtp_notifier(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "test.sqlite")
            database.initialize()
            for key, value in {
                "notification.desktop.enabled": "false",
                "notification.email.enabled": "true",
                "smtp.host": "smtp.example.com",
                "smtp.port": "465",
                "smtp.username": "sender@example.com",
                "smtp.sender": "sender@example.com",
                "smtp.recipient": "receiver@example.com",
                "smtp.use_ssl": "true",
            }.items():
                database.set_setting(key, value)
            credentials = MemoryCredentialStore({"smtp.password": "authorization-code"})

            self.assertEqual(notification_configuration_errors(database, credentials), ())
            notifiers = build_notifiers(database, credentials)
            self.assertEqual(len(notifiers), 1)
            self.assertIsInstance(notifiers[0], SmtpEmailNotifier)

    def test_failed_scheduled_check_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "test.sqlite")
            outside_window = datetime(2026, 7, 20, 16, 0, tzinfo=SHANGHAI)
            with self.assertRaisesRegex(RuntimeError, "MISSED_EXECUTION_WINDOW"):
                run_daily_check(database, now=outside_window)
            row = database.latest_check_run()
            self.assertEqual(row["state"], "failed")
            self.assertIn("MISSED_EXECUTION_WINDOW", row["error_summary"])
