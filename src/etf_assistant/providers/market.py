from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from ..domain import Quote


class MarketProvider(Protocol):
    def latest_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...

    def completed_closes(self, symbol: str, count: int = 20) -> list[Decimal]: ...


class TradingCalendar(Protocol):
    def is_trading_day(self, value: date) -> bool: ...


class WeekdayCalendar:
    """Development fallback only; production should use an exchange calendar."""

    def is_trading_day(self, value: date) -> bool:
        return value.weekday() < 5


@dataclass(slots=True)
class StaticMarketProvider:
    quotes: dict[str, Quote]
    closes: dict[str, list[Decimal]]

    def latest_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes}

    def completed_closes(self, symbol: str, count: int = 20) -> list[Decimal]:
        return self.closes[symbol][-count:]


class AkshareMarketProvider:
    """AKShare adapter loaded lazily so the core package has no mandatory network dependency."""

    def __init__(self) -> None:
        try:
            import akshare as ak
        except ImportError as error:
            raise RuntimeError("Install the 'market' extra to use AKShare") from error
        self.ak = ak

    def latest_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        frame = self.ak.fund_etf_spot_em()
        wanted = set(symbols)
        result: dict[str, Quote] = {}
        now = datetime.now().astimezone()
        for row in frame.to_dict("records"):
            symbol = str(row.get("代码", ""))
            if symbol not in wanted:
                continue
            price = row.get("最新价")
            if price is None:
                continue
            change = row.get("涨跌幅")
            result[symbol] = Quote(
                symbol=symbol,
                price=Decimal(str(price)),
                quoted_at=now,
                daily_change=Decimal(str(change)) if change is not None else None,
            )
        return result

    def completed_closes(self, symbol: str, count: int = 20) -> list[Decimal]:
        frame = self.ak.fund_etf_hist_em(symbol=symbol, period="daily", adjust="qfq")
        if "收盘" not in frame.columns:
            raise RuntimeError("AKShare ETF history response is missing the close column")
        today = date.today().isoformat()
        if "日期" in frame.columns:
            frame = frame[frame["日期"].astype(str) < today]
        return [Decimal(str(value)) for value in frame["收盘"].tail(count).tolist()]


class AkshareTradingCalendar:
    def __init__(self) -> None:
        try:
            import akshare as ak
        except ImportError as error:
            raise RuntimeError("Install the 'market' extra to use AKShare") from error
        frame = ak.tool_trade_date_hist_sina()
        self._dates = {str(value)[:10] for value in frame["trade_date"].tolist()}

    def is_trading_day(self, value: date) -> bool:
        return value.isoformat() in self._dates

