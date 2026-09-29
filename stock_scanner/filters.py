"""Configurable scanner filters, kept separate from indicator calculations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import polars as pl

Operator = Literal["eq", "gt", "ge", "lt", "le"]


@dataclass(frozen=True)
class Condition:
    column: str
    operator: Operator
    value: bool | float


DEFAULT_CONDITIONS = (
    Condition("daily_golden_cross", "eq", True),
    Condition("weekly_hist", "gt", 0.0),
    Condition("monthly_hist", "gt", 0.0),
    Condition("45d_hist", "gt", 0.0),
)

CROSS_COLUMNS = (
    "daily_golden_cross",
    "weekly_golden_cross",
    "monthly_golden_cross",
    "45d_golden_cross",
)

CROSS_COMBINATIONS = {
    "all_four": {
        "label": "日线+周线+月线+45日线金叉",
        "required": CROSS_COLUMNS,
        "excluded": (),
    },
    "daily_weekly_monthly_only": {
        "label": "仅日线+周线+月线金叉",
        "required": CROSS_COLUMNS[:3],
        "excluded": (CROSS_COLUMNS[3],),
    },
    "weekly_monthly_45d_only": {
        "label": "仅周线+月线+45日线金叉",
        "required": CROSS_COLUMNS[1:],
        "excluded": (CROSS_COLUMNS[0],),
    },
}


def apply_filter(
    frame: pl.DataFrame, conditions: tuple[Condition, ...] = DEFAULT_CONDITIONS
) -> pl.DataFrame:
    expression = pl.lit(True)
    operations = {
        "eq": lambda column, value: column == value,
        "gt": lambda column, value: column > value,
        "ge": lambda column, value: column >= value,
        "lt": lambda column, value: column < value,
        "le": lambda column, value: column <= value,
    }
    for condition in conditions:
        if condition.column not in frame.columns:
            raise ValueError(f"filter column does not exist: {condition.column}")
        expression &= operations[condition.operator](
            pl.col(condition.column), condition.value
        )
    return frame.filter(expression.fill_null(False))


def filter_cross_combinations(frame: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Return the three requested, mutually exclusive golden-cross groups."""
    missing = set(CROSS_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"golden-cross columns do not exist: {sorted(missing)}")

    groups: dict[str, pl.DataFrame] = {}
    for key, rule in CROSS_COMBINATIONS.items():
        expression = pl.lit(True)
        for column in rule["required"]:
            expression &= pl.col(column)
        for column in rule["excluded"]:
            expression &= ~pl.col(column)
        groups[key] = frame.filter(expression.fill_null(False))
    return groups


def any_golden_cross_report(frame: pl.DataFrame) -> pl.DataFrame:
    """Return stocks crossing on any timeframe, with false states left blank."""
    missing = set(CROSS_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"golden-cross columns do not exist: {sorted(missing)}")

    any_cross = pl.any_horizontal(
        *(pl.col(column).fill_null(False) for column in CROSS_COLUMNS)
    )
    display_columns = [
        pl.when(pl.col(column).fill_null(False))
        .then(pl.lit("TRUE"))
        .otherwise(pl.lit(None, dtype=pl.String))
        .alias(column.removesuffix("_golden_cross"))
        for column in CROSS_COLUMNS
    ]
    return frame.filter(any_cross).select("symbol", "name", *display_columns).sort("symbol")


def monthly_weekly_recent_cross_report(frame: pl.DataFrame) -> pl.DataFrame:
    """Monthly cross now, with a weekly cross zero to four bars ago."""
    required = {
        "monthly_golden_cross",
        "weekly_golden_cross_bars_ago",
        "monthly_dif",
        "monthly_dea",
        "weekly_dif",
        "weekly_dea",
        "latest_close",
        "three_month_low",
        "three_month_low_date",
        "three_month_low_distance_pct",
        "weekly_golden_cross_date",
        "daily_cross_days_before_weekly",
        "daily_golden_cross_before_weekly_date",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"monthly/weekly cross columns do not exist: {sorted(missing)}")
    return (
        frame.filter(
            pl.col("monthly_golden_cross").fill_null(False)
            & pl.col("weekly_golden_cross_bars_ago").is_between(0, 4, closed="both")
            & (pl.col("three_month_low_distance_pct") <= 40.0)
        )
        .select(
            "symbol",
            "name",
            "three_month_low_distance_pct",
            "latest_close",
            "three_month_low",
            "three_month_low_date",
            pl.col("weekly_golden_cross_bars_ago").alias("weekly_cross_bars_ago"),
            "weekly_golden_cross_date",
            "daily_cross_days_before_weekly",
            "daily_golden_cross_before_weekly_date",
            pl.col("monthly_dif").round(4),
            pl.col("monthly_dea").round(4),
            (pl.col("monthly_dea") - pl.col("monthly_dif")).round(4).alias(
                "monthly_dea_minus_dif"
            ),
            pl.col("weekly_dif").round(4),
            pl.col("weekly_dea").round(4),
            (pl.col("weekly_dea") - pl.col("weekly_dif")).round(4).alias(
                "weekly_dea_minus_dif"
            ),
        )
        .sort(
            [
                "three_month_low_distance_pct",
                "weekly_cross_bars_ago",
                "symbol",
            ]
        )
    )
