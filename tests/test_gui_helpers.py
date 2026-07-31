from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from etf_assistant.gui import (
    _check_result_message,
    _database_path_from_arguments,
    _decimal_input,
    _money,
    _parse_take_profit_rules,
    _percent,
)
from etf_assistant.service import CheckResult


class GuiHelperTests(unittest.TestCase):
    def test_money_format(self) -> None:
        self.assertEqual(_money(Decimal("1234.5")), "¥1,234.50")

    def test_percent_format(self) -> None:
        self.assertEqual(_percent(Decimal("10.456")), "10.46%")

    def test_fractional_holdings_accept_common_decimal_separators(self) -> None:
        self.assertEqual(
            _decimal_input("123.456789", "当前实际持有份额"),
            Decimal("123.456789"),
        )
        self.assertEqual(
            _decimal_input("123，456789", "当前实际持有份额"),
            Decimal("123.456789"),
        )
        self.assertEqual(
            _decimal_input(".25", "当前实际持有份额"),
            Decimal("0.25"),
        )

    def test_fractional_holdings_reject_more_than_six_places(self) -> None:
        with self.assertRaisesRegex(ValueError, "最多支持 6 位小数"):
            _decimal_input("1.1234567", "当前实际持有份额")

    def test_background_entry_accepts_an_isolated_database(self) -> None:
        self.assertEqual(
            _database_path_from_arguments(["--db", "/tmp/etf-test.sqlite", "--daily-check"]),
            Path("/tmp/etf-test.sqlite"),
        )

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
        self.assertIn("executionStatus", html)
        self.assertIn("已标记为执行，状态已保存", html)
        self.assertIn("本条为 0 元风险提示，无需执行", html)
        self.assertIn("本周已有补仓提醒，本次仅提示风险加深", html)
        self.assertIn("定时检查：14:50 已启用", html)
        self.assertIn("var(--rise)", html)
        self.assertIn("var(--fall)", html)
        self.assertIn("不会再弹出系统密码框", html)
        self.assertIn("当前持仓与止盈", html)
        self.assertIn("精确止盈位置", html)
        self.assertIn("当前阶段定投金额", html)
        self.assertIn('id="tp-actual-units" type="text" inputmode="decimal"', html)
        self.assertIn("当前实际持有份额必须是非负数字，最多 6 位小数", html)
        self.assertIn("记录实际赎回", html)
        self.assertIn("天天基金最新官方净值", html)
        self.assertIn("QDII / 高误差估值", html)
        self.assertIn("保存人工净值并复核", html)
        self.assertIn("场内 ETF 只提供回撤与盘中涨跌信号", html)
        self.assertIn("确认已投入", html)
        self.assertIn("等待净值", html)
        self.assertIn("从天天基金同步", html)
        self.assertIn("archivePlan", html)
        self.assertIn("takeProfitDrawdown", html)
        self.assertIn("恢复回撤阈值", html)
        self.assertIn("恢复后定投金额", html)
        self.assertIn("删除此档", html)
        self.assertIn("删除档位", html)
        self.assertIn("回撤档位 ${Number(duplicate.threshold)}% 重复", html)
        self.assertIn('class="level-phases"', html)
        self.assertIn('class="level-interval"', html)
        self.assertIn("phases.disabled=!phased", html)
        self.assertIn("interval.disabled=!phased", html)
        self.assertIn('<form id="plan-form" novalidate>', html)
        self.assertIn("请填写计划名称、场外基金代码和场内 ETF 代码", html)
        self.assertIn("后一期检查时仍达到该回撤档位才会提醒", html)
        self.assertIn("每个自然周最多提醒一次", html)
        self.assertIn("删除全部档位表示暂不自动恢复定投", html)
        self.assertNotIn("企业微信", html)

    def test_recovery_thresholds_and_amounts_are_frontend_configurable(self) -> None:
        levels, recovery = _parse_take_profit_rules(
            """{"levels": [], "recoveryLevels": [
                {"drawdown": "12.5", "recurringAmount": "180"},
                {"drawdown": "27", "recurringAmount": "420"}
            ]}"""
        )
        self.assertEqual(levels, ())
        self.assertEqual(
            [(level.drawdown, level.recurring_amount) for level in recovery],
            [
                (Decimal("12.5"), Decimal("180")),
                (Decimal("27"), Decimal("420")),
            ],
        )

    def test_recovery_rule_requires_both_frontend_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须同时填写"):
            _parse_take_profit_rules(
                """{"levels": [], "recoveryLevels": [
                    {"drawdown": "20", "recurringAmount": ""}
                ]}"""
            )

    def test_all_recovery_rules_can_be_removed(self) -> None:
        _, recovery = _parse_take_profit_rules(
            """{"levels": [], "recoveryLevels": []}"""
        )
        self.assertEqual(recovery, ())

    def test_check_errors_are_aggregated_for_the_ui(self) -> None:
        result = CheckResult(4, (), ("raw upstream error 1", "raw upstream error 2"))
        self.assertEqual(
            _check_result_message(result),
            "已更新 4 项行情；未执行定投、补仓或止盈策略；2 项历史 K 线暂时不可用",
        )

    def test_desktop_builds_include_market_native_library(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertIn("--collect-all py_mini_racer", (root / "scripts" / "build_macos.sh").read_text())
        self.assertIn("--collect-all py_mini_racer", (root / "scripts" / "build_windows.ps1").read_text())


if __name__ == "__main__":
    unittest.main()
