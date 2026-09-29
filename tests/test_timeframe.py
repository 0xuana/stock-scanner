from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from stock_scanner.timeframe import to_monthly, to_n_trading_days, to_weekly


def daily_frame(count: int = 50) -> pl.DataFrame:
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(count)]
    return pl.DataFrame(
        {
            "symbol": ["000001"] * count,
            "date": dates,
            "open": [float(i) for i in range(count)],
            "high": [float(i + 2) for i in range(count)],
            "low": [float(i - 1) for i in range(count)],
            "close": [float(i + 1) for i in range(count)],
            "volume": [10.0] * count,
            "amount": [100.0] * count,
        }
    )


def test_45d_uses_fixed_trading_day_blocks_and_ohlcv_rules() -> None:
    result = to_n_trading_days(daily_frame(), 45)
    assert result.height == 2
    assert result[0, "open"] == 0.0
    assert result[0, "high"] == 46.0
    assert result[0, "low"] == -1.0
    assert result[0, "close"] == 45.0
    assert result[0, "volume"] == 450.0
    assert result[0, "amount"] == 4500.0


def test_weekly_and_monthly_aggregate_calendar_periods() -> None:
    frame = daily_frame()
    assert to_weekly(frame).height == 8
    assert to_monthly(frame).height == 2

