"""Daily OHLCV aggregation, independent from indicators."""

from __future__ import annotations

import polars as pl

OHLCV_AGGREGATIONS = [
    pl.col("open").first().alias("open"),
    pl.col("high").max().alias("high"),
    pl.col("low").min().alias("low"),
    pl.col("close").last().alias("close"),
    pl.col("volume").sum().alias("volume"),
    pl.col("amount").sum().alias("amount"),
]


def _calendar_bars(daily: pl.DataFrame, period: str) -> pl.DataFrame:
    return (
        daily.sort("date")
        .with_columns(pl.col("date").dt.truncate(period).alias("period_start"))
        .group_by(["symbol", "period_start"], maintain_order=True)
        .agg(pl.col("date").max().alias("date"), *OHLCV_AGGREGATIONS)
        .drop("period_start")
        .sort("date")
    )


def to_weekly(daily: pl.DataFrame) -> pl.DataFrame:
    return _calendar_bars(daily, "1w")


def to_monthly(daily: pl.DataFrame) -> pl.DataFrame:
    return _calendar_bars(daily, "1mo")


def to_n_trading_days(daily: pl.DataFrame, days: int = 45) -> pl.DataFrame:
    """Aggregate consecutive fixed-size trading-day blocks, anchored at first bar."""
    if days <= 0:
        raise ValueError("days must be positive")
    return (
        daily.sort("date")
        .with_row_index("row_number")
        .with_columns((pl.col("row_number") // days).alias("period_number"))
        .group_by(["symbol", "period_number"], maintain_order=True)
        .agg(pl.col("date").max().alias("date"), *OHLCV_AGGREGATIONS)
        .drop("period_number")
        .sort("date")
    )


def build_timeframes(daily: pl.DataFrame) -> dict[str, pl.DataFrame]:
    return {
        "daily": daily.sort("date"),
        "weekly": to_weekly(daily),
        "monthly": to_monthly(daily),
        "45d": to_n_trading_days(daily, 45),
    }
