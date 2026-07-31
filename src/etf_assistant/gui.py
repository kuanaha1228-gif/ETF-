from __future__ import annotations

import json
import platform
import sys
import threading
from datetime import date
from decimal import Decimal
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
    RecoveryLevel,
    Strategy,
    StrategyLevel,
    TakeProfitLevel,
    ValuationType,
)
from .presets import install_prd_plan_presets, install_v17_take_profit_rules
from .providers.credentials import LocalCredentialStore
from .providers.market import EastmoneyFundNavProvider
from .providers.notify import SmtpEmailNotifier
from .scheduler import ensure_scheduler
from .strategy import STRATEGY_TEMPLATES, default_strategy, take_profit_target


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


def _decimal_input(value: object, label: str) -> Decimal:
    text = str(value).strip().translate(
        str.maketrans({"，": ".", ",": ".", "。": ".", "．": "."})
    )
    if text.startswith("."):
        text = "0" + text
    if not text or text.count(".") > 1:
        raise ValueError(f"{label}格式不正确")
    whole, separator, fraction = text.partition(".")
    if not whole.isdigit() or (separator and (not fraction.isdigit() or len(fraction) > 6)):
        raise ValueError(f"{label}最多支持 6 位小数")
    return Decimal(text)


def _check_result_message(result) -> str:
    message = f"已更新 {result.checked_plans} 项行情；未执行定投、补仓或止盈策略"
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


def _database_path_from_arguments(arguments: list[str]) -> Path:
    if "--db" not in arguments:
        return default_database_path()
    index = arguments.index("--db")
    if index + 1 >= len(arguments):
        raise ValueError("--db requires a database path")
    return Path(arguments[index + 1]).expanduser()


def _plan_payload(database: Database, plan: Plan) -> dict[str, object]:
    strategy = database.get_active_strategy(plan.id)
    cycle = database.get_active_cycle(plan.id)
    take_profit_cycle = database.current_take_profit_cycle(plan.id)
    position = database.current_position(plan.id)
    latest_nav = database.latest_official_nav(plan.id)
    holding_transactions = database.holding_transactions(plan.id)
    take_profit_levels = database.take_profit_levels(plan.id)
    recovery_levels = database.recovery_levels(plan.id)
    executed_take_profit = (
        database.executed_take_profit_level_ids(UUID(take_profit_cycle["id"]))
        if take_profit_cycle else set()
    )
    return {
        "id": str(plan.id),
        "name": plan.name,
        "purchaseCode": plan.purchase_code,
        "purchaseName": plan.purchase_name,
        "signalCode": plan.signal_code,
        "signalName": plan.signal_name,
        "baseAmount": str(plan.base_amount),
        "currentAmount": str(plan.recurring_amount),
        "weekday": plan.invest_weekday,
        "weekdayLabel": WEEKDAYS[plan.invest_weekday],
        "recurringEnabled": plan.recurring_enabled,
        "drawdownEnabled": plan.drawdown_enabled,
        "enabled": plan.enabled,
        "pausedForReview": bool(cycle["paused_for_review"]) if cycle else False,
        "cyclePeak": cycle["cycle_peak"] if cycle else None,
        "takeProfit": {
            "cycleId": take_profit_cycle["id"] if take_profit_cycle else None,
            "costBasis": position["average_cost"],
            "actualUnits": position["actual_units"],
            "totalCost": position["total_cost"],
            "positionUpdatedAt": position["updated_at"],
            "positionReviewStatus": position["review_status"],
            "basisStatus": (
                take_profit_cycle["basis_status"] if take_profit_cycle else "draft"
            ),
            "effectiveDate": (
                take_profit_cycle["effective_date"] if take_profit_cycle else None
            ),
            "peak": (
                take_profit_cycle["take_profit_peak"] if take_profit_cycle else None
            ),
            "state": take_profit_cycle["state"] if take_profit_cycle else "preparing",
            "previousOfficialNav": (
                latest_nav["official_nav"] if latest_nav else None
            ),
            "previousOfficialNavDate": (
                latest_nav["nav_date"] if latest_nav else None
            ),
            "officialDailyChange": (
                latest_nav["daily_change"] if latest_nav else None
            ),
            "officialNavSource": latest_nav["source"] if latest_nav else None,
            "valuationType": (
                take_profit_cycle["valuation_type"]
                if take_profit_cycle else ValuationType.STANDARD.value
            ),
            "basisReviewStatus": position["review_status"],
            "holdingTransactions": [
                {
                    "id": row["id"],
                    "eventId": row["event_id"],
                    "navDate": row["nav_date"],
                    "grossAmount": row["gross_amount"],
                    "feeAmount": row["fee_amount"],
                    "officialNav": row["official_nav"],
                    "units": row["units"],
                    "status": row["status"],
                }
                for row in holding_transactions
            ],
            "levels": [
                {
                    "id": str(level.id),
                    "profitRate": str(level.profit_rate),
                    "sellRatio": str(level.sell_ratio),
                    "sellAll": level.sell_all,
                    "nextRecurringAmount": str(level.next_recurring_amount),
                    "targetPrice": (
                        str(
                            take_profit_target(
                                Decimal(position["average_cost"]),
                                level.profit_rate,
                            )
                        )
                        if position["average_cost"] is not None
                        else None
                    ),
                    "executed": level.id in executed_take_profit,
                }
                for level in take_profit_levels
            ],
            "recoveryLevels": [
                {
                    "id": str(level.id),
                    "drawdown": str(level.drawdown),
                    "recurringAmount": (
                        str(level.recurring_amount)
                        if level.recurring_amount is not None else None
                    ),
                }
                for level in recovery_levels
            ],
        },
        "levels": [
                {
                    "threshold": str(level.threshold),
                    "multiplier": str(level.multiplier),
                    "executionMode": level.execution_mode.value,
                    "phases": level.phases,
                    "intervalWeeks": level.interval_weeks,
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


def _parse_take_profit_rules(
    payload_text: str,
) -> tuple[tuple[TakeProfitLevel, ...], tuple[RecoveryLevel, ...]]:
    payload = json.loads(payload_text)
    levels = tuple(
        TakeProfitLevel(
            profit_rate=Decimal(str(item["profitRate"])),
            sell_ratio=(
                Decimal("100")
                if item.get("sellAll")
                else Decimal(str(item["sellRatio"]))
            ),
            sell_all=bool(item.get("sellAll")),
            next_recurring_amount=Decimal(str(item["nextRecurringAmount"])),
        )
        for item in payload.get("levels", [])
    )
    recovery = []
    for item in payload.get("recoveryLevels", []):
        drawdown = str(item.get("drawdown", "")).strip()
        recurring_amount = str(item.get("recurringAmount", "")).strip()
        if not drawdown or not recurring_amount:
            raise ValueError("每个恢复档位必须同时填写回撤阈值和恢复金额")
        recovery.append(
            RecoveryLevel(
                drawdown=Decimal(drawdown),
                recurring_amount=Decimal(recurring_amount),
            )
        )
    return levels, tuple(recovery)


def main() -> int:
    database = Database(_database_path_from_arguments(sys.argv[1:]))
    database.initialize()
    install_prd_plan_presets(database)
    install_v17_take_profit_rules(database)
    if "--daily-check" in sys.argv:
        from .runtime import run_daily_check

        try:
            result = run_daily_check(database, force="--force" in sys.argv)
            return 0 if not result.errors else 1
        except Exception:
            # The database already contains a non-sensitive failure summary.
            return 1

    scheduler_ready = False
    scheduler_error = ""
    if getattr(sys, "frozen", False) and platform.system() == "Darwin":
        try:
            ensure_scheduler([sys.executable, "--daily-check"])
            scheduler_ready = True
        except Exception as error:
            scheduler_error = str(error)

    QtCore, QtWebChannel, QtWebEngineWidgets, QtWidgets = _load_qt()

    class Bridge(QtCore.QObject):
        stateChanged = QtCore.Signal()
        checkFinished = QtCore.Signal(str)
        chartLoaded = QtCore.Signal(str, str)
        navSyncFinished = QtCore.Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self._check_running = False
            self._chart_running: set[str] = set()
            self._nav_sync_running: set[str] = set()

        @QtCore.Slot(result=str)
        def getState(self) -> str:
            plans = database.list_plans()
            events = database.recent_events(100)
            latest_check = database.latest_check_run()
            market = {
                row["plan_id"]: {
                    "price": row["quote_price"],
                    "quoteTime": row["quote_time"],
                    "dailyChange": row["daily_change"],
                    "highestClose": row["highest_close"],
                    "highestCloseDate": row["highest_close_date"],
                    "drawdown": row["drawdown"],
                    "cyclePeak": row["cycle_peak"],
                    "cycleDrawdown": row["cycle_drawdown"],
                    "strategyStage": row["strategy_stage"],
                    "decisionPeakType": row["decision_peak_type"],
                    "decisionPeakPrice": row["decision_peak_price"],
                    "decisionPeakDate": row["decision_peak_date"],
                    "takeProfitPeak": row["take_profit_peak"],
                    "takeProfitDrawdown": row["take_profit_drawdown"],
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
                            "planId": row["plan_id"],
                            "planName": row["plan_name"],
                            "quoteTime": row["quote_time"],
                            "tradingDate": row["trading_date"],
                            "drawdown": str(row["drawdown"]),
                            "eventType": row["event_type"],
                            "decisionPeakType": row["decision_peak_type"],
                            "decisionPeakPrice": row["decision_peak_price"],
                            "threshold": row["threshold_snapshot"],
                            "regularAmount": str(row["regular_amount"]),
                            "extraAmount": str(row["extra_amount"]),
                            "totalAmount": str(row["total_amount"]),
                            "state": row["state"],
                            "executionStatus": row["execution_status"],
                            "executedAmount": row["executed_amount"],
                            "executedAt": row["executed_at"],
                            "takeProfitCostBasis": row["take_profit_cost_basis"],
                            "takeProfitTargetPrice": row["take_profit_target_price"],
                            "sellRatio": row["sell_ratio_snapshot"],
                            "plannedSellUnits": row["planned_sell_units"],
                            "actualSellUnits": row["actual_sell_units"],
                            "recurringAmountBefore": row["recurring_amount_before"],
                            "recurringAmountAfter": row["recurring_amount_after"],
                            "valuationStatus": row["valuation_status"],
                            "valuationType": row["valuation_type"],
                            "estimatedNav": row["estimated_nav"],
                            "referenceNav": row["reference_nav"],
                            "referenceNavDate": row["reference_nav_date"],
                            "signalPreviousClose": row["signal_previous_close"],
                            "signalIntradayReturn": row["signal_intraday_return"],
                            "officialNav": row["official_nav"],
                            "officialNavDate": row["official_nav_date"],
                            "holdingStatus": row["holding_status"],
                            "holdingNavDate": row["holding_nav_date"],
                            "holdingFeeAmount": row["holding_fee_amount"],
                            "holdingUnits": row["holding_units"],
                        }
                        for row in events
                    ],
                    "dailySummaries": [
                        {
                            "id": row["id"],
                            "tradingDate": row["trading_date"],
                            "recipient": row["email_recipient"],
                            "subject": row["subject"],
                            "state": row["state"],
                            "attempts": row["attempts"],
                            "sentAt": row["sent_at"],
                            "errorSummary": row["error_summary"],
                        }
                        for row in database.recent_daily_summaries()
                    ],
                    "runtime": {
                        "schedulerInstalled": scheduler_ready,
                        "schedulerError": scheduler_error,
                        "lastCheck": (
                            {
                                "startedAt": latest_check["started_at"],
                                "finishedAt": latest_check["finished_at"],
                                "state": latest_check["state"],
                                "checkedPlans": latest_check["checked_plans"],
                                "createdEvents": latest_check["created_events"],
                                "errorSummary": latest_check["error_summary"],
                            }
                            if latest_check
                            else None
                        ),
                    },
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
                    current_amount=Decimal(
                        str(payload.get("currentAmount", payload["baseAmount"]))
                    ),
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
                        phases=(
                            int(item.get("phases", 2))
                            if item.get("executionMode") == "phased"
                            else 1
                        ),
                        interval_weeks=(
                            int(item.get("intervalWeeks", 1))
                            if item.get("executionMode") == "phased"
                            else 1
                        ),
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
                    strategy_changed = tuple(
                        (
                            level.threshold, level.multiplier, level.execution_mode,
                            level.phases, level.interval_weeks, level.enabled,
                            level.max_executions, level.cycle_amount_cap,
                            level.cycle_multiplier_cap,
                        )
                        for level in levels
                    ) != tuple(
                        (
                            level.threshold, level.multiplier, level.execution_mode,
                            level.phases, level.interval_weeks, level.enabled,
                            level.max_executions, level.cycle_amount_cap,
                            level.cycle_multiplier_cap,
                        )
                        for level in current.levels
                    )
                    next_strategy = (
                        Strategy(
                            plan.id,
                            current.version + 1,
                            levels,
                            EffectiveMode.IMMEDIATE,
                        )
                        if strategy_changed
                        else None
                    )
                    if next_strategy:
                        next_strategy.validate()
                    database.update_plan(plan)
                    if next_strategy:
                        database.save_strategy(next_strategy)
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
                        current_amount=current.current_amount,
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

        @QtCore.Slot(str, result=str)
        def archivePlan(self, plan_id: str) -> str:
            try:
                database.archive_plan(UUID(plan_id))
                self.stateChanged.emit()
                return ""
            except Exception as error:
                return str(error)

        @QtCore.Slot(str, str, result=str)
        def saveTakeProfitRules(self, plan_id: str, payload_text: str) -> str:
            try:
                levels, recovery = _parse_take_profit_rules(payload_text)
                database.replace_take_profit_rules(UUID(plan_id), levels, recovery)
                self.stateChanged.emit()
                return ""
            except Exception as error:
                return str(error)

        @QtCore.Slot(str, str, bool, bool, result=str)
        def saveTakeProfitBasis(
            self,
            plan_id: str,
            payload_text: str,
            lock: bool,
            confirm_correction: bool,
        ) -> str:
            try:
                payload = json.loads(payload_text)
                database.save_take_profit_basis(
                    UUID(plan_id),
                    actual_units=_decimal_input(
                        payload.get("actualUnits", ""), "当前实际持有份额"
                    ),
                    cost_basis=_decimal_input(
                        payload.get("costBasis", ""), "当前平均持仓成本"
                    ),
                    lock=lock,
                    confirm_correction=confirm_correction,
                    previous_official_nav=(
                        _decimal_input(
                            payload.get("previousOfficialNav", ""),
                            "场外官方净值",
                        )
                        if str(payload.get("previousOfficialNav", "")).strip()
                        else None
                    ),
                    previous_official_nav_date=(
                        date.fromisoformat(payload["previousOfficialNavDate"])
                        if payload.get("previousOfficialNavDate") else None
                    ),
                    valuation_type=ValuationType(
                        payload.get("valuationType", ValuationType.STANDARD.value)
                    ),
                )
                self.stateChanged.emit()
                return ""
            except Exception as error:
                return str(error)

        @QtCore.Slot(str, str, str, result=str)
        def recordOfficialNav(
            self, plan_id: str, nav_date: str, official_nav: str
        ) -> str:
            try:
                reconciled = database.record_official_nav(
                    UUID(plan_id),
                    nav_date=date.fromisoformat(nav_date),
                    official_nav=_decimal_input(official_nav, "场外官方净值"),
                )
                self.stateChanged.emit()
                return f"官方净值已保存，已处理 {reconciled} 条待复核/待结算记录"
            except Exception as error:
                return str(error)

        @QtCore.Slot(str, str, result=str)
        def confirmTakeProfit(self, event_id: str, actual_units: str) -> str:
            try:
                database.confirm_take_profit_execution(
                    UUID(event_id),
                    actual_sell_units=_decimal_input(actual_units, "实际卖出份额"),
                )
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
                    if row["event_type"] in {"recurring", "drawdown", "combined"}:
                        return "请使用“确认已投入”填写金额、费用和净值归属日"
                    database.update_event_action(
                        UUID(event_id),
                        execution_status=ExecutionStatus.EXECUTED,
                        executed_amount=Decimal(row["total_amount"]),
                    )
                elif action == "ignored":
                    database.update_event_action(UUID(event_id), state=EventState.IGNORED)
                elif action == "postponed":
                    database.update_event_action(UUID(event_id), state=EventState.POSTPONED)
                elif action == "not_executed":
                    database.update_event_action(
                        UUID(event_id),
                        execution_status=ExecutionStatus.NOT_EXECUTED,
                    )
                self.stateChanged.emit()
                return ""
            except Exception as error:
                return str(error)

        @QtCore.Slot(str, str, str, str, result=str)
        def confirmInvestment(
            self,
            event_id: str,
            gross_amount: str,
            fee_amount: str,
            nav_date: str,
        ) -> str:
            try:
                status = database.confirm_investment(
                    UUID(event_id),
                    gross_amount=_decimal_input(gross_amount, "实际投入金额"),
                    fee_amount=_decimal_input(fee_amount, "申购费用"),
                    nav_date=date.fromisoformat(nav_date),
                )
                self.stateChanged.emit()
                return (
                    "已按对应日官方净值结算，持仓份额和平均成本已更新"
                    if status == "settled"
                    else "已确认投入；对应日官方净值尚未公布，当前等待净值结算"
                )
            except Exception as error:
                return str(error)

        @QtCore.Slot(str, result=str)
        def syncFundNav(self, plan_id: str) -> str:
            if plan_id in self._nav_sync_running:
                return "busy"
            self._nav_sync_running.add(plan_id)
            threading.Thread(
                target=self._sync_fund_nav,
                args=(plan_id,),
                daemon=True,
            ).start()
            return "started"

        def _sync_fund_nav(self, plan_id: str) -> None:
            try:
                plan = database.get_plan(UUID(plan_id))
                nav = EastmoneyFundNavProvider().latest_nav(plan.purchase_code)
                affected = database.record_official_nav(
                    plan.id,
                    nav_date=nav.nav_date,
                    official_nav=nav.unit_nav,
                    daily_change=nav.daily_change,
                    source=nav.source,
                    fetched_at=nav.fetched_at,
                )
                self.stateChanged.emit()
                message = (
                    f"已同步 {nav.nav_date.isoformat()} 单位净值 {nav.unit_nav}"
                    + (f"，并处理 {affected} 条待复核/待结算记录" if affected else "")
                )
            except Exception as error:
                message = str(error)
            finally:
                self._nav_sync_running.discard(plan_id)
            self.navSyncFinished.emit(message)

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
