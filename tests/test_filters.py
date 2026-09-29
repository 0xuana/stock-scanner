import polars as pl

from stock_scanner.filters import (
    any_golden_cross_report,
    apply_filter,
    filter_cross_combinations,
    monthly_weekly_recent_cross_report,
)


def test_default_filter_requires_all_conditions() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["yes", "no"],
            "daily_golden_cross": [True, True],
            "weekly_hist": [1.0, -1.0],
            "monthly_hist": [1.0, 1.0],
            "45d_hist": [1.0, 1.0],
        }
    )
    assert apply_filter(frame).get_column("symbol").to_list() == ["yes"]


def test_cross_combinations_are_exact_and_mutually_exclusive() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["all", "dwm", "wm45", "other"],
            "daily_golden_cross": [True, True, False, True],
            "weekly_golden_cross": [True, True, True, False],
            "monthly_golden_cross": [True, True, True, True],
            "45d_golden_cross": [True, False, True, False],
        }
    )
    groups = filter_cross_combinations(frame)
    assert groups["all_four"].get_column("symbol").to_list() == ["all"]
    assert groups["daily_weekly_monthly_only"].get_column("symbol").to_list() == ["dwm"]
    assert groups["weekly_monthly_45d_only"].get_column("symbol").to_list() == ["wm45"]


def test_any_golden_cross_report_keeps_true_and_blanks_false() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["000001", "000002", "000003"],
            "name": ["日线", "周月45", "无金叉"],
            "daily_golden_cross": [True, False, False],
            "weekly_golden_cross": [False, True, False],
            "monthly_golden_cross": [False, True, False],
            "45d_golden_cross": [False, True, False],
        }
    )
    report = any_golden_cross_report(frame)
    assert report.to_dicts() == [
        {
            "symbol": "000001",
            "name": "日线",
            "daily": "TRUE",
            "weekly": None,
            "monthly": None,
            "45d": None,
        },
        {
            "symbol": "000002",
            "name": "周月45",
            "daily": None,
            "weekly": "TRUE",
            "monthly": "TRUE",
            "45d": "TRUE",
        },
    ]


def test_monthly_cross_with_weekly_cross_zero_to_four_bars_ago() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["keep0", "keep4", "too_old", "no_month", "too_far"],
            "name": ["a", "b", "c", "d", "e"],
            "monthly_golden_cross": [True, True, True, False, True],
            "weekly_golden_cross_bars_ago": [0, 4, 5, 2, 3],
            "weekly_golden_cross_date": ["2026-09-23"] * 5,
            "daily_cross_days_before_weekly": [5] * 5,
            "daily_golden_cross_before_weekly_date": ["2026-09-18"] * 5,
            "latest_close": [10.0] * 5,
            "three_month_low": [6.0, 7.0, 8.0, 8.0, 5.0],
            "three_month_low_date": ["2026-08-01"] * 5,
            "three_month_low_distance_pct": [40.0, 30.0, 20.0, 20.0, 50.0],
            "monthly_dif": [2.0, 3.123456, 4.0, 5.0, 6.0],
            "monthly_dea": [1.5, 2.567891, 3.5, 4.5, 5.5],
            "weekly_dif": [1.0, 2.123456, 3.0, 4.0, 5.0],
            "weekly_dea": [0.8, 1.876543, 2.8, 3.8, 4.8],
        }
    )
    report = monthly_weekly_recent_cross_report(frame)
    assert report.get_column("symbol").to_list() == ["keep4", "keep0"]
    assert report.get_column("daily_cross_days_before_weekly").to_list() == [5, 5]
    assert report.columns[:6] == [
        "symbol",
        "name",
        "three_month_low_distance_pct",
        "latest_close",
        "three_month_low",
        "three_month_low_date",
    ]
    assert report[0, "monthly_dif"] == 3.1235
    assert report[0, "monthly_dea"] == 2.5679
    assert report[0, "monthly_dea_minus_dif"] == -0.5556
    assert report[0, "weekly_dif"] == 2.1235
    assert report[0, "weekly_dea"] == 1.8765
    assert report[0, "weekly_dea_minus_dif"] == -0.2469
