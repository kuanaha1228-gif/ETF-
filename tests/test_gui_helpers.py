from __future__ import annotations

import unittest
from decimal import Decimal

from etf_assistant.gui import _money, _percent


class GuiHelperTests(unittest.TestCase):
    def test_money_format(self) -> None:
        self.assertEqual(_money(Decimal("1234.5")), "¥1,234.50")

    def test_percent_format(self) -> None:
        self.assertEqual(_percent(Decimal("10.456")), "10.46%")


if __name__ == "__main__":
    unittest.main()
