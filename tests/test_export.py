from datetime import date, datetime
import re

import polars as pl
import requests

from stock_scanner.export import (
    eastmoney_unified_url,
    export_cross_reports,
    export_html,
    export_latest_results,
    get_eastmoney_chart_url,
)


# def test_cross_reports_write_combined_and_separate_csv_files(tmp_path) -> None:
#     frame = pl.DataFrame(
#         {
#             "symbol": ["000001", "000002", "000003"],
#             "name": ["全部", "日周月", "周月45"],
#             "daily_golden_cross": [True, True, False],
#             "weekly_golden_cross": [True, True, True],
#             "monthly_golden_cross": [True, True, True],
#             "45d_golden_cross": [True, False, True],
#         }
#     )
#     export_cross_reports(frame, tmp_path)

#     combined = pl.read_csv(
#         tmp_path / "golden_cross_combinations.csv",
#         schema_overrides={"symbol": pl.String},
#     )
#     assert combined.get_column("symbol").sort().to_list() == [
#         "000001",
#         "000002",
#         "000003",
#     ]
#     assert (tmp_path / "golden_cross_all_four.csv").exists()
#     assert (tmp_path / "golden_cross_daily_weekly_monthly_only.csv").exists()
#     assert (tmp_path / "golden_cross_weekly_monthly_45d_only.csv").exists()
#     assert (tmp_path / "golden_cross_any.csv").exists()


def test_latest_exports_keep_stable_names_and_timestamped_archive(tmp_path) -> None:
    frame = pl.DataFrame({"symbol": ["000001"], "name": ["平安银行"]})
    archived = export_latest_results(
        frame, tmp_path, datetime(2026, 9, 23, 14, 5, 6)
    )
    assert (tmp_path / "latest.csv").exists()
    assert (tmp_path / "latest.parquet").exists()
    assert archived == tmp_path / "latest_20260923-140506.csv"
    assert archived.exists()


def test_html_export_has_ordered_chinese_headers_and_eastmoney_links(tmp_path) -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["688203", "301150"],
            "name": ["海正生材", "冠龙节能"],
            "three_month_low_distance_pct": [20.0, 30.0],
            "latest_close": [10.5, 20.0],
            "three_month_low": [8.0, 15.0],
            "three_month_low_date": [date(2026, 8, 1), date(2026, 8, 2)],
        }
    )
    output = tmp_path / "report.html"
    resolved_urls = {
        "1.688203": "https://quote.eastmoney.com/kcb/688203.html#fullScreenChart",
        "0.301150": "https://quote.eastmoney.com/sz301150.html#fullScreenChart",
    }

    export_html(
        frame,
        output,
        resolved_urls.__getitem__,
        generated_on=date(2026, 9, 23),
    )

    document = output.read_text(encoding="utf-8")
    headers = [
        "股票代码",
        "股票名称",
        "现价较近三月最低价涨幅（%）",
        "最新收盘价",
        "近三月最低价",
        "近三月最低价日期",
    ]
    assert re.findall(r'<th scope="col" data-column="\d+">([^<]+)</th>', document) == headers
    assert "https://quote.eastmoney.com/kcb/688203.html#fullScreenChart" in document
    assert "https://quote.eastmoney.com/sz301150.html#fullScreenChart" in document
    assert "addEventListener('click'" in document
    assert "月线金叉与周线近期金叉报表（2026-09-23）" in document
    assert "月线金叉与周线近 5 周金叉（2026-09-23）" in document


def test_eastmoney_unified_url_uses_stock_market_prefix() -> None:
    assert eastmoney_unified_url("688203").endswith("/1.688203")
    assert eastmoney_unified_url("301150").endswith("/0.301150")


class RedirectResponse:
    def __init__(self, url: str, error: Exception | None = None) -> None:
        self.url = url
        self.error = error

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error


def test_eastmoney_chart_url_follows_redirect_and_adds_fragment(monkeypatch) -> None:
    def fake_get(url: str, *, allow_redirects: bool, timeout: int) -> RedirectResponse:
        assert url == "https://quote.eastmoney.com/unify/r/1.688203"
        assert allow_redirects is True
        assert timeout == 10
        return RedirectResponse("https://quote.eastmoney.com/kcb/688203.html#old")

    monkeypatch.setattr("stock_scanner.export.requests.get", fake_get)
    assert get_eastmoney_chart_url("1.688203") == (
        "https://quote.eastmoney.com/kcb/688203.html#fullScreenChart"
    )


def test_eastmoney_chart_url_falls_back_to_unified_url(monkeypatch) -> None:
    def failing_get(url: str, *, allow_redirects: bool, timeout: int) -> RedirectResponse:
        raise requests.Timeout("timed out")

    monkeypatch.setattr("stock_scanner.export.requests.get", failing_get)
    assert get_eastmoney_chart_url("0.301150") == (
        "https://quote.eastmoney.com/unify/r/0.301150#fullScreenChart"
    )


def test_monthly_weekly_report_filename_contains_generation_date(
    tmp_path, monkeypatch
) -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["688203"],
            "name": ["海正生材"],
            "daily_golden_cross": [False],
            "weekly_golden_cross": [True],
            "monthly_golden_cross": [True],
            "45d_golden_cross": [False],
            "weekly_golden_cross_bars_ago": [0],
            "weekly_golden_cross_date": [date(2026, 9, 18)],
            "daily_cross_days_before_weekly": [3],
            "daily_golden_cross_before_weekly_date": [date(2026, 9, 15)],
            "latest_close": [10.0],
            "three_month_low": [8.0],
            "three_month_low_date": [date(2026, 8, 1)],
            "three_month_low_distance_pct": [25.0],
            "monthly_dif": [0.222222],
            "monthly_dea": [0.111111],
            "weekly_dif": [0.333333],
            "weekly_dea": [0.222222],
        }
    )
    monkeypatch.setattr(
        "stock_scanner.export.get_eastmoney_chart_url",
        lambda secid: f"https://example.test/{secid}#fullScreenChart",
    )

    export_cross_reports(frame, tmp_path, generated_on=date(2026, 9, 23))

    output = tmp_path / "monthly_golden_weekly_cross_0_4_20260923.html"
    document = output.read_text(encoding="utf-8")
    assert output.exists()
    assert "0.2222" in document
    assert "0.1111" in document
    assert "0.222222" not in document
