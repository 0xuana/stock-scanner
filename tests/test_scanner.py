import polars as pl
import logging
from datetime import date, timedelta

from stock_scanner.scanner import (
    ScanConfig,
    build_stock_universe,
    latest_macd_state,
    run_scan,
)


def test_latest_state_uses_low_price_as_percentage_denominator() -> None:
    start = date(2025, 1, 1)
    dates = [
        start + timedelta(days=offset)
        for offset in range(620)
        if (start + timedelta(days=offset)).weekday() < 5
    ]
    lows = [8.0] * len(dates)
    lows[-30] = 6.0
    lows[-10] = 6.0
    daily = pl.DataFrame(
        {
            "symbol": ["000001"] * len(dates),
            "date": dates,
            "open": [9.0] * len(dates),
            "high": [10.0] * len(dates),
            "low": lows,
            "close": [9.0] * len(dates),
            "volume": [100.0] * len(dates),
            "amount": [1000.0] * len(dates),
        }
    )

    state = latest_macd_state("000001", "平安银行", daily)

    assert state["three_month_low"] == 6.0
    assert state["three_month_low_date"] == dates[-10]
    assert state["three_month_low_distance_pct"] == 50.0


class AlwaysFailProvider:
    def stock_list(self) -> pl.DataFrame:
        return pl.DataFrame({"symbol": ["000035"], "name": ["中国天楹"]})

    def daily_bars(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        raise RuntimeError("upstream timeout")


def test_single_stock_failure_is_concise_and_does_not_abort(tmp_path, caplog) -> None:
    caplog.set_level(logging.ERROR)
    matched, failures = run_scan(
        ScanConfig(
            data_dir=tmp_path / "data",
            results_dir=tmp_path / "results",
            workers=2,
        ),
        provider=AlwaysFailProvider(),
    )
    assert matched.is_empty()
    assert failures.get_column("symbol").to_list() == ["000035"]
    assert failures[0, "error"] == "upstream timeout"
    assert caplog.records[0].exc_info is None


def test_stock_universe_excludes_st_before_scan() -> None:
    stocks = pl.DataFrame(
        {
            "symbol": ["000001", "000002", "000003", "000004"],
            "name": ["平安银行", "ST示例", "*ST示例", "正常股"],
        }
    )
    selected = build_stock_universe(stocks)
    assert selected.get_column("symbol").to_list() == ["000001", "000004"]
    assert build_stock_universe(stocks, include_st=True).height == 4
