import tempfile
from datetime import datetime
from pathlib import Path
from unittest import TestCase
from zoneinfo import ZoneInfo

from etf_assistant.runtime import ProcessLock, in_execution_window
from etf_assistant.scheduler import macos_plist, windows_task_xml


SHANGHAI = ZoneInfo("Asia/Shanghai")


class RuntimeTests(TestCase):
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

    def test_scheduler_definitions_contain_1450(self) -> None:
        command = ["/app/etf-assistant", "daily-check"]
        plist = macos_plist(command)
        xml = windows_task_xml(command)
        self.assertIn(b"<integer>14</integer>", plist)
        self.assertIn(b"<integer>50</integer>", plist)
        self.assertIn("14:50:00", xml)
        self.assertIn("<Monday/>", xml)

