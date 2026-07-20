from __future__ import annotations

import sys
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID, uuid4

from .backup import export_backup, restore_backup
from .config import default_database_path
from .db import Database
from .domain import (
    EffectiveMode,
    EventState,
    ExecutionMode,
    ExecutionStatus,
    Plan,
    Strategy,
    StrategyLevel,
)
from .strategy import default_strategy


WEEKDAYS = ("周一", "周二", "周三", "周四", "周五")


def _load_qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError as error:
        raise RuntimeError("桌面界面需要安装 PySide6：python -m pip install -e '.[desktop]'") from error
    return QtCore, QtGui, QtWidgets


def _money(value: object) -> str:
    return f"¥{Decimal(str(value)):,.2f}"


def _percent(value: object) -> str:
    return f"{Decimal(str(value)):.2f}%"


def _versioned_strategy(plan_id: UUID, current: Strategy, levels: tuple[StrategyLevel, ...]) -> Strategy:
    return Strategy(
        plan_id=plan_id,
        version=current.version + 1,
        levels=levels,
        effective_mode=EffectiveMode.IMMEDIATE,
    )


def main() -> int:
    QtCore, QtGui, QtWidgets = _load_qt()

    class PlanDialog(QtWidgets.QDialog):
        def __init__(self, parent=None, plan: Plan | None = None, strategy: Strategy | None = None):
            super().__init__(parent)
            self.setWindowTitle("编辑计划" if plan else "新增计划")
            self.resize(720, 650)
            self._plan = plan
            layout = QtWidgets.QVBoxLayout(self)

            title = QtWidgets.QLabel("基金计划")
            title.setObjectName("dialogTitle")
            layout.addWidget(title)
            form = QtWidgets.QFormLayout()
            self.name = QtWidgets.QLineEdit(plan.name if plan else "")
            self.purchase_code = QtWidgets.QLineEdit(plan.purchase_code if plan else "")
            self.purchase_name = QtWidgets.QLineEdit(plan.purchase_name if plan else "")
            self.signal_code = QtWidgets.QLineEdit(plan.signal_code if plan else "")
            self.signal_name = QtWidgets.QLineEdit(plan.signal_name if plan else "")
            self.amount = QtWidgets.QDoubleSpinBox()
            self.amount.setRange(0.01, 10_000_000)
            self.amount.setDecimals(2)
            self.amount.setPrefix("¥ ")
            self.amount.setValue(float(plan.base_amount) if plan else 300)
            self.weekday = QtWidgets.QComboBox()
            self.weekday.addItems(WEEKDAYS)
            self.weekday.setCurrentIndex(plan.invest_weekday if plan else 3)
            form.addRow("计划名称", self.name)
            form.addRow("场外联接基金代码", self.purchase_code)
            form.addRow("场外基金名称", self.purchase_name)
            form.addRow("场内观察 ETF 代码", self.signal_code)
            form.addRow("场内 ETF 名称", self.signal_name)
            form.addRow("每次基础金额", self.amount)
            form.addRow("固定定投日", self.weekday)
            layout.addLayout(form)

            switches = QtWidgets.QHBoxLayout()
            self.recurring = QtWidgets.QCheckBox("启用固定定投")
            self.drawdown = QtWidgets.QCheckBox("启用回撤补仓")
            self.recurring.setChecked(plan.recurring_enabled if plan else True)
            self.drawdown.setChecked(plan.drawdown_enabled if plan else True)
            switches.addWidget(self.recurring)
            switches.addWidget(self.drawdown)
            switches.addStretch()
            layout.addLayout(switches)

            strategy_label = QtWidgets.QLabel("回撤策略（每只基金独立配置）")
            strategy_label.setObjectName("sectionTitle")
            layout.addWidget(strategy_label)
            self.levels = QtWidgets.QTableWidget(0, 4)
            self.levels.setHorizontalHeaderLabels(("回撤 %", "补仓倍数", "执行方式", "启用"))
            self.levels.horizontalHeader().setStretchLastSection(True)
            self.levels.verticalHeader().setVisible(False)
            self.levels.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
            layout.addWidget(self.levels, 1)

            level_buttons = QtWidgets.QHBoxLayout()
            add_level = QtWidgets.QPushButton("添加档位")
            remove_level = QtWidgets.QPushButton("删除选中")
            add_level.clicked.connect(lambda: self._append_level(None))
            remove_level.clicked.connect(self._remove_level)
            level_buttons.addWidget(add_level)
            level_buttons.addWidget(remove_level)
            level_buttons.addStretch()
            layout.addLayout(level_buttons)

            source = strategy.levels if strategy else default_strategy(UUID(int=0)).levels
            for level in source:
                self._append_level(level)

            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.StandardButton.Save
                | QtWidgets.QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _append_level(self, level: StrategyLevel | None) -> None:
            row = self.levels.rowCount()
            self.levels.insertRow(row)
            threshold = QtWidgets.QDoubleSpinBox()
            threshold.setRange(0.01, 99.99)
            threshold.setValue(float(level.threshold) if level else 5)
            multiplier = QtWidgets.QDoubleSpinBox()
            multiplier.setRange(0.01, 100)
            multiplier.setDecimals(2)
            multiplier.setValue(float(level.multiplier) if level else 1)
            mode = QtWidgets.QComboBox()
            mode.addItem("单次", ExecutionMode.ONCE.value)
            mode.addItem("分期", ExecutionMode.PHASED.value)
            mode.addItem("深度回撤每周", ExecutionMode.WEEKLY_WHILE_DEEP.value)
            if level:
                mode.setCurrentIndex(max(0, mode.findData(level.execution_mode.value)))
            enabled = QtWidgets.QCheckBox()
            enabled.setChecked(level.enabled if level else True)
            self.levels.setCellWidget(row, 0, threshold)
            self.levels.setCellWidget(row, 1, multiplier)
            self.levels.setCellWidget(row, 2, mode)
            self.levels.setCellWidget(row, 3, enabled)

        def _remove_level(self) -> None:
            rows = sorted({item.row() for item in self.levels.selectedItems()}, reverse=True)
            for row in rows:
                self.levels.removeRow(row)

        def values(self) -> tuple[Plan, tuple[StrategyLevel, ...]]:
            plan = Plan(
                id=self._plan.id if self._plan else UUID(int=0),
                name=self.name.text().strip(),
                purchase_code=self.purchase_code.text().strip(),
                purchase_name=self.purchase_name.text().strip(),
                signal_code=self.signal_code.text().strip(),
                signal_name=self.signal_name.text().strip(),
                base_amount=Decimal(str(self.amount.value())),
                invest_weekday=self.weekday.currentIndex(),
                recurring_enabled=self.recurring.isChecked(),
                drawdown_enabled=self.drawdown.isChecked(),
            )
            if self._plan is None:
                plan = replace(plan, id=uuid4())
            levels: list[StrategyLevel] = []
            for row in range(self.levels.rowCount()):
                threshold = self.levels.cellWidget(row, 0)
                multiplier = self.levels.cellWidget(row, 1)
                mode = self.levels.cellWidget(row, 2)
                enabled = self.levels.cellWidget(row, 3)
                execution_mode = ExecutionMode(mode.currentData())
                levels.append(
                    StrategyLevel(
                        threshold=Decimal(str(threshold.value())),
                        multiplier=Decimal(str(multiplier.value())),
                        execution_mode=execution_mode,
                        phases=2 if execution_mode is ExecutionMode.PHASED else 1,
                        enabled=enabled.isChecked(),
                    )
                )
            plan.validate()
            Strategy(plan.id, 1, tuple(levels)).validate()
            return plan, tuple(levels)

    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self, database: Database):
            super().__init__()
            self.database = database
            self.database.initialize()
            self.setWindowTitle("ETF 定投助手")
            self.resize(1180, 760)
            self.setMinimumSize(900, 620)
            self._build()
            self.refresh()

        def _build(self) -> None:
            root = QtWidgets.QWidget()
            self.setCentralWidget(root)
            outer = QtWidgets.QHBoxLayout(root)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)

            sidebar = QtWidgets.QFrame()
            sidebar.setObjectName("sidebar")
            sidebar.setFixedWidth(210)
            side = QtWidgets.QVBoxLayout(sidebar)
            side.setContentsMargins(20, 24, 20, 20)
            brand = QtWidgets.QLabel("ETF 定投助手")
            brand.setObjectName("brand")
            side.addWidget(brand)
            subtitle = QtWidgets.QLabel("本地规则提醒工具")
            subtitle.setObjectName("sidebarMuted")
            side.addWidget(subtitle)
            side.addSpacing(24)
            self.nav = []
            for text in ("今日概览", "基金计划", "提醒历史", "数据与设置"):
                button = QtWidgets.QPushButton(text)
                button.setCheckable(True)
                button.setObjectName("navButton")
                side.addWidget(button)
                self.nav.append(button)
            side.addStretch()
            db_label = QtWidgets.QLabel(f"数据保存在本机\n{self.database.path.name}")
            db_label.setObjectName("sidebarMuted")
            side.addWidget(db_label)
            outer.addWidget(sidebar)

            self.pages = QtWidgets.QStackedWidget()
            outer.addWidget(self.pages, 1)
            self.pages.addWidget(self._dashboard_page())
            self.pages.addWidget(self._plans_page())
            self.pages.addWidget(self._history_page())
            self.pages.addWidget(self._settings_page())
            for index, button in enumerate(self.nav):
                button.clicked.connect(lambda checked=False, i=index: self._select_page(i))
            self._select_page(0)

        def _page(self, title: str, subtitle: str):
            widget = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(widget)
            layout.setContentsMargins(32, 28, 32, 28)
            header = QtWidgets.QLabel(title)
            header.setObjectName("pageTitle")
            layout.addWidget(header)
            caption = QtWidgets.QLabel(subtitle)
            caption.setObjectName("muted")
            layout.addWidget(caption)
            layout.addSpacing(18)
            return widget, layout

        def _dashboard_page(self):
            page, layout = self._page("今日概览", "14:50 自动检查场内 ETF，并按你设置的规则计算金额")
            status = QtWidgets.QFrame()
            status.setObjectName("notice")
            line = QtWidgets.QHBoxLayout(status)
            self.check_status = QtWidgets.QLabel("等待今日检查")
            self.check_status.setObjectName("noticeTitle")
            line.addWidget(self.check_status)
            line.addStretch()
            run = QtWidgets.QPushButton("立即检查")
            run.setObjectName("primaryButton")
            run.clicked.connect(self._manual_check)
            line.addWidget(run)
            layout.addWidget(status)

            stats = QtWidgets.QHBoxLayout()
            self.plan_count = self._stat("启用计划", "0")
            self.action_count = self._stat("待查看提醒", "0")
            self.base_total = self._stat("每周基础投入", "¥0")
            for card in (self.plan_count, self.action_count, self.base_total):
                stats.addWidget(card)
            layout.addLayout(stats)
            layout.addWidget(self._section("今日提醒"))
            self.today_table = self._table(("计划", "当前回撤", "触发档位", "额外金额", "状态"))
            layout.addWidget(self.today_table, 1)
            return page

        def _plans_page(self):
            page, layout = self._page("基金计划", "场内 ETF 用于观察；实际投入标的是对应的场外联接基金")
            bar = QtWidgets.QHBoxLayout()
            bar.addStretch()
            add = QtWidgets.QPushButton("新增计划")
            add.setObjectName("primaryButton")
            add.clicked.connect(self._add_plan)
            edit = QtWidgets.QPushButton("编辑选中")
            edit.clicked.connect(self._edit_plan)
            bar.addWidget(edit)
            bar.addWidget(add)
            layout.addLayout(bar)
            self.plan_table = self._table(("计划", "场外基金", "观察 ETF", "基础金额", "定投日", "策略档位", "状态"))
            layout.addWidget(self.plan_table, 1)
            return page

        def _history_page(self):
            page, layout = self._page("提醒历史", "记录当时使用的行情、策略版本和通知结果，不依赖你回填成交")
            actions = QtWidgets.QHBoxLayout()
            executed = QtWidgets.QPushButton("记录为已执行")
            not_executed = QtWidgets.QPushButton("记录为未执行")
            ignored = QtWidgets.QPushButton("忽略本档")
            postponed = QtWidgets.QPushButton("延迟提醒")
            executed.clicked.connect(self._mark_executed)
            not_executed.clicked.connect(
                lambda: self._update_selected_event(execution_status=ExecutionStatus.NOT_EXECUTED)
            )
            ignored.clicked.connect(
                lambda: self._update_selected_event(state=EventState.IGNORED)
            )
            postponed.clicked.connect(
                lambda: self._update_selected_event(state=EventState.POSTPONED)
            )
            for button in (executed, not_executed, ignored, postponed):
                actions.addWidget(button)
            actions.addStretch()
            layout.addLayout(actions)
            self.history_table = self._table(("时间", "计划", "回撤", "档位", "固定投入", "额外投入", "状态"))
            layout.addWidget(self.history_table, 1)
            return page

        def _settings_page(self):
            page, layout = self._page("数据与设置", "迁移包包含计划、策略和历史记录，不包含邮箱密码或微信 Webhook")
            layout.addWidget(self._section("备份与迁移"))
            actions = QtWidgets.QHBoxLayout()
            export = QtWidgets.QPushButton("导出完整迁移包")
            restore = QtWidgets.QPushButton("从迁移包恢复")
            export.clicked.connect(self._export)
            restore.clicked.connect(self._restore)
            actions.addWidget(export)
            actions.addWidget(restore)
            actions.addStretch()
            layout.addLayout(actions)
            note = QtWidgets.QLabel("恢复前会自动保留当前数据库副本。SMTP 密码和微信 Webhook 需在新设备重新配置。")
            note.setObjectName("muted")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addSpacing(24)
            layout.addWidget(self._section("通知渠道"))
            self.desktop_enabled = QtWidgets.QCheckBox("桌面系统通知")
            self.email_enabled = QtWidgets.QCheckBox("邮件通知")
            self.wechat_enabled = QtWidgets.QCheckBox("企业微信机器人通知")
            for checkbox in (self.desktop_enabled, self.email_enabled, self.wechat_enabled):
                layout.addWidget(checkbox)
            save = QtWidgets.QPushButton("保存设置")
            save.setObjectName("primaryButton")
            save.clicked.connect(self._save_settings)
            layout.addWidget(save, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
            layout.addStretch()
            return page

        def _stat(self, label: str, value: str):
            card = QtWidgets.QFrame()
            card.setObjectName("card")
            box = QtWidgets.QVBoxLayout(card)
            caption = QtWidgets.QLabel(label)
            caption.setObjectName("muted")
            number = QtWidgets.QLabel(value)
            number.setObjectName("statValue")
            box.addWidget(caption)
            box.addWidget(number)
            card.value_label = number
            return card

        def _section(self, text: str):
            label = QtWidgets.QLabel(text)
            label.setObjectName("sectionTitle")
            return label

        def _table(self, headers):
            table = QtWidgets.QTableWidget(0, len(headers))
            table.setHorizontalHeaderLabels(headers)
            table.horizontalHeader().setStretchLastSection(True)
            table.verticalHeader().setVisible(False)
            table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
            table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setAlternatingRowColors(False)
            return table

        def _select_page(self, index: int) -> None:
            self.pages.setCurrentIndex(index)
            for i, button in enumerate(self.nav):
                button.setChecked(i == index)

        def refresh(self) -> None:
            plans = self.database.list_plans()
            events = self.database.recent_events(100)
            enabled = [plan for plan in plans if plan.enabled]
            self.plan_count.value_label.setText(str(len(enabled)))
            self.action_count.value_label.setText(str(sum(row["state"] in ("created", "notified") for row in events)))
            self.base_total.value_label.setText(_money(sum((p.base_amount for p in enabled if p.recurring_enabled), Decimal("0"))))
            self._fill_plans(plans)
            self._fill_history(events)
            self._fill_today(events)
            self.desktop_enabled.setChecked(self.database.get_setting("notification.desktop.enabled", "true") == "true")
            self.email_enabled.setChecked(self.database.get_setting("notification.email.enabled", "false") == "true")
            self.wechat_enabled.setChecked(self.database.get_setting("notification.wechat.enabled", "false") == "true")

        def _fill_plans(self, plans: list[Plan]) -> None:
            self.plan_table.setRowCount(len(plans))
            for row, plan in enumerate(plans):
                strategy = self.database.get_active_strategy(plan.id)
                levels = " / ".join(f"{level.threshold:g}%×{level.multiplier:g}" for level in strategy.levels if level.enabled)
                values = (
                    plan.name, plan.purchase_code, plan.signal_code, _money(plan.base_amount),
                    WEEKDAYS[plan.invest_weekday], levels, "启用" if plan.enabled else "暂停",
                )
                for column, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(str(value))
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, str(plan.id))
                    self.plan_table.setItem(row, column, item)
            self.plan_table.resizeColumnsToContents()

        def _fill_history(self, events) -> None:
            self.history_table.setRowCount(len(events))
            for row, event in enumerate(events):
                values = (
                    event["quote_time"][:16].replace("T", " "), event["plan_name"],
                    _percent(event["drawdown"]),
                    _percent(event["threshold_snapshot"]) if event["threshold_snapshot"] else "—",
                    _money(event["regular_amount"]), _money(event["extra_amount"]), event["state"],
                )
                for column, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(str(value))
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, event["id"])
                    self.history_table.setItem(row, column, item)
            self.history_table.resizeColumnsToContents()

        def _selected_event_id(self) -> UUID | None:
            row = self.history_table.currentRow()
            if row < 0:
                QtWidgets.QMessageBox.information(self, "请选择提醒", "请先在历史表格中选择一条提醒。")
                return None
            return UUID(self.history_table.item(row, 0).data(QtCore.Qt.ItemDataRole.UserRole))

        def _update_selected_event(self, **changes) -> None:
            event_id = self._selected_event_id()
            if event_id is None:
                return
            try:
                self.database.update_event_action(event_id, **changes)
                self.refresh()
            except (KeyError, ValueError) as error:
                QtWidgets.QMessageBox.warning(self, "无法更新提醒", str(error))

        def _mark_executed(self) -> None:
            event_id = self._selected_event_id()
            if event_id is None:
                return
            row = self.database.event_row(event_id)
            planned = Decimal(row["total_amount"])
            amount, accepted = QtWidgets.QInputDialog.getDouble(
                self,
                "记录实际投入",
                "实际投入金额（可按实际情况修改）：",
                float(planned),
                0,
                10_000_000,
                2,
            )
            if accepted:
                self.database.update_event_action(
                    event_id,
                    execution_status=ExecutionStatus.EXECUTED,
                    executed_amount=Decimal(str(amount)),
                )
                self.refresh()

        def _fill_today(self, events) -> None:
            from datetime import date
            today = date.today().isoformat()
            rows = [event for event in events if event["trading_date"] == today]
            self.today_table.setRowCount(len(rows))
            for row, event in enumerate(rows):
                values = (
                    event["plan_name"], _percent(event["drawdown"]),
                    _percent(event["threshold_snapshot"]) if event["threshold_snapshot"] else "—",
                    _money(event["extra_amount"]), event["state"],
                )
                for column, value in enumerate(values):
                    self.today_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
            self.today_table.resizeColumnsToContents()

        def _add_plan(self) -> None:
            dialog = PlanDialog(self)
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            try:
                plan, levels = dialog.values()
                strategy = Strategy(plan.id, 1, levels, EffectiveMode.IMMEDIATE)
                self.database.add_plan_with_strategy(plan, strategy)
                self.refresh()
            except (ValueError, InvalidOperation) as error:
                QtWidgets.QMessageBox.warning(self, "无法保存", str(error))

        def _selected_plan(self) -> Plan | None:
            row = self.plan_table.currentRow()
            if row < 0:
                return None
            return self.database.get_plan(UUID(self.plan_table.item(row, 0).data(QtCore.Qt.ItemDataRole.UserRole)))

        def _edit_plan(self) -> None:
            plan = self._selected_plan()
            if plan is None:
                QtWidgets.QMessageBox.information(self, "请选择计划", "请先在表格中选择一项计划。")
                return
            current = self.database.get_active_strategy(plan.id)
            dialog = PlanDialog(self, plan, current)
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            try:
                updated, levels = dialog.values()
                self.database.update_plan(updated)
                if tuple((x.threshold, x.multiplier, x.execution_mode, x.enabled) for x in levels) != tuple(
                    (x.threshold, x.multiplier, x.execution_mode, x.enabled) for x in current.levels
                ):
                    self.database.save_strategy(_versioned_strategy(plan.id, current, levels))
                self.refresh()
            except (ValueError, InvalidOperation) as error:
                QtWidgets.QMessageBox.warning(self, "无法保存", str(error))

        def _manual_check(self) -> None:
            from .runtime import run_daily_check
            self.check_status.setText("正在检查…")
            QtWidgets.QApplication.processEvents()
            try:
                result = run_daily_check(self.database, force=True)
                self.check_status.setText(f"已检查 {result.checked_plans} 项，生成 {len(result.created_events)} 条提醒")
                self.refresh()
            except Exception as error:
                self.check_status.setText("检查失败")
                QtWidgets.QMessageBox.warning(self, "检查失败", str(error))

        def _export(self) -> None:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出迁移包", "etf-backup.etfbak", "ETF 迁移包 (*.etfbak)")
            if path:
                try:
                    export_backup(self.database, Path(path))
                    QtWidgets.QMessageBox.information(self, "导出完成", f"迁移包已保存到：\n{path}")
                except Exception as error:
                    QtWidgets.QMessageBox.warning(self, "导出失败", str(error))

        def _restore(self) -> None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择迁移包", "", "ETF 迁移包 (*.etfbak)")
            if not path:
                return
            answer = QtWidgets.QMessageBox.question(self, "确认恢复", "当前数据将被迁移包替换，系统会先自动保留回滚副本。是否继续？")
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            try:
                rollback = restore_backup(Path(path), self.database)
                self.refresh()
                QtWidgets.QMessageBox.information(self, "恢复完成", f"数据已恢复。\n回滚副本：{rollback or '无'}")
            except Exception as error:
                QtWidgets.QMessageBox.warning(self, "恢复失败", str(error))

        def _save_settings(self) -> None:
            self.database.set_setting("notification.desktop.enabled", str(self.desktop_enabled.isChecked()).lower())
            self.database.set_setting("notification.email.enabled", str(self.email_enabled.isChecked()).lower())
            self.database.set_setting("notification.wechat.enabled", str(self.wechat_enabled.isChecked()).lower())
            QtWidgets.QMessageBox.information(self, "已保存", "通知渠道设置已保存。密钥请通过系统凭据存储配置。")

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("ETF 定投助手")
    app.setStyle("Fusion")
    app.setStyleSheet("""
        * { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 14px; color: #18201d; }
        QMainWindow, QWidget { background: #f6f7f6; }
        #sidebar { background: #17211d; }
        #brand { color: #ffffff; font-size: 19px; font-weight: 600; }
        #sidebarMuted { color: #9eaaa4; font-size: 12px; }
        #navButton { background: transparent; color: #cbd3cf; border: 0; border-radius: 7px; padding: 10px 12px; text-align: left; }
        #navButton:hover { background: #23302a; color: #ffffff; }
        #navButton:checked { background: #2c4137; color: #ffffff; }
        #pageTitle { font-size: 24px; font-weight: 600; }
        #dialogTitle { font-size: 20px; font-weight: 600; }
        #sectionTitle { font-size: 16px; font-weight: 600; margin-top: 8px; }
        #muted { color: #68736e; }
        #notice, #card { background: #ffffff; border: 1px solid #dfe4e1; border-radius: 10px; }
        #notice { background: #eef6f1; border-color: #cfe3d7; }
        #noticeTitle { font-weight: 600; color: #24523d; }
        #statValue { font-size: 22px; font-weight: 600; }
        QPushButton { background: #ffffff; border: 1px solid #d4dad6; border-radius: 7px; padding: 8px 14px; }
        QPushButton:hover { background: #f0f3f1; }
        #primaryButton { background: #276447; color: #ffffff; border-color: #276447; }
        #primaryButton:hover { background: #1f553b; }
        QLineEdit, QComboBox, QDoubleSpinBox { background: #ffffff; border: 1px solid #cfd6d2; border-radius: 6px; padding: 7px; }
        QTableWidget { background: #ffffff; border: 1px solid #dfe4e1; border-radius: 8px; gridline-color: transparent; selection-background-color: #dfeee6; selection-color: #18201d; }
        QHeaderView::section { background: #f0f3f1; border: 0; border-bottom: 1px solid #dfe4e1; padding: 9px; font-weight: 600; }
        QTableWidget::item { border-bottom: 1px solid #edf0ee; padding: 8px; }
        QDialog { background: #f6f7f6; }
    """)
    database = Database(default_database_path())
    window = MainWindow(database)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
