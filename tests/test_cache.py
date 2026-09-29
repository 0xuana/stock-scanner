from __future__ import annotations

from datetime import date

import polars as pl

from stock_scanner.cache import ParquetCache
from stock_scanner.data_provider import DAILY_SCHEMA


class FakeProvider:
    def __init__(self) -> None:
        self.starts: list[date] = []

    def daily_bars(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        self.starts.append(start)
        return pl.DataFrame(
            {
                "symbol": [symbol],
                "date": [start],
                "open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5],
                "volume": [10.0], "amount": [15.0],
            },
            schema=DAILY_SCHEMA,
        )


def test_cache_updates_from_day_after_latest_bar(tmp_path) -> None:
    cache = ParquetCache(tmp_path)
    provider = FakeProvider()
    cache.update(provider, "000001", initial_start=date(2024, 1, 1), end=date(2024, 1, 2))
    result = cache.update(provider, "000001", initial_start=date(2024, 1, 1), end=date(2024, 1, 2))
    assert provider.starts == [date(2024, 1, 1), date(2024, 1, 2)]
    assert result.height == 2


def test_cache_backfills_when_requested_start_moves_earlier(tmp_path) -> None:
    cache = ParquetCache(tmp_path)
    provider = FakeProvider()
    cache.update(provider, "000001", initial_start=date(2024, 1, 2), end=date(2024, 1, 2))
    result = cache.update(provider, "000001", initial_start=date(2024, 1, 1), end=date(2024, 1, 2))
    assert provider.starts == [date(2024, 1, 2), date(2024, 1, 1)]
    assert result.get_column("date").to_list() == [date(2024, 1, 1), date(2024, 1, 2)]
