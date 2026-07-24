from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from .db import Database
from .domain import DeliveryState, EventState, ExecutionMode, Plan, Quote, Strategy, StrategyLevel
from .providers.market import MarketProvider, TradingCalendar
from .providers.notify import NotificationProvider
from .strategy import evaluate


@dataclass(frozen=True, slots=True)
class CheckResult:
    checked_plans: int
    created_events: tuple[UUID, ...]
    errors: tuple[str, ...]


def iso_week_key(value: date) -> str:
    year, week, _ = value.isocalendar()
    return f"{year}-W{week:02d}"


def event_key(plan: Plan, value: date, cycle_id: UUID | None, level: StrategyLevel | None) -> str:
    return ":".join(
        [
            str(plan.id), value.isoformat(),
            str(cycle_id) if cycle_id else "no-cycle",
            str(level.id) if level else "no-level",
        ]
    )


def _phase_is_due(database: Database, cycle_id: UUID, level: StrategyLevel, today: date) -> bool:
    notified = database.notified_threshold_events(cycle_id, level.threshold)
    if level.execution_mode is ExecutionMode.ONCE:
        return not notified
    if level.execution_mode is ExecutionMode.WEEKLY_WHILE_DEEP:
        return True
    if len(notified) >= level.phases:
        return False
    if not notified:
        return True
    last_date = date.fromisoformat(notified[-1]["trading_date"])
    return (today - last_date).days >= level.interval_weeks * 7


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
    if (
        level.cycle_multiplier_cap is not None
        and multiplier + level.multiplier > level.cycle_multiplier_cap
    ):
        return False
    return True


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


def _render(
    plan: Plan,
    quote: Quote,
    drawdown: Decimal,
    highest: Decimal,
    level: StrategyLevel | None,
    regular: Decimal,
    extra: Decimal,
    *,
    suppressed: bool = False,
) -> tuple[str, str]:
    if level is None:
        title = f"【ETF定投提醒】{plan.name}"
        body = (
            f"今天计划定投 {regular} 元，未触发额外投入档位。\n"
            f"14:50 附近回撤 {drawdown:.2f}%，行情时间 {quote.quoted_at:%H:%M:%S}。"
        )
        return title, body
    title = f"【ETF计划提醒】{plan.name}触发{level.threshold}%档"
    if suppressed:
        return (
            f"【ETF风险升级】{plan.name}触发{level.threshold}%档",
            (
                f"购买基金：{plan.purchase_name or plan.name}（{plan.purchase_code}）\n"
                f"观察 ETF：{plan.signal_name or plan.signal_code}（{plan.signal_code}）\n"
                f"最新价：{quote.price}，行情时间：{quote.quoted_at:%Y-%m-%d %H:%M:%S}\n"
                f"本轮固定回撤高点：{highest}，本轮回撤：{drawdown:.2f}%\n\n"
                "本周已经发送过一次补仓计划，本次仅提示风险档位加深，"
                "不重复增加补仓金额。\n\n"
                "本消息按你预设的规则计算，不代表最终收盘价格，也不构成投资建议。"
            ),
        )
    body = (
        f"购买基金：{plan.purchase_name or plan.name}（{plan.purchase_code}）\n"
        f"观察 ETF：{plan.signal_name or plan.signal_code}（{plan.signal_code}）\n"
        f"最新价：{quote.price}，行情时间：{quote.quoted_at:%Y-%m-%d %H:%M:%S}\n"
        f"本轮固定回撤高点：{highest}，本轮回撤：{drawdown:.2f}%\n"
        f"固定定投：{regular} 元\n"
        f"额外投入：{plan.base_amount} × {level.multiplier} = {extra} 元\n"
        f"今日计划合计：{regular + extra} 元\n\n"
        "本消息按你预设的规则计算，不代表最终收盘价格，也不构成投资建议。"
    )
    return title, body


class DailyCheckService:
    def __init__(
        self,
        database: Database,
        market: MarketProvider,
        calendar: TradingCalendar,
        notifiers: list[NotificationProvider],
    ) -> None:
        self.database = database
        self.market = market
        self.calendar = calendar
        self.notifiers = notifiers

    def run(self, now: datetime | None = None) -> CheckResult:
        now = now or datetime.now().astimezone()
        today = now.date()
        if not self.calendar.is_trading_day(today):
            return CheckResult(0, (), ())
        plans = [plan for plan in self.database.list_plans() if plan.enabled]
        quotes = self.market.latest_quotes([plan.signal_code for plan in plans])
        created: list[UUID] = []
        errors: list[str] = []
        for plan in plans:
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
                strategy = self.database.get_active_strategy(plan.id)
                closes = self.market.completed_closes(plan.signal_code, 20)
                short_decision = evaluate(
                    current_price=quote.price,
                    completed_closes=closes,
                    base_amount=plan.base_amount,
                    strategy=strategy,
                )
                active_cycle = self.database.get_active_cycle(plan.id)
                cycle_id = UUID(active_cycle["id"]) if active_cycle else None
                cycle_peak = None
                cycle_drawdown = None
                paused_for_review = bool(active_cycle["paused_for_review"]) if active_cycle else False
                if active_cycle:
                    cycle_peak = self.database.resolve_cycle_peak(
                        plan.id, short_decision.highest_close
                    )
                    decision = evaluate(
                        current_price=quote.price,
                        completed_closes=[cycle_peak],
                        base_amount=plan.base_amount,
                        strategy=strategy,
                    )
                    cycle_drawdown = decision.drawdown
                else:
                    decision = short_decision
                    if plan.drawdown_enabled and decision.level is not None:
                        cycle_peak = short_decision.highest_close
                        cycle_drawdown = short_decision.drawdown
                        cycle_id = self.database.get_or_create_active_cycle(
                            plan.id, strategy.id, cycle_peak
                        )
                if active_cycle and cycle_drawdown is not None:
                    ended = self.database.update_recovery(
                        plan.id, cycle_drawdown <= Decimal("2"), today
                    )
                    if ended:
                        cycle_id = None
                        cycle_peak = None
                        cycle_drawdown = None
                self.database.save_market_snapshot(
                    plan_id=plan.id,
                    quote_price=quote.price,
                    quote_time=quote.quoted_at,
                    daily_change=quote.daily_change,
                    highest_close=short_decision.highest_close,
                    drawdown=short_decision.drawdown,
                    cycle_peak=cycle_peak,
                    cycle_drawdown=cycle_drawdown,
                )
                regular = plan.base_amount if plan.recurring_enabled and today.weekday() == plan.invest_weekday else Decimal("0.00")
                level = (
                    decision.level
                    if plan.drawdown_enabled and not paused_for_review
                    else None
                )
                extra = Decimal("0.00")
                state = EventState.CREATED
                event_type = "recurring"
                if level is not None:
                    if cycle_id is None:
                        raise RuntimeError("drawdown cycle was not initialized")
                    if not _continuous_budget_allows(
                        self.database, cycle_id, level, decision.extra_amount
                    ):
                        self.database.set_cycle_paused(cycle_id, True)
                        level = None
                    else:
                        week_key = iso_week_key(today)
                        highest_notified = self.database.highest_notified_threshold(cycle_id)
                        shallow_replay = (
                            highest_notified is not None
                            and level.threshold < highest_notified
                            and level.execution_mode is not ExecutionMode.WEEKLY_WHILE_DEEP
                        )
                        due = not shallow_replay and _phase_is_due(
                            self.database, cycle_id, level, today
                        )
                        if self.database.notified_this_week(plan.id, week_key):
                            state = EventState.SUPPRESSED
                            event_type = "level_suppressed"
                        elif due:
                            extra = decision.extra_amount
                            event_type = "combined" if regular else "drawdown"
                        else:
                            level = None
                if regular == 0 and extra == 0 and state is not EventState.SUPPRESSED:
                    continue
                week_key = iso_week_key(today)
                event_id, was_created = self.database.create_event(
                    plan_id=plan.id,
                    strategy_id=strategy.id,
                    level_id=level.id if level else None,
                    event_type=event_type,
                    trading_date=today.isoformat(),
                    week_key=week_key,
                    quote_price=quote.price,
                    quote_time=quote.quoted_at,
                    daily_change=quote.daily_change,
                    highest_close=decision.highest_close,
                    drawdown=decision.drawdown,
                    threshold_snapshot=level.threshold if level else None,
                    multiplier_snapshot=level.multiplier if level else None,
                    execution_mode_snapshot=level.execution_mode.value if level else None,
                    regular_amount=regular,
                    extra_amount=extra,
                    idempotency_key=event_key(plan, today, cycle_id, level),
                    state=state,
                    cycle_id=cycle_id,
                )
                if was_created:
                    created.append(event_id)
                else:
                    existing = self.database.event_row(event_id)
                    state = EventState(existing["state"])
                    regular = Decimal(existing["regular_amount"])
                    extra = Decimal(existing["extra_amount"])
                deliveries = self.database.delivery_states(event_id)
                pending_notifiers = [
                    notifier for notifier in self.notifiers
                    if deliveries.get(notifier.name) is not DeliveryState.SENT
                ]
                if not pending_notifiers:
                    continue
                title, body = _render(
                    plan,
                    quote,
                    decision.drawdown,
                    decision.highest_close,
                    level,
                    regular,
                    extra,
                    suppressed=state is EventState.SUPPRESSED,
                )
                sent = False
                for notifier in pending_notifiers:
                    try:
                        notifier.send(title, body)
                        self.database.record_delivery(
                            event_id=event_id, channel=notifier.name, state=DeliveryState.SENT
                        )
                        sent = True
                    except Exception as error:
                        self.database.record_delivery(
                            event_id=event_id,
                            channel=notifier.name,
                            state=DeliveryState.FAILED,
                            error_summary=str(error)[:500],
                        )
                if not sent:
                    self.database.mark_event_delivery_failed(event_id)
                elif level is not None and cycle_id is not None and _continuous_budget_exhausted(
                    self.database, cycle_id, level
                ):
                    self.database.set_cycle_paused(cycle_id, True)
            except Exception as error:
                errors.append(f"{plan.signal_code}: {error}")
        return CheckResult(len(plans), tuple(created), tuple(errors))
