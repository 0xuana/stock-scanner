"""Per-symbol Parquet cache and incremental update policy."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl

from .data_provider import DAILY_SCHEMA, AkShareProvider, TencentProvider


class ParquetCache:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, symbol: str) -> Path:
        if not (symbol.isascii() and symbol.isdigit()):
            raise ValueError(f"invalid stock symbol: {symbol!r}")
        return self.root / f"{symbol}.parquet"

    def read(self, symbol: str) -> pl.DataFrame:
        path = self.path_for(symbol)
        return (
            pl.read_parquet(path).sort("date")
            if path.exists()
            else pl.DataFrame(schema=DAILY_SCHEMA)
        )

    def write(self, symbol: str, frame: pl.DataFrame) -> None:
        path = self.path_for(symbol)
        temporary = path.with_suffix(".parquet.tmp")
        frame.sort("date").write_parquet(temporary, compression="zstd")
        temporary.replace(path)

    def update(
        self,
        provider: AkShareProvider | TencentProvider,
        symbol: str,
        *,
        initial_start: date,
        end: date,
    ) -> pl.DataFrame:
        cached = self.read(symbol)
        additions: list[pl.DataFrame] = []
        if cached.is_empty():
            additions.append(provider.daily_bars(symbol, initial_start, end))
        else:
            first_date = cached.get_column("date").min()
            last_date = cached.get_column("date").max()
            if initial_start < first_date:
                additions.append(
                    provider.daily_bars(symbol, initial_start, first_date - timedelta(days=1))
                )
            if last_date < end:
                additions.append(
                    provider.daily_bars(symbol, last_date + timedelta(days=1), end)
                )

        fresh_frames = [frame for frame in additions if not frame.is_empty()]
        if not fresh_frames:
            return cached
        combined = (
            pl.concat([cached, *fresh_frames], how="vertical_relaxed")
            .unique(subset=["symbol", "date"], keep="last")
            .sort("date")
        )
        self.write(symbol, combined)
        return combined
