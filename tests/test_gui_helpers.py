from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from etf_assistant.gui import _check_result_message, _money, _percent
from etf_assistant.service import CheckResult


class GuiHelperTests(unittest.TestCase):
    def test_money_format(self) -> None:
        self.assertEqual(_money(Decimal("1234.5")), "¥1,234.50")

    def test_percent_format(self) -> None:
        self.assertEqual(_percent(Decimal("10.456")), "10.46%")

    def test_frozen_entry_dispatches_child_processes_before_qt_import(self) -> None:
        module = __import__("etf_assistant.gui_entry", fromlist=["__file__"])
        source = Path(module.__file__).read_text(encoding="utf-8")
        self.assertLess(
            source.index("multiprocessing.freeze_support()"),
            source.index("from etf_assistant.gui import main as gui_main"),
        )

    def test_html_frontend_contains_required_product_surfaces(self) -> None:
        module = __import__("etf_assistant.gui", fromlist=["__file__"])
        html = (Path(module.__file__).with_name("web") / "index.html").read_text(encoding="utf-8")
        self.assertIn("基金管理", html)
        self.assertIn("策略历史", html)
        self.assertIn("SMTP 邮箱配置", html)
        self.assertIn("qwebchannel.js", html)
        self.assertIn("setPlanFlags", html)
        self.assertIn("切换常规计划", html)
        self.assertIn("切换回撤提醒", html)
        self.assertIn("短期20日回撤", html)
        self.assertIn("本轮固定锚点回撤", html)
        self.assertIn("PAUSED_FOR_REVIEW", html)
        self.assertIn("核心宽基：4 / 7 / 10 / 15 / 22", html)
        self.assertIn("后台抓取中", html)
        self.assertIn("30日K线", html)
        self.assertIn("onpointermove", html)
        self.assertIn("开盘　", html)
        self.assertIn("涨跌　", html)
        self.assertIn("checkFinished.connect", html)
        self.assertIn("var(--rise)", html)
        self.assertIn("var(--fall)", html)
        self.assertIn("不会再弹出系统密码框", html)
        self.assertNotIn("企业微信", html)

    def test_check_errors_are_aggregated_for_the_ui(self) -> None:
        result = CheckResult(4, (), ("raw upstream error 1", "raw upstream error 2"))
        self.assertEqual(
            _check_result_message(result),
            "已更新 4 项行情，生成 0 条提醒；2 项历史 K 线暂时不可用",
        )

    def test_desktop_builds_include_market_native_library(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertIn("--collect-all py_mini_racer", (root / "scripts" / "build_macos.sh").read_text())
        self.assertIn("--collect-all py_mini_racer", (root / "scripts" / "build_windows.ps1").read_text())


if __name__ == "__main__":
    unittest.main()
