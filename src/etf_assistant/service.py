from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from .db import Database
from .domain import (
    DecisionPeakType,
    DeliveryState,
    EventState,
    ExecutionMode,
    Quote,
    StrategyLevel,
    StrategyStage,
    ValuationStatus,
    ValuationType,
)
from .providers.market import FundNavProvider, MarketProvider, TradingCalendar
from .providers.notify import NotificationProvider
from .strategy import (
    choose_level,
    drawdown_from_peak,
    estimate_linked_fund_nav,
    planned_amount,
    planned_sell_units,
    rolling_high,
    take_profit_target,
)


@dataclass(frozen=True, slots=True)
class CheckResult:
    checked_plans: int
    created_events: tuple[UUID, ...]
    errors: tuple[str, ...]
    summary_id: UUID | None = None


def iso_week_key(value: date) -> str:
    year, week, _ = value.isocalendar()
    return f"{year}-W{week:02d}"


def event_key(
    plan_id: UUID,
    value: date,
    cycle_id: UUID | None,
    discriminator: str,
) -> str:
    return f"{plan_id}:{value.isoformat()}:{cycle_id or 'none'}:{discriminator}"


def _phase_is_due(
    database: Database, cycle_id: UUID, level: StrategyLevel, today: date
) -> bool:
    delivered = database.notified_threshold_events(cycle_id, level.threshold)
    if level.execution_mode is ExecutionMode.ONCE:
        return not delivered
    if level.execution_mode is ExecutionMode.PHASED:
        if len(delivered) >= level.phases:
            return False
        if not delivered:
            return True
        latest = max(date.fromisoformat(row["trading_date"]) for row in delivered)
        return today >= latest + timedelta(weeks=level.interval_weeks)
    if not delivered:
        return True
    latest = max(date.fromisoformat(row["trading_date"]) for row in delivered)
    return iso_week_key(latest) != iso_week_key(today)


def _continuous_budget_allows(
    database: Database,
    cycle_id: UUID,
    level: StrategyLevel,
    next_amount: Decimal,
) -> bool:
    if level.execution_mode is not ExecutionMode.WEEKLY_WHILE_DEEP:
        return True
    count, amount, multiplier = database.cycle_budget_usage(cycle_id)
    if level.max_executions is not None and count >= level.max_executions:
        return False
    if level.cycle_amount_cap is not None and amount + next_amount > level.cycle_amount_cap:
        return False
    return not (
        level.cycle_multiplier_cap is not None
        and multiplier + level.multiplier > level.cycle_multiplier_cap
    )


def _continuous_budget_exhausted(
    database: Database, cycle_id: UUID, level: StrategyLevel
) -> bool:
    if level.execution_mode is not ExecutionMode.WEEKLY_WHILE_DEEP:
        return False
    count, amount, multiplier = database.cycle_budget_usage(cycle_id)
    return bool(
        (level.max_executions is not None and count >= level.max_executions)
        or (level.cycle_amount_cap is not None and amount >= level.cycle_amount_cap)
        or (
            level.cycle_multiplier_cap is not None
            and multiplier >= level.cycle_multiplier_cap
        )
    )


def _render_event(plan, row) -> tuple[str, str]:
    event_type = row["event_type"]
    if event_type == "take_profit":
        high_error = row["valuation_type"] == ValuationType.QDII_HIGH_ERROR.value
        if row["valuation_status"] == ValuationStatus.NAV_CONFIRMED.value:
            return (
                f"【ETF止盈确认】{plan.name}官方净值达到预设位置",
                (
                    f"场外官方净值：{row['official_nav']}"
                    f"（{row['official_nav_date']}）\n"
                    f"本轮场外止盈基准：{row['take_profit_cost_basis']}\n"
                    f"目标净值：{row['take_profit_target_price']}（收益"
                    f"{row['threshold_snapshot']}%）\n"
                    f"计划卖出：当前实际持仓的 {row['sell_ratio_snapshot']}%，"
                    f"约 {row['planned_sell_units']} 份\n"
                    f"确认实际卖出后，定投金额由 {row['recurring_amount_before']} 元"
                    f"调整为 {row['recurring_amount_after']} 元。\n\n"
                    "该提醒用于补充14:50后才达到的止盈位置；当前盘中可能已经回落，"
                    "请结合最新行情自行决定是否执行。"
                ),
            )
        title = f"【ETF止盈预警】{plan.name}盘中估算达到预设位置"
        body = (
            f"观察 ETF：{plan.signal_name or plan.signal_code}（{plan.signal_code}）\n"
            f"最新价：{row['quote_price']}，行情时间：{row['quote_time'][:19]}\n"
            f"场外上一期官方净值：{row['reference_nav']}"
            f"（{row['reference_nav_date']}）\n"
            f"场内 ETF 昨收：{row['signal_previous_close']}，盘中涨跌："
            f"{Decimal(row['signal_intraday_return']) * 100:.2f}%\n"
            f"场外当日估算净值：{row['estimated_nav']}\n"
            f"本轮场外止盈基准：{row['take_profit_cost_basis']}\n"
            f"目标净值：{row['take_profit_target_price']}（收益"
            f"{row['threshold_snapshot']}%）\n"
            f"计划卖出：当前实际持仓的 {row['sell_ratio_snapshot']}%，"
            f"约 {row['planned_sell_units']} 份\n"
            f"确认实际卖出后，定投金额由 {row['recurring_amount_before']} 元调整为"
            f" {row['recurring_amount_after']} 元。\n\n"
            + (
                "该计划为 QDII 或高误差估值，交易时段、汇率、溢折价和跟踪误差"
                "可能造成较大偏差，仅作预警。\n"
                if high_error else ""
            )
            + "本次仅为盘中估算预警，待基金公司公布官方净值后复核，请自行决定是否执行。"
        )
        return title, body
    if event_type == "recovery":
        return (
            f"【ETF定投恢复】{plan.name}达到预设回撤档",
            (
                f"止盈阶段高点：{row['decision_peak_price']}\n"
                f"当前价：{row['quote_price']}，止盈后回撤：{Decimal(row['drawdown']):.2f}%\n"
                f"当前阶段定投由 {row['recurring_amount_before']} 元调整为"
                f" {row['recurring_amount_after']} 元。\n\n"
                "本次为盘中估算，不代表最终收盘价格。"
            ),
        )
    level = row["threshold_snapshot"]
    if row["state"] == EventState.SUPPRESSED.value:
        return (
            f"【ETF风险升级】{plan.name}触发{level}%档",
            (
                f"最新价：{row['quote_price']}，实际采用"
                f" {row['decision_peak_type']}={row['decision_peak_price']}，"
                f"回撤 {Decimal(row['drawdown']):.2f}%。\n"
                "本周已经发送过一次补仓计划，本次新增金额 0 元。"
            ),
        )
    return (
        f"【ETF计划提醒】{plan.name}",
        (
            f"最新价：{row['quote_price']}，行情时间：{row['quote_time'][:19]}\n"
            f"判断基准：{row['decision_peak_type'] or 'rolling_20d_high'} "
            f"{row['decision_peak_price'] or row['highest_close']}\n"
            f"回撤：{Decimal(row['drawdown']):.2f}%\n"
            f"固定定投：{row['regular_amount']} 元\n"
            f"额外补仓：{row['extra_amount']} 元\n"
            f"今日计划合计：{row['total_amount']} 元\n\n"
            "本消息按你预设的规则计算，为盘中估算，不构成投资建议。"
        ),
    )


def _summary_text(
    today: date,
    checked_at: datetime,
    items: list[dict[str, Any]],
    errors: list[str],
) -> tuple[str, str, dict[str, object]]:
    action_items = [
        item for item in items if item["events"]
    ]
    buy_count = sum(
        1
        for item in items
        for event in item["events"]
        if event["event_type"] in {"drawdown", "combined"}
        and Decimal(event["extra_amount"]) > 0
    )
    take_profit_count = sum(
        1
        for item in items
        for event in item["events"]
        if event["event_type"] == "take_profit"
    )
    has_action = bool(action_items)
    subject = (
        f"【ETF每日提醒】{today.isoformat()}｜补仓{buy_count}只｜止盈{take_profit_count}只"
        if has_action
        else f"【ETF每日汇总】{today.isoformat()}｜今日无操作"
    )
    lines = [
        "ETF 每日汇总",
        "",
        f"检查时间：{checked_at:%Y-%m-%d %H:%M:%S}",
        f"补仓触发：{buy_count}只",
        f"止盈触发：{take_profit_count}只",
        "",
    ]
    if not has_action:
        lines.append("今日未触发定投、补仓或止盈。")
        lines.append("")
    regular_total = Decimal("0")
    extra_total = Decimal("0")
    for item in items:
        lines.extend(
            [
                f"{item['name']}（{item['signal_code']}）",
                f"最新价：{item['quote_price']}",
                (
                    f"20日最高：{item['rolling_high']}（{item['rolling_high_date']}）"
                    f"｜回撤：{Decimal(item['short_drawdown']):.2f}%"
                ),
                f"下一止盈价：{item['next_take_profit_target'] or '未设置'}",
            ]
        )
        for event in item["events"]:
            regular_total += Decimal(event["regular_amount"])
            extra_total += Decimal(event["extra_amount"])
            if event["event_type"] == "take_profit":
                if event["valuation_status"] == ValuationStatus.NAV_CONFIRMED.value:
                    lines.append(
                        f"止盈确认：官方净值 {event['official_nav']}"
                        f"（{event['official_nav_date']}）｜基准"
                        f" {event['take_profit_cost_basis']}｜目标净值"
                        f" {event['take_profit_target_price']}｜卖出"
                        f" {event['sell_ratio_snapshot']}%（计划"
                        f" {event['planned_sell_units']}份）"
                    )
                else:
                    lines.append(
                        f"止盈预警：上一期官方净值 {event['reference_nav']}"
                        f"（{event['reference_nav_date']}）｜场内昨收"
                        f" {event['signal_previous_close']}｜盘中涨跌"
                        f" {Decimal(event['signal_intraday_return']) * 100:.2f}%｜"
                        f"场外估算净值 {event['estimated_nav']}｜基准"
                        f" {event['take_profit_cost_basis']}｜目标净值"
                        f" {event['take_profit_target_price']}｜估值状态"
                        f" {event['valuation_status']}｜卖出"
                        f" {event['sell_ratio_snapshot']}%（计划"
                        f" {event['planned_sell_units']}份）"
                    )
                if event["valuation_type"] == ValuationType.QDII_HIGH_ERROR.value:
                    lines.append(
                        "高误差提示：该计划受交易时段、汇率、溢折价和跟踪误差"
                        "影响，本次仅作盘中预警。"
                    )
            elif event["event_type"] == "recovery":
                lines.append(
                    f"恢复定投：{event['recurring_amount_before']} → "
                    f"{event['recurring_amount_after']} 元"
                )
            elif event["state"] == EventState.SUPPRESSED.value:
                lines.append(
                    f"风险升级：进入 {event['threshold_snapshot']}% 档；"
                    "本周已补仓，本次新增金额 0 元"
                )
            else:
                lines.append(
                    f"固定定投 {event['regular_amount']} 元｜额外补仓"
                    f" {event['extra_amount']} 元"
                )
        lines.append("")
    if errors:
        lines.extend(["检查失败：", *errors, ""])
    lines.extend(
        [
            f"固定定投合计：{regular_total}元",
            f"额外补仓合计：{extra_total}元",
            f"今日计划合计：{regular_total + extra_total}元",
            "",
            "行情为14:50盘中价格；场外止盈仅为估算预警，"
            "待基金公司公布官方净值后复核，请自行确认是否执行。",
        ]
    )
    snapshot = {
        "trading_date": today.isoformat(),
        "checked_at": checked_at.isoformat(),
        "items": items,
        "errors": errors,
    }
    return subject, "\n".join(lines), snapshot


class DailyCheckService:
    def __init__(
        self,
        database: Database,
        market: MarketProvider,
        calendar: TradingCalendar,
        notifiers: list[NotificationProvider],
        fund_nav_provider: FundNavProvider | None = None,
    ) -> None:
        self.database = database
        self.market = market
        self.calendar = calendar
        self.notifiers = notifiers
        self.fund_nav_provider = fund_nav_provider

    def run(
        self,
        now: datetime | None = None,
        *,
        scheduled: bool = True,
        check_run_id: UUID | None = None,
    ) -> CheckResult:
        now = now or datetime.now().astimezone()
        today = now.date()
        if not self.calendar.is_trading_day(today):
            return CheckResult(0, (), ())
        plans = self.database.list_plans()
        quotes = self.market.latest_quotes([plan.signal_code for plan in plans])
        created: list[UUID] = []
        errors: list[str] = []
        summary_items: list[dict[str, Any]] = []
        desktop_notifiers = [
            notifier for notifier in self.notifiers if notifier.name != "email"
        ]
        email_notifier = next(
            (notifier for notifier in self.notifiers if notifier.name == "email"), None
        )

        for plan in plans:
            plan_event_ids: list[UUID] = []
            if self.fund_nav_provider is not None:
                try:
                    fund_nav = self.fund_nav_provider.latest_nav(plan.purchase_code)
                    self.database.record_official_nav(
                        plan.id,
                        nav_date=fund_nav.nav_date,
                        official_nav=fund_nav.unit_nav,
                        daily_change=fund_nav.daily_change,
                        source=fund_nav.source,
                        fetched_at=fund_nav.fetched_at,
                    )
                except Exception as error:
                    errors.append(f"{plan.purchase_code} 场外净值同步失败: {error}")
            try:
                quote = quotes.get(plan.signal_code)
                if quote is None:
                    raise RuntimeError("latest quote is unavailable")
                if abs((now - quote.quoted_at).total_seconds()) > 600:
                    raise RuntimeError("latest quote is older than 10 minutes")
                self.database.save_market_snapshot(
                    plan_id=plan.id,
                    quote_price=quote.price,
                    quote_time=quote.quoted_at,
                    daily_change=quote.daily_change,
                    highest_close=None,
                    drawdown=None,
                )
                history = self.market.completed_history(plan.signal_code, 20)
                high = rolling_high(history)
                signal_previous_close = history[-1].close
                short_drawdown = drawdown_from_peak(quote.price, high.close)
                strategy = self.database.get_active_strategy(plan.id)
                active_drawdown = self.database.get_active_cycle(plan.id)
                take_profit_cycle = self.database.current_take_profit_cycle(plan.id)
                position = self.database.current_position(plan.id)
                latest_nav = self.database.latest_official_nav(plan.id)
                take_profit_levels = self.database.take_profit_levels(plan.id)
                executed_take_profit = (
                    self.database.executed_take_profit_level_ids(
                        UUID(take_profit_cycle["id"])
                    )
                    if take_profit_cycle else set()
                )
                position_ready = bool(
                    take_profit_cycle
                    and position["average_cost"] is not None
                    and Decimal(position["actual_units"]) > 0
                    and position["review_status"] == "confirmed"
                    and latest_nav is not None
                )
                regular = (
                    plan.recurring_amount
                    if plan.recurring_enabled and today.weekday() == plan.invest_weekday
                    else Decimal("0.00")
                )
                stage = StrategyStage.NORMAL
                decision_peak_type = DecisionPeakType.ROLLING_20D_HIGH
                decision_peak_price = high.close
                decision_peak_date = high.trading_date
                decision_drawdown = short_drawdown
                take_profit_peak = None
                take_profit_drawdown = None
                event_payload: dict[str, Any] | None = None

                if take_profit_cycle and take_profit_cycle["state"] in {
                    "active", "recovering"
                }:
                    stage = (
                        StrategyStage.TAKE_PROFIT
                        if take_profit_cycle["state"] == "active"
                        else StrategyStage.RECOVERY
                    )
                    if scheduled:
                        take_profit_peak = self.database.update_take_profit_peak(
                            UUID(take_profit_cycle["id"]), quote.price, today
                        )
                        take_profit_cycle = self.database.current_take_profit_cycle(
                            plan.id
                        )
                    else:
                        take_profit_peak = (
                            Decimal(take_profit_cycle["take_profit_peak"])
                            if take_profit_cycle["take_profit_peak"] is not None
                            else quote.price
                        )
                    decision_peak_type = DecisionPeakType.TAKE_PROFIT_PEAK
                    decision_peak_price = take_profit_peak
                    decision_peak_date = (
                        date.fromisoformat(take_profit_cycle["take_profit_peak_date"])
                        if take_profit_cycle["take_profit_peak_date"]
                        else today
                    )
                    decision_drawdown = drawdown_from_peak(quote.price, take_profit_peak)
                    take_profit_drawdown = decision_drawdown

                if (
                    scheduled
                    and position_ready
                    and take_profit_cycle["state"] != "recovering"
                ):
                    cost_basis = Decimal(position["average_cost"])
                    reference_nav = Decimal(latest_nav["official_nav"])
                    reference_nav_date = date.fromisoformat(latest_nav["nav_date"])
                    official_eligible = [
                        level
                        for level in take_profit_levels
                        if level.id not in executed_take_profit
                        and reference_nav_date < today
                        and reference_nav >= take_profit_target(
                            cost_basis, level.profit_rate
                        )
                        and not self.database.take_profit_event_exists_for_nav_date(
                            plan.id, reference_nav_date, level.id
                        )
                    ]
                    if official_eligible:
                        level = max(
                            official_eligible, key=lambda item: item.profit_rate
                        )
                        target = take_profit_target(cost_basis, level.profit_rate)
                        units = planned_sell_units(
                            Decimal(position["actual_units"]), level
                        )
                        event_payload = {
                            "event_type": "take_profit",
                            "level": None,
                            "threshold": level.profit_rate,
                            "regular": regular,
                            "extra": Decimal("0.00"),
                            "key": event_key(
                                plan.id,
                                reference_nav_date,
                                UUID(take_profit_cycle["id"]),
                                f"take-profit-official:{level.id}",
                            ),
                            "take_profit_cycle_id": UUID(take_profit_cycle["id"]),
                            "take_profit_level_id": level.id,
                            "take_profit_cost_basis": cost_basis,
                            "take_profit_target_price": target,
                            "sell_ratio_snapshot": level.sell_ratio,
                            "planned_sell_units": units,
                            "valuation_status": ValuationStatus.NAV_CONFIRMED,
                            "valuation_type": ValuationType(
                                take_profit_cycle["valuation_type"]
                            ),
                            "estimated_nav": reference_nav,
                            "reference_nav": reference_nav,
                            "reference_nav_date": reference_nav_date,
                            "official_nav": reference_nav,
                            "official_nav_date": reference_nav_date,
                            "recurring_amount_before": plan.recurring_amount,
                            "recurring_amount_after": level.next_recurring_amount,
                        }
                    else:
                        estimated_nav, signal_return = estimate_linked_fund_nav(
                            reference_nav, signal_previous_close, quote.price
                        )
                        eligible = [
                            level
                            for level in take_profit_levels
                            if level.id not in executed_take_profit
                            and estimated_nav >= take_profit_target(
                                cost_basis, level.profit_rate
                            )
                        ]
                        if eligible:
                            level = max(eligible, key=lambda item: item.profit_rate)
                            target = take_profit_target(cost_basis, level.profit_rate)
                            units = planned_sell_units(
                                Decimal(position["actual_units"]), level
                            )
                            event_payload = {
                                "event_type": "take_profit",
                                "level": None,
                                "threshold": level.profit_rate,
                                "regular": regular,
                                "extra": Decimal("0.00"),
                                "key": event_key(
                                    plan.id,
                                    today,
                                    UUID(take_profit_cycle["id"]),
                                    f"take-profit:{level.id}",
                                ),
                                "take_profit_cycle_id": UUID(
                                    take_profit_cycle["id"]
                                ),
                                "take_profit_level_id": level.id,
                                "take_profit_cost_basis": cost_basis,
                                "take_profit_target_price": target,
                                "sell_ratio_snapshot": level.sell_ratio,
                                "planned_sell_units": units,
                                "valuation_status": (
                                    ValuationStatus.ESTIMATED_WARNING
                                ),
                                "valuation_type": ValuationType(
                                    take_profit_cycle["valuation_type"]
                                ),
                                "estimated_nav": estimated_nav,
                                "reference_nav": reference_nav,
                                "reference_nav_date": reference_nav_date,
                                "signal_previous_close": signal_previous_close,
                                "signal_intraday_return": signal_return,
                                "recurring_amount_before": plan.recurring_amount,
                                "recurring_amount_after": (
                                    level.next_recurring_amount
                                ),
                            }

                if (
                    scheduled
                    and event_payload is None
                    and take_profit_cycle
                    and take_profit_cycle["state"] in {"active", "recovering"}
                ):
                    recovery_levels = self.database.recovery_levels(plan.id)
                    executed_recovery = self.database.executed_recovery_level_ids(
                        UUID(take_profit_cycle["id"])
                    )
                    eligible = [
                        level
                        for level in recovery_levels
                        if level.id not in executed_recovery
                        and level.recurring_amount is not None
                        and level.recurring_amount > plan.recurring_amount
                        and decision_drawdown >= level.drawdown
                    ]
                    if eligible:
                        recovery = max(eligible, key=lambda item: item.drawdown)
                        event_payload = {
                            "event_type": "recovery",
                            "level": None,
                            "threshold": recovery.drawdown,
                            "regular": (
                                recovery.recurring_amount
                                if plan.recurring_enabled
                                and today.weekday() == plan.invest_weekday
                                else Decimal("0.00")
                            ),
                            "extra": Decimal("0.00"),
                            "key": event_key(
                                plan.id,
                                today,
                                UUID(take_profit_cycle["id"]),
                                f"recovery:{recovery.id}",
                            ),
                            "take_profit_cycle_id": UUID(take_profit_cycle["id"]),
                            "take_profit_level_id": None,
                            "recurring_amount_before": plan.recurring_amount,
                            "recurring_amount_after": recovery.recurring_amount,
                            "recovery_level_id": recovery.id,
                        }

                cycle_id = UUID(active_drawdown["id"]) if active_drawdown else None
                cycle_peak = None
                cycle_drawdown = None
                if (
                    event_payload is None
                    and not (
                        take_profit_cycle
                        and take_profit_cycle["state"] in {"active", "recovering"}
                    )
                ):
                    level = None
                    if active_drawdown:
                        cycle_peak = self.database.resolve_cycle_peak(plan.id, high.close)
                        cycle_drawdown = drawdown_from_peak(quote.price, cycle_peak)
                        decision_peak_type = DecisionPeakType.CYCLE_PEAK
                        decision_peak_price = cycle_peak
                        decision_peak_date = date.fromisoformat(
                            active_drawdown["started_at"][:10]
                        )
                        decision_drawdown = cycle_drawdown
                        if scheduled:
                            ended = self.database.update_recovery(
                                plan.id, cycle_drawdown <= Decimal("2"), today
                            )
                            if ended:
                                cycle_id = None
                                cycle_peak = None
                                cycle_drawdown = None
                        if scheduled and cycle_id is not None:
                            level = choose_level(cycle_drawdown, strategy)
                    elif scheduled:
                        level = choose_level(short_drawdown, strategy)
                        if plan.drawdown_enabled and level is not None:
                            cycle_peak = high.close
                            cycle_drawdown = short_drawdown
                            cycle_id = self.database.get_or_create_active_cycle(
                                plan.id, strategy.id, cycle_peak
                            )
                    if (
                        scheduled
                        and level is not None
                        and plan.drawdown_enabled
                        and cycle_id is not None
                        and not bool(
                            active_drawdown["paused_for_review"]
                            if active_drawdown else False
                        )
                    ):
                        extra = planned_amount(plan.base_amount, level)
                        if not _continuous_budget_allows(
                            self.database, cycle_id, level, extra
                        ):
                            self.database.set_cycle_paused(cycle_id, True)
                            level = None
                        else:
                            highest_notified = self.database.highest_notified_threshold(
                                cycle_id
                            )
                            shallow_replay = (
                                highest_notified is not None
                                and level.threshold < highest_notified
                                and level.execution_mode
                                is not ExecutionMode.WEEKLY_WHILE_DEEP
                            )
                            due = not shallow_replay and _phase_is_due(
                                self.database, cycle_id, level, today
                            )
                            state = EventState.CREATED
                            event_type = "combined" if regular else "drawdown"
                            if not due:
                                level = None
                            elif self.database.notified_this_week(
                                plan.id, iso_week_key(today)
                            ):
                                state = EventState.SUPPRESSED
                                event_type = "level_suppressed"
                                extra = Decimal("0.00")
                            if level is not None:
                                event_payload = {
                                    "event_type": event_type,
                                    "level": level,
                                    "threshold": level.threshold,
                                    "regular": regular,
                                    "extra": extra,
                                    "state": state,
                                    "key": event_key(
                                        plan.id,
                                        today,
                                        cycle_id,
                                        f"drawdown:{level.id}",
                                    ),
                                }

                if (
                    scheduled
                    and event_payload is None
                    and regular
                    and not self.database.recurring_event_exists(plan.id, today)
                ):
                    event_payload = {
                        "event_type": "recurring",
                        "level": None,
                        "threshold": None,
                        "regular": regular,
                        "extra": Decimal("0.00"),
                        "key": event_key(plan.id, today, None, "recurring"),
                    }

                if scheduled and event_payload is not None:
                    level = event_payload.get("level")
                    event_id, was_created = self.database.create_event(
                        plan_id=plan.id,
                        strategy_id=strategy.id,
                        level_id=level.id if level else None,
                        event_type=event_payload["event_type"],
                        trading_date=today.isoformat(),
                        week_key=iso_week_key(today),
                        quote_price=quote.price,
                        quote_time=quote.quoted_at,
                        daily_change=quote.daily_change,
                        highest_close=high.close,
                        drawdown=decision_drawdown,
                        rolling_20d_high_date=high.trading_date,
                        decision_peak_type=decision_peak_type,
                        decision_peak_price=decision_peak_price,
                        decision_peak_date=decision_peak_date,
                        threshold_snapshot=event_payload["threshold"],
                        multiplier_snapshot=level.multiplier if level else None,
                        execution_mode_snapshot=(
                            level.execution_mode.value if level else None
                        ),
                        regular_amount=event_payload["regular"],
                        extra_amount=event_payload["extra"],
                        idempotency_key=event_payload["key"],
                        state=event_payload.get("state", EventState.CREATED),
                        cycle_id=cycle_id,
                        take_profit_cycle_id=event_payload.get(
                            "take_profit_cycle_id"
                        ),
                        take_profit_level_id=event_payload.get(
                            "take_profit_level_id"
                        ),
                        take_profit_cost_basis=event_payload.get(
                            "take_profit_cost_basis"
                        ),
                        take_profit_target_price=event_payload.get(
                            "take_profit_target_price"
                        ),
                        sell_ratio_snapshot=event_payload.get(
                            "sell_ratio_snapshot"
                        ),
                        planned_sell_units=event_payload.get("planned_sell_units"),
                        recurring_amount_before=event_payload.get(
                            "recurring_amount_before"
                        ),
                        recurring_amount_after=event_payload.get(
                            "recurring_amount_after"
                        ),
                        valuation_status=event_payload.get("valuation_status"),
                        valuation_type=event_payload.get("valuation_type"),
                        estimated_nav=event_payload.get("estimated_nav"),
                        reference_nav=event_payload.get("reference_nav"),
                        reference_nav_date=event_payload.get("reference_nav_date"),
                        signal_previous_close=event_payload.get(
                            "signal_previous_close"
                        ),
                        signal_intraday_return=event_payload.get(
                            "signal_intraday_return"
                        ),
                        official_nav=event_payload.get("official_nav"),
                        official_nav_date=event_payload.get("official_nav_date"),
                    )
                    if was_created:
                        created.append(event_id)
                    plan_event_ids.append(event_id)
                    row = self.database.event_row(event_id)
                    if (
                        was_created
                        and event_payload["event_type"] == "recovery"
                    ):
                        self.database.apply_recovery(
                            cycle_id=event_payload["take_profit_cycle_id"],
                            level_id=event_payload["recovery_level_id"],
                            event_id=event_id,
                            recurring_amount=event_payload["recurring_amount_after"],
                        )
                        row = self.database.event_row(event_id)
                    for notifier in desktop_notifiers:
                        if (
                            self.database.delivery_states(event_id).get(notifier.name)
                            is DeliveryState.SENT
                        ):
                            continue
                        title, body = _render_event(plan, row)
                        try:
                            notifier.send(title, body)
                            self.database.record_delivery(
                                event_id=event_id,
                                channel=notifier.name,
                                state=DeliveryState.SENT,
                            )
                        except Exception as error:
                            self.database.record_delivery(
                                event_id=event_id,
                                channel=notifier.name,
                                state=DeliveryState.FAILED,
                                error_summary=str(error)[:500],
                            )
                    if (
                        level is not None
                        and cycle_id is not None
                        and _continuous_budget_exhausted(
                            self.database, cycle_id, level
                        )
                    ):
                        self.database.set_cycle_paused(cycle_id, True)
                elif scheduled and event_payload is None:
                    for existing_row in self.database.events_for_plan_date(
                        plan.id, today
                    ):
                        existing_id = UUID(existing_row["id"])
                        deliveries = self.database.delivery_states(existing_id)
                        for notifier in desktop_notifiers:
                            if deliveries.get(notifier.name) is not DeliveryState.FAILED:
                                continue
                            title, body = _render_event(plan, existing_row)
                            try:
                                notifier.send(title, body)
                                self.database.record_delivery(
                                    event_id=existing_id,
                                    channel=notifier.name,
                                    state=DeliveryState.SENT,
                                )
                            except Exception as error:
                                self.database.record_delivery(
                                    event_id=existing_id,
                                    channel=notifier.name,
                                    state=DeliveryState.FAILED,
                                    error_summary=str(error)[:500],
                                )

                next_target = None
                if position_ready:
                    remaining_levels = [
                        level
                        for level in take_profit_levels
                        if level.id not in executed_take_profit
                    ]
                    if remaining_levels:
                        next_target = str(
                            take_profit_target(
                                Decimal(position["average_cost"]),
                                min(
                                    remaining_levels,
                                    key=lambda item: item.profit_rate,
                                ).profit_rate,
                            )
                        )
                self.database.save_market_snapshot(
                    plan_id=plan.id,
                    quote_price=quote.price,
                    quote_time=quote.quoted_at,
                    daily_change=quote.daily_change,
                    highest_close=high.close,
                    highest_close_date=high.trading_date,
                    drawdown=short_drawdown,
                    cycle_peak=cycle_peak,
                    cycle_drawdown=cycle_drawdown,
                    strategy_stage=stage.value,
                    decision_peak_type=decision_peak_type.value,
                    decision_peak_price=decision_peak_price,
                    decision_peak_date=decision_peak_date,
                    take_profit_peak=take_profit_peak,
                    take_profit_drawdown=take_profit_drawdown,
                )
                summary_items.append(
                    {
                        "plan_id": str(plan.id),
                        "name": plan.name,
                        "signal_code": plan.signal_code,
                        "quote_price": str(quote.price),
                        "quote_time": quote.quoted_at.isoformat(),
                        "rolling_high": str(high.close),
                        "rolling_high_date": high.trading_date.isoformat(),
                        "short_drawdown": str(short_drawdown),
                        "decision_peak_type": decision_peak_type.value,
                        "decision_peak_price": str(decision_peak_price),
                        "decision_drawdown": str(decision_drawdown),
                        "next_take_profit_target": next_target,
                        "events": [
                            dict(self.database.event_row(event_id))
                            for event_id in plan_event_ids
                        ],
                    }
                )
            except Exception as error:
                errors.append(f"{plan.signal_code}: {error}")

        summary_id = None
        if scheduled and email_notifier is not None:
            recipient = getattr(email_notifier, "recipient", email_notifier.name)
            subject, body, snapshot = _summary_text(
                today, now, summary_items, errors
            )
            summary_id, was_created = self.database.create_daily_summary(
                check_run_id=check_run_id,
                trading_date=today,
                recipient=recipient,
                subject=subject,
                body=body,
                snapshot=snapshot,
            )
            summary_row = self.database.daily_summary_row(summary_id)
            if was_created or summary_row["state"] == "daily_summary_failed":
                send_subject = summary_row["subject"]
                send_body = summary_row["body"]
                retry_snapshot = snapshot
                if not was_created:
                    import json

                    retry_snapshot = json.loads(summary_row["snapshot_json"])
                summary_event_ids = tuple(
                    UUID(event["id"])
                    for item in retry_snapshot["items"]
                    for event in item["events"]
                )
                try:
                    email_notifier.send(send_subject, send_body)
                    self.database.mark_daily_summary(summary_id, sent=True)
                    self.database.mark_events_from_daily_summary(
                        summary_event_ids, sent=True
                    )
                    for event_id in summary_event_ids:
                        row = self.database.event_row(event_id)
                        if (
                            row["cycle_id"]
                            and row["level_id"]
                            and row["execution_mode_snapshot"]
                            == ExecutionMode.WEEKLY_WHILE_DEEP.value
                        ):
                            level = next(
                                (
                                    candidate
                                    for candidate in self.database.get_active_strategy(
                                        UUID(row["plan_id"])
                                    ).levels
                                    if str(candidate.id) == row["level_id"]
                                ),
                                None,
                            )
                            if level and _continuous_budget_exhausted(
                                self.database, UUID(row["cycle_id"]), level
                            ):
                                self.database.set_cycle_paused(
                                    UUID(row["cycle_id"]), True
                                )
                except Exception as error:
                    self.database.mark_daily_summary(
                        summary_id, sent=False, error_summary=str(error)[:500]
                    )
                    self.database.mark_events_from_daily_summary(
                        summary_event_ids, sent=False
                    )
                    errors.append(f"daily email: {error}")
        return CheckResult(len(plans), tuple(created), tuple(errors), summary_id)
