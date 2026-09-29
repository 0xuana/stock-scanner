from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from stock_scanner.indicators import macd


def test_macd_matches_definition_and_emits_states() -> None:
    closes = [10.0] * 25 + [11.0, 12.0, 13.0]
    frame = pl.DataFrame(
        {
            "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(len(closes))],
            "close": closes,
        }
    )
    result = macd(frame)

    assert result.height == len(closes)
    assert result[25, "golden_cross"] is True
    assert result[-1, "dif"] > result[-1, "dea"] > 0
    assert result[-1, "hist"] == result[-1, "dif"] - result[-1, "dea"]
    assert result[-1, "above_zero"] is True


def test_macd_detects_death_cross() -> None:
    closes = [10.0] * 25 + [13.0, 14.0, 15.0] + [9.0] * 8
    frame = pl.DataFrame({"date": list(range(len(closes))), "close": closes})
    result = macd(frame)
    assert result.get_column("death_cross").any()

