from __future__ import annotations

import json
import sys
import threading
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from .backup import export_backup, restore_backup
from .config import default_database_path
from .db import Database
from .domain import EffectiveMode, EventState, ExecutionMode, ExecutionStatus, Plan, Strategy, StrategyLevel
from .presets import install_prd_plan_presets
from .providers.credentials import LocalCredentialStore
from .providers.notify import SmtpEmailNotifier
from .strategy import STRATEGY_TEMPLATES, default_strategy


WEEKDAYS = ("周一", "周二", "周三", "周四", "周五")


def _load_qt():
    try:
        from PySide6 import QtCore, QtWebChannel, QtWebEngineWidgets, QtWidgets
    except ImportError as error:
        raise RuntimeError("桌面界面需要安装 PySide6：python -m pip install -e '.[desktop]'") from error
    return QtCore, QtWebChannel, QtWebEngineWidgets, QtWidgets


def _money(value: object) -> str:
    return f"¥{Decimal(str(value)):,.2f}"


def _percent(value: object) -> str:
    return f"{Decimal(str(value)):.2f}%"


def _check_result_message(result) -> str:
    message = f"已更新 {result.checked_plans} 项行情，生成 {len(result.created_events)} 条提醒"
    if result.errors:
        message += f"；{len(result.errors)} 项历史 K 线暂时不可用"
    return message


def _friendly_error(error: Exception) -> str:
    text = str(error)
    if "already running" in text:
        return "行情检查正在进行，请稍候"
    if any(marker in text.lower() for marker in ("connection", "timeout", "remote")):
        return "行情服务暂时不可用，请稍后再试"
    return "操作失败，请稍后再试"


def _plan_payload(database: Database, plan: Plan) -> dict[str, object]:
    strategy = database.get_active_strategy(plan.id)
    cycle = database.get_active_cycle(plan.id)
    return {
        "id": str(plan.id),
        "name": plan.name,
        "purchaseCode": plan.purchase_code,
        "purchaseName": plan.purchase_name,
        "signalCode": plan.signal_code,
        "signalName": plan.signal_name,
        "baseAmount": str(plan.base_amount),
        "weekday": plan.invest_weekday,
        "weekdayLabel": WEEKDAYS[plan.invest_weekday],
        "recurringEnabled": plan.recurring_enabled,
        "drawdownEnabled": plan.drawdown_enabled,
        "enabled": plan.enabled,
        "pausedForReview": bool(cycle["paused_for_review"]) if cycle else False,
        "cyclePeak": cycle["cycle_peak"] if cycle else None,
        "levels": [
            {
                "threshold": str(level.threshold),
                "multiplier": str(level.multiplier),
                "executionMode": level.execution_mode.value,
                "enabled": level.enabled,
                "maxExecutions": level.max_executions,
                "cycleAmountCap": (
                    str(level.cycle_amount_cap) if level.cycle_amount_cap else None
                ),
                "cycleMultiplierCap": (
                    str(level.cycle_multiplier_cap)
                    if level.cycle_multiplier_cap else None
                ),
            }
            for level in strategy.levels
        ],
    }


def main() -> int:
    QtCore, QtWebChannel, QtWebEngineWidgets, QtWidgets = _load_qt()
    database = Database(default_database_path())
    database.initialize()
    install_prd_plan_presets(database)

    class Bridge(QtCore.QObject):
        stateChanged = QtCore.Signal()
        checkFinished = QtCore.Signal(str)
        chartLoaded = QtCore.Signal(str, str)

        def __init__(self) -> None:
            super().__init__()
            self._check_running = False
            self._chart_running: set[str] = set()

        @QtCore.Slot(result=str)
        def getState(self) -> str:
            plans = database.list_plans()
            events = database.recent_events(100)
            market = {
                row["plan_id"]: {
                    "price": row["quote_price"],
                    "quoteTime": row["quote_time"],
                    "dailyChange": row["daily_change"],
                    "highestClose": row["highest_close"],
                    "drawdown": row["drawdown"],
                    "cyclePeak": row["cycle_peak"],
                    "cycleDrawdown": row["cycle_drawdown"],
                    "updatedAt": row["updated_at"],
                }
                for row in database.market_snapshots()
            }
            return json.dumps(
                {
                    "plans": [_plan_payload(database, plan) for plan in plans],
                    "market": market,
                    "events": [
                        {
                            "id": row["id"],
                            "planName": row["plan_name"],
                            "quoteTime": row["quote_time"],
                            "tradingDate": row["trading_date"],
                            "drawdown": str(row["drawdown"]),
                            "threshold": row["threshold_snapshot"],
                            "regularAmount": str(row["regular_amount"]),
                            "extraAmount": str(row["extra_amount"]),
                            "totalAmount": str(row["total_amount"]),
                            "state": row["state"],
                        }
                        for row in events
                    ],
                    "strategyTemplates": {
                        key: {"name": value[0], "thresholds": value[1]}
                        for key, value in STRATEGY_TEMPLATES.items()
                    },
                    "settings": {
                        "desktopEnabled": database.get_setting("notification.desktop.enabled", "true") == "true",
                        "emailEnabled": database.get_setting("notification.email.enabled", "false") == "true",
                        "host": database.get_setting("smtp.host", "") or "",
                        "port": int(database.get_setting("smtp.port", "465") or "465"),
                        "username": database.get_setting("smtp.username", "") or "",
                        "sender": database.get_setting("smtp.sender", "") or "",
                        "recipient": database.get_setting("smtp.recipient", "") or "",
                        "useSsl": database.get_setting("smtp.use_ssl", "true") == "true",
                    },
                },
                ensure_ascii=False,
            )

        @QtCore.Slot(str, result=str)
        def savePlan(self, payload_text: str) -> str:
            try:
                payload = json.loads(payload_text)
                existing = database.get_plan(UUID(payload["id"])) if payload.get("id") else None
                plan = Plan(
                    id=existing.id if existing else uuid4(),
                    name=payload["name"].strip(),
                    purchase_code=payload["purchaseCode"].strip(),
                    purchase_name=payload["purchaseName"].strip(),
                    signal_code=payload["signalCode"].strip(),
                    signal_name=payload["signalName"].strip(),
                    base_amount=Decimal(str(payload["baseAmount"])),
                    invest_weekday=int(payload["weekday"]),
                    recurring_enabled=bool(payload["recurringEnabled"]),
                    drawdown_enabled=bool(payload["drawdownEnabled"]),
                    enabled=True,
                )
                levels = tuple(
                    StrategyLevel(
                        threshold=Decimal(str(item["threshold"])),
                        multiplier=Decimal(str(item["multiplier"])),
                        execution_mode=ExecutionMode(item.get("executionMode", "once")),
                        phases=2 if item.get("executionMode") == "phased" else 1,
                        enabled=bool(item.get("enabled", True)),
                        max_executions=(
                            int(item["maxExecutions"])
                            if item.get("maxExecutions") else None
                        ),
                        cycle_amount_cap=(
                            Decimal(str(item["cycleAmountCap"]))
                            if item.get("cycleAmountCap") else None
                        ),
                        cycle_multiplier_cap=(
                            Decimal(str(item["cycleMultiplierCap"]))
                            if item.get("cycleMultiplierCap") else None
                        ),
                    )
                    for item in payload.get("levels", [])
                )
                plan.validate()
                if existing:
                    current = database.get_active_strategy(existing.id)
                    database.update_plan(plan)
                    if levels and tuple(
                        (
                            level.threshold, level.multiplier, level.execution_mode,
                            level.enabled, level.max_executions, level.cycle_amount_cap,
                            level.cycle_multiplier_cap,
                        )
                        for level in levels
                    ) != tuple(
                        (
                            level.threshold, level.multiplier, level.execution_mode,
                            level.enabled, level.max_executions, level.cycle_amount_cap,
                            level.cycle_multiplier_cap,
                        )
                        for level in current.levels
                    ):
                        database.save_strategy(
                            Strategy(plan.id, current.version + 1, levels, EffectiveMode.IMMEDIATE)
                        )
                else:
                    strategy = Strategy(plan.id, 1, levels, EffectiveMode.IMMEDIATE) if levels else default_strategy(plan.id)
                    database.add_plan_with_strategy(plan, strategy)
                self.stateChanged.emit()
                return ""
            except Exception as error:
                return str(error)

        @QtCore.Slot(str, bool, bool, result=str)
        def setPlanFlags(self, plan_id: str, recurring_enabled: bool, drawdown_enabled: bool) -> str:
            try:
                current = database.get_plan(UUID(plan_id))
                database.update_plan(
                    Plan(
                        id=current.id,
                        name=current.name,
                        purchase_code=current.purchase_code,
                        purchase_name=current.purchase_name,
                        signal_code=current.signal_code,
                        signal_name=current.signal_name,
                        base_amount=current.base_amount,
                        invest_weekday=current.invest_weekday,
                        recurring_enabled=recurring_enabled,
                        drawdown_enabled=drawdown_enabled,
                        enabled=current.enabled,
                    )
                )
                self.stateChanged.emit()
                return ""
            except Exception as error:
                return str(error)

        @QtCore.Slot(str, result=str)
        def resumeCycle(self, plan_id: str) -> str:
            try:
                database.resume_cycle(UUID(plan_id))
                self.stateChanged.emit()
                return ""
            except Exception as error:
                return str(error)

        def _save_email(self, payload: dict[str, object]) -> None:
            required = {
                "SMTP 服务器": str(payload.get("host", "")).strip(),
                "登录账号": str(payload.get("username", "")).strip(),
                "发件地址": str(payload.get("sender", "")).strip(),
                "收件地址": str(payload.get("recipient", "")).strip(),
            }
            credentials = LocalCredentialStore()
            password = str(payload.get("password", "")) or credentials.get("smtp.password")
            if payload.get("emailEnabled"):
                missing = [label for label, value in required.items() if not value]
                if not password:
                    missing.append("邮箱授权码")
                if missing:
                    raise ValueError("请填写：" + "、".join(missing))
            database.set_setting("notification.desktop.enabled", str(bool(payload.get("desktopEnabled"))).lower())
            database.set_setting("notification.email.enabled", str(bool(payload.get("emailEnabled"))).lower())
            database.set_setting("smtp.host", required["SMTP 服务器"])
            database.set_setting("smtp.port", str(int(payload.get("port", 465))))
            database.set_setting("smtp.username", required["登录账号"])
            database.set_setting("smtp.sender", required["发件地址"])
            database.set_setting("smtp.recipient", required["收件地址"])
            database.set_setting("smtp.use_ssl", str(bool(payload.get("useSsl", True))).lower())
            if payload.get("password"):
                credentials.set("smtp.password", str(payload["password"]))

        @QtCore.Slot(str, result=str)
        def saveEmail(self, payload_text: str) -> str:
            try:
                self._save_email(json.loads(payload_text))
                self.stateChanged.emit()
                return ""
            except Exception as error:
                return str(error)

        @QtCore.Slot(str, result=str)
        def testEmail(self, payload_text: str) -> str:
            try:
                payload = json.loads(payload_text)
                self._save_email(payload)
                password = LocalCredentialStore().get("smtp.password")
                if not password:
                    raise RuntimeError("尚未保存邮箱授权码")
                SmtpEmailNotifier(
                    host=str(payload["host"]).strip(),
                    port=int(payload["port"]),
                    username=str(payload["username"]).strip(),
                    password=password,
                    sender=str(payload["sender"]).strip(),
                    recipient=str(payload["recipient"]).strip(),
                    use_ssl=bool(payload.get("useSsl", True)),
                ).send("ETF 投资助手测试邮件", "邮箱通知已配置成功。此邮件由本机 ETF 投资助手发送。")
                self.stateChanged.emit()
                return ""
            except Exception as error:
                return str(error)

        @QtCore.Slot(str, str, result=str)
        def updateEvent(self, event_id: str, action: str) -> str:
            try:
                if action == "executed":
                    row = database.event_row(UUID(event_id))
                    database.update_event_action(
                        UUID(event_id),
                        execution_status=ExecutionStatus.EXECUTED,
                        executed_amount=Decimal(row["total_amount"]),
                    )
                elif action == "ignored":
                    database.update_event_action(UUID(event_id), state=EventState.IGNORED)
                elif action == "postponed":
                    database.update_event_action(UUID(event_id), state=EventState.POSTPONED)
                self.stateChanged.emit()
                return ""
            except Exception as error:
                return str(error)

        @QtCore.Slot(result=str)
        def runCheck(self) -> str:
            if self._check_running:
                return "busy"
            self._check_running = True
            threading.Thread(target=self._run_check, daemon=True).start()
            return "started"

        def _run_check(self) -> None:
            try:
                from .runtime import run_daily_check

                result = run_daily_check(database, force=True)
                self.stateChanged.emit()
                message = _check_result_message(result)
            except Exception as error:
                message = _friendly_error(error)
            finally:
                self._check_running = False
            self.checkFinished.emit(message)

        @QtCore.Slot(str, result=str)
        def loadChart(self, plan_id: str) -> str:
            if plan_id in self._chart_running:
                return "busy"
            self._chart_running.add(plan_id)
            threading.Thread(target=self._load_chart, args=(plan_id,), daemon=True).start()
            return "started"

        def _load_chart(self, plan_id: str) -> None:
            payload: dict[str, object]
            try:
                from .providers.market import AkshareMarketProvider

                plan = database.get_plan(UUID(plan_id))
                candles = AkshareMarketProvider().daily_candles(plan.signal_code, 30)
                if not candles:
                    raise RuntimeError("empty chart")
                payload = {"candles": candles}
            except Exception as error:
                payload = {"candles": [], "error": _friendly_error(error)}
            finally:
                self._chart_running.discard(plan_id)
            self.chartLoaded.emit(plan_id, json.dumps(payload, ensure_ascii=False))

        @QtCore.Slot(result=str)
        def exportData(self) -> str:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                None, "导出迁移包", "etf-backup.etfbak", "ETF 迁移包 (*.etfbak)"
            )
            if not path:
                return ""
            try:
                export_backup(database, Path(path))
                return "迁移包已保存到：" + path
            except Exception as error:
                return "导出失败：" + str(error)

        @QtCore.Slot(result=str)
        def restoreData(self) -> str:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                None, "选择迁移包", "", "ETF 迁移包 (*.etfbak)"
            )
            if not path:
                return ""
            try:
                rollback = restore_backup(Path(path), database)
                self.stateChanged.emit()
                return f"数据已恢复；回滚副本：{rollback or '无'}"
            except Exception as error:
                return "恢复失败：" + str(error)

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("ETF 投资助手")
    window = QtWidgets.QMainWindow()
    window.setWindowTitle("ETF 投资助手")
    window.resize(1360, 860)
    window.setMinimumSize(1080, 700)
    view = QtWebEngineWidgets.QWebEngineView(window)
    channel = QtWebChannel.QWebChannel(view.page())
    bridge = Bridge()
    channel.registerObject("backend", bridge)
    view.page().setWebChannel(channel)
    view.setUrl(QtCore.QUrl.fromLocalFile(str(Path(__file__).with_name("web") / "index.html")))
    window.setCentralWidget(view)
    window.show()
    window._bridge = bridge
    window._channel = channel
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
