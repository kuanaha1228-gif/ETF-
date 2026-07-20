from __future__ import annotations

import unittest
from decimal import Decimal

from etf_assistant.gui import _money, _percent
from etf_assistant import gui_entry


class GuiHelperTests(unittest.TestCase):
    def test_money_format(self) -> None:
        self.assertEqual(_money(Decimal("1234.5")), "¥1,234.50")

    def test_percent_format(self) -> None:
        self.assertEqual(_percent(Decimal("10.456")), "10.46%")

    def test_frozen_entry_imports_package_main(self) -> None:
        self.assertIs(gui_entry.main, __import__("etf_assistant.gui", fromlist=["main"]).main)


if __name__ == "__main__":
    unittest.main()
