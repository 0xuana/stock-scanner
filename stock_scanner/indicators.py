"""Pure indicator calculations with no data-provider dependency."""

from __future__ import annotations

import polars as pl


def macd(
    frame: pl.DataFrame, fast: int = 10, slow: int = 20, signal: int = 8
) -> pl.DataFrame:
    if not 0 < fast < slow or signal <= 0:
        raise ValueError("MACD periods must satisfy 0 < fast < slow and signal > 0")
    result = frame.sort("date").with_columns(
        (
            pl.col("close").ewm_mean(span=fast, adjust=False)
            - pl.col("close").ewm_mean(span=slow, adjust=False)
        ).alias("dif")
    )
    result = result.with_columns(
        pl.col("dif").ewm_mean(span=signal, adjust=False).alias("dea")
    )
    result = result.with_columns((pl.col("dif") - pl.col("dea")).alias("hist"))
    return result.with_columns(
        (
            (pl.col("dif").shift(1) <= pl.col("dea").shift(1))
            & (pl.col("dif") > pl.col("dea"))
        )
        .fill_null(False)
        .alias("golden_cross"),
        (
            (pl.col("dif").shift(1) >= pl.col("dea").shift(1))
            & (pl.col("dif") < pl.col("dea"))
        )
        .fill_null(False)
        .alias("death_cross"),
        (pl.col("hist") > 0).alias("above_zero"),
        (pl.col("hist") < 0).alias("below_zero"),
        (pl.col("hist") > pl.col("hist").shift(1)).fill_null(False).alias("hist_rising"),
        (pl.col("hist") < pl.col("hist").shift(1)).fill_null(False).alias("hist_falling"),
    )
