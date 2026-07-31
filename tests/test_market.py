from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase

import pandas as pd

from etf_assistant.providers.market import (
    AkshareMarketProvider,
    _parse_eastmoney_nav_script,
)


class _FakeAkshare:
    def __init__(self) -> None:
        self.symbol = ""

    def fund_etf_hist_sina(self, *, symbol: str):
        self.symbol = symbol
        return pd.DataFrame(
            [
                {"date": "2026-07-18", "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1},
                {"date": "2026-07-19", "open": 1.1, "high": 1.3, "low": 1.0, "close": 1.2},
            ]
        )

    def fund_etf_hist_em(self, **_kwargs):
        raise ConnectionError("history source unavailable")


class MarketProviderTests(TestCase):
    def test_eastmoney_nav_script_returns_latest_official_nav(self) -> None:
        payload = """
        var Data_netWorthTrend = [
          {"x":1785254400000,"y":1.0143,"equityReturn":-2.21},
          {"x":1785340800000,"y":1.0253,"equityReturn":1.08}
        ];
        """
        fetched_at = datetime(2026, 7, 30, 9, tzinfo=timezone.utc)
        nav = _parse_eastmoney_nav_script("019666", payload, fetched_at)
        self.assertEqual(nav.symbol, "019666")
        self.assertEqual(nav.unit_nav, Decimal("1.0253"))
        self.assertEqual(nav.daily_change, Decimal("1.08"))
        self.assertEqual(nav.source, "eastmoney")

    def test_daily_candles_use_exchange_prefix_and_return_ohlc(self) -> None:
        provider = AkshareMarketProvider.__new__(AkshareMarketProvider)
        provider.ak = _FakeAkshare()
        candles = provider.daily_candles("516080", 30)
        self.assertEqual(provider.ak.symbol, "sh516080")
        self.assertEqual(candles[-1]["close"], "1.2")
        self.assertEqual(candles[-1]["date"], "2026-07-19")

    def test_split_is_back_adjusted_in_sina_fallback(self) -> None:
        provider = AkshareMarketProvider.__new__(AkshareMarketProvider)
        provider.ak = _FakeAkshare()
        provider.ak.fund_etf_hist_sina = lambda **_kwargs: pd.DataFrame(
            [
                {"date": "2026-07-08", "open": 1.8, "high": 2.0, "low": 1.7, "close": 1.946},
                {"date": "2026-07-09", "open": 0.973, "high": 1.0, "low": 0.9, "close": 0.95},
                {"date": "2026-07-10", "open": 0.95, "high": 0.98, "low": 0.92, "close": 0.96},
            ]
        )
        candles = provider.daily_candles("159516", 30)
        self.assertEqual(candles[0]["close"], "0.973")
        self.assertEqual(candles[0]["high"], "1.0")

    def test_completed_closes_fall_back_to_adjusted_sina_data(self) -> None:
        provider = AkshareMarketProvider.__new__(AkshareMarketProvider)
        provider.ak = _FakeAkshare()
        closes = provider.completed_closes("516080", 2)
        self.assertEqual([str(value) for value in closes], ["1.1", "1.2"])

    def test_unrecognized_price_discontinuity_is_not_used_for_signals(self) -> None:
        provider = AkshareMarketProvider.__new__(AkshareMarketProvider)
        provider.ak = _FakeAkshare()
        provider.ak.fund_etf_hist_sina = lambda **_kwargs: pd.DataFrame(
            [
                {"date": "2026-07-17", "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0},
                {"date": "2026-07-18", "open": 0.7, "high": 0.75, "low": 0.68, "close": 0.72},
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "已跳过回撤提醒"):
            provider.completed_closes("516080", 2)
