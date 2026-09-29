from datetime import date

from stock_scanner.data_provider import TencentProvider


class UnusedFallback:
    pass


def test_tencent_daily_bars_maps_ohlcv_and_preserves_amount_column(monkeypatch) -> None:
    provider = TencentProvider(fallback=UnusedFallback())
    monkeypatch.setattr(
        provider,
        "_fetch_window",
        lambda symbol, start, end: [
            ["2024-01-02", "10", "11", "12", "9", "1000"],
        ],
    )
    frame = provider._tencent_daily_bars(
        "000001", date(2024, 1, 1), date(2024, 1, 2)
    )
    assert frame.row(0, named=True) == {
        "symbol": "000001",
        "date": date(2024, 1, 2),
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "volume": 1000.0,
        "amount": None,
    }


def test_tencent_market_prefix_routes_stock_codes() -> None:
    assert TencentProvider._market_prefix("000035") == "sz"
    assert TencentProvider._market_prefix("600519") == "sh"
    assert TencentProvider._market_prefix("920982") == "bj"
