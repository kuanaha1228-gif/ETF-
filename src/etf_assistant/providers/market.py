from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from ..domain import DatedClose, Quote


_SPLIT_FACTORS = tuple(Decimal(value) for value in ("0.2", "0.25", "0.333333", "0.5", "2", "3", "4", "5", "10"))


def _split_adjusted_candles(candles: list[dict[str, str]]) -> list[dict[str, str]]:
    adjusted = [
        {
            "date": candle["date"],
            "open": Decimal(candle["open"]),
            "high": Decimal(candle["high"]),
            "low": Decimal(candle["low"]),
            "close": Decimal(candle["close"]),
        }
        for candle in candles
    ]
    for index in range(1, len(adjusted)):
        current_open = adjusted[index]["open"]
        if not current_open:
            continue
        ratio = adjusted[index - 1]["close"] / current_open
        factor = min(_SPLIT_FACTORS, key=lambda candidate: abs(ratio - candidate))
        if abs(ratio - factor) / factor > Decimal("0.03"):
            continue
        for earlier in adjusted[:index]:
            for key in ("open", "high", "low", "close"):
                earlier[key] /= factor
    return [
        {key: str(value) for key, value in candle.items()}
        for candle in adjusted
    ]


def _history_is_safe_for_signals(candles: list[dict[str, str]]) -> bool:
    for previous, current in zip(candles, candles[1:]):
        previous_close = Decimal(previous["close"])
        current_open = Decimal(current["open"])
        if previous_close and abs(current_open - previous_close) / previous_close > Decimal("0.25"):
            return False
    return True


class MarketProvider(Protocol):
    def latest_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...

    def completed_closes(self, symbol: str, count: int = 20) -> list[Decimal]: ...

    def completed_history(self, symbol: str, count: int = 20) -> list[DatedClose]: ...


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
    dates: dict[str, list[date]] | None = None

    def latest_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes}

    def completed_closes(self, symbol: str, count: int = 20) -> list[Decimal]:
        return self.closes[symbol][-count:]

    def completed_history(self, symbol: str, count: int = 20) -> list[DatedClose]:
        closes = self.completed_closes(symbol, count)
        dates = (
            self.dates[symbol][-len(closes):]
            if self.dates and symbol in self.dates
            else [date(2000, 1, 1) + timedelta(days=index) for index in range(len(closes))]
        )
        return [
            DatedClose(trading_date=trading_date, close=close)
            for trading_date, close in zip(dates, closes)
        ]


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
        return [item.close for item in self.completed_history(symbol, count)]

    def completed_history(self, symbol: str, count: int = 20) -> list[DatedClose]:
        try:
            frame = self.ak.fund_etf_hist_em(symbol=symbol, period="daily", adjust="qfq")
            if "收盘" not in frame.columns or "日期" not in frame.columns:
                raise RuntimeError("AKShare ETF history response is missing date or close")
            today = date.today().isoformat()
            frame = frame[frame["日期"].astype(str) < today].tail(count)
            history = [
                DatedClose(
                    trading_date=date.fromisoformat(str(row["日期"])[:10]),
                    close=Decimal(str(row["收盘"])),
                )
                for row in frame.to_dict("records")
            ]
            if len(history) >= count:
                return history
        except Exception:
            pass
        today = date.today().isoformat()
        candles = [
            candle for candle in self.daily_candles(symbol, count + 10)
            if candle["date"] < today
        ]
        if len(candles) < count or not _history_is_safe_for_signals(candles):
            raise RuntimeError("历史行情无法安全复权，已跳过回撤提醒")
        return [
            DatedClose(
                trading_date=date.fromisoformat(candle["date"]),
                close=Decimal(candle["close"]),
            )
            for candle in candles[-count:]
        ]

    def daily_candles(self, symbol: str, count: int = 30) -> list[dict[str, str]]:
        exchange_symbol = ("sh" if symbol.startswith(("5", "6", "9")) else "sz") + symbol
        frame = self.ak.fund_etf_hist_sina(symbol=exchange_symbol)
        columns = {str(column).lower(): column for column in frame.columns}

        def column(*names: str):
            for name in names:
                if name.lower() in columns:
                    return columns[name.lower()]
            raise RuntimeError("K 线数据字段不完整")

        date_column = column("date", "日期")
        open_column = column("open", "开盘")
        high_column = column("high", "最高")
        low_column = column("low", "最低")
        close_column = column("close", "收盘")
        candles = [
            {
                "date": str(row[date_column])[:10],
                "open": str(row[open_column]),
                "high": str(row[high_column]),
                "low": str(row[low_column]),
                "close": str(row[close_column]),
            }
            for row in frame.tail(count).to_dict("records")
        ]
        return _split_adjusted_candles(candles)


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
