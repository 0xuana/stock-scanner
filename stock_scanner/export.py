"""File export and report presentation for scanner results."""

from __future__ import annotations

import html
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl
import requests

from .filters import (
    CROSS_COLUMNS,
    CROSS_COMBINATIONS,
    any_golden_cross_report,
    filter_cross_combinations,
    monthly_weekly_recent_cross_report,
)

LOG = logging.getLogger(__name__)

MONTHLY_WEEKLY_REPORT_COLUMNS = {
    "symbol": "股票代码",
    "name": "股票名称",
    "weekly_cross_bars_ago": "周线金叉距今周数",
    "weekly_golden_cross_date": "周线金叉日期",
    "daily_cross_days_before_weekly": "日线金叉领先天数",
    "daily_golden_cross_before_weekly_date": "日线金叉日期",
    "latest_close": "最新收盘价",
    "three_month_low": "近三月最低价",
    "three_month_low_date": "近三月最低价日期",
    "three_month_low_distance_pct": "现价较近三月最低价涨幅（%）",
    "monthly_dif": "月线 DIF",
    "monthly_dea": "月线 DEA",
    "monthly_dea_minus_dif": "月线 DEA-DIF",
    "weekly_dif": "周线 DIF",
    "weekly_dea": "周线 DEA",
    "weekly_dea_minus_dif": "周线 DEA-DIF",
}


@dataclass(frozen=True)
class ExportSummary:
    archived_csv: Path
    cross_groups: dict[str, pl.DataFrame]


def export_csv(frame: pl.DataFrame, path: Path) -> Path:
    """Export one DataFrame as CSV, creating its directory when needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_csv(path)
    return path


def export_parquet(frame: pl.DataFrame, path: Path) -> Path:
    """Export one DataFrame as compressed Parquet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path, compression="zstd")
    return path


def eastmoney_unified_url(symbol: str) -> str:
    """Return Eastmoney's unified quote URL for an A-share symbol."""
    market = "1" if symbol.startswith("6") else "0"
    return f"https://quote.eastmoney.com/unify/r/{market}.{symbol}"


def get_eastmoney_chart_url(secid: str) -> str:
    """Resolve Eastmoney's redirect and return its full-screen chart URL."""
    unified_url = f"https://quote.eastmoney.com/unify/r/{secid}"
    try:
        response = requests.get(unified_url, allow_redirects=True, timeout=10)
        response.raise_for_status()
        final_url = response.url.split("#", maxsplit=1)[0]
    except requests.RequestException as exc:
        LOG.warning("Eastmoney redirect resolution failed for %s: %s", secid, exc)
        final_url = unified_url
    return f"{final_url}#fullScreenChart"


def export_html(
    frame: pl.DataFrame,
    path: Path,
    quote_url_resolver: Callable[[str], str] | None = None,
    generated_on: date | None = None,
) -> Path:
    """Export a dependency-free Chinese table that sorts by any column."""
    path.parent.mkdir(parents=True, exist_ok=True)
    resolve_quote_url = quote_url_resolver or get_eastmoney_chart_url
    report_date = generated_on or date.today()
    headers = "".join(
        f'<th scope="col" data-column="{index}">{html.escape(MONTHLY_WEEKLY_REPORT_COLUMNS.get(column, column))}</th>'
        for index, column in enumerate(frame.columns)
    )
    body_rows: list[str] = []
    for row in frame.iter_rows(named=True):
        cells: list[str] = []
        for column in frame.columns:
            value = row[column]
            display = "" if value is None else str(value)
            content = html.escape(display)
            if column == "name":
                symbol = str(row["symbol"])
                market = "1" if symbol.startswith("6") else "0"
                url = html.escape(resolve_quote_url(f"{market}.{symbol}"), quote=True)
                content = (
                    f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
                    f"{content}</a>"
                )
            cells.append(
                f'<td data-sort-value="{html.escape(display, quote=True)}">{content}</td>'
            )
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>月线金叉与周线近期金叉报表（{report_date:%Y-%m-%d}）</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; color: #1f2937; }}
    h1 {{ font-size: 1.35rem; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ border-collapse: collapse; width: 100%; white-space: nowrap; }}
    th, td {{ border: 1px solid #d1d5db; padding: 8px 10px; text-align: right; }}
    th {{ background: #f3f4f6; cursor: pointer; user-select: none; position: sticky; top: 0; }}
    th::after {{ content: " ↕"; color: #9ca3af; }}
    th[data-direction="asc"]::after {{ content: " ↑"; color: #111827; }}
    th[data-direction="desc"]::after {{ content: " ↓"; color: #111827; }}
    tr:nth-child(even) {{ background: #f9fafb; }}
    a {{ color: #0369a1; }}
  </style>
</head>
<body>
  <h1>月线金叉与周线近 5 周金叉（{report_date:%Y-%m-%d}）</h1>
  <p>共 {frame.height} 只股票；点击任意列名排序。</p>
  <div class="table-wrap">
    <table id="report"><thead><tr>{headers}</tr></thead><tbody>{"".join(body_rows)}</tbody></table>
  </div>
  <script>
    const table = document.querySelector('#report');
    const headers = [...table.querySelectorAll('th')];
    headers.forEach((header) => header.addEventListener('click', () => {{
      const column = Number(header.dataset.column);
      const direction = header.dataset.direction === 'asc' ? 'desc' : 'asc';
      headers.forEach((item) => delete item.dataset.direction);
      header.dataset.direction = direction;
      const rows = [...table.tBodies[0].rows];
      rows.sort((left, right) => {{
        const a = left.cells[column].dataset.sortValue;
        const b = right.cells[column].dataset.sortValue;
        if (a === '' || b === '') return a === b ? 0 : (a === '' ? 1 : -1);
        const aNumber = Number(a);
        const bNumber = Number(b);
        const comparison = Number.isNaN(aNumber) || Number.isNaN(bNumber)
          ? a.localeCompare(b, 'zh-CN', {{ numeric: true }})
          : aNumber - bNumber;
        return direction === 'asc' ? comparison : -comparison;
      }});
      rows.forEach((row) => table.tBodies[0].appendChild(row));
    }}));
  </script>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")
    return path


def export_cross_reports(
    frame: pl.DataFrame,
    directory: Path,
    generated_on: date | None = None,
) -> dict[str, pl.DataFrame]:
    """Export all golden-cross CSV reports and the dated HTML report."""
    groups = filter_cross_combinations(frame)
    report_columns = ["symbol", "name", *CROSS_COLUMNS]
    combined: list[pl.DataFrame] = []
    for key, group in groups.items():
        report = (
            group.select(report_columns)
            .with_columns(pl.lit(CROSS_COMBINATIONS[key]["label"]).alias("match_type"))
            .select("symbol", "name", "match_type", *CROSS_COLUMNS)
        )
        combined.append(report)
    export_csv(any_golden_cross_report(frame), directory / "golden_cross_any.csv")
    if "weekly_golden_cross_bars_ago" in frame.columns:
        report_date = generated_on or date.today()
        export_html(
            monthly_weekly_recent_cross_report(frame),
            directory / f"html/monthly_golden_weekly_cross_0_4_{report_date:%Y%m%d}.html",
            generated_on=report_date,
        )
    return groups


def export_latest_results(
    frame: pl.DataFrame,
    directory: Path,
    generated_at: datetime | None = None,
) -> Path:
    """Export the stable latest files and one timestamped CSV archive."""
    export_parquet(frame, directory / "latest.parquet")
    # export_csv(frame, directory / "latest.csv")
    timestamp = (generated_at or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return export_csv(frame, directory / f"latest_{timestamp}.csv")


def export_scan_outputs(
    all_results: pl.DataFrame,
    failed_results: pl.DataFrame,
    matched: pl.DataFrame,
    directory: Path,
    generated_at: datetime | None = None,
) -> ExportSummary:
    """Export every file produced by one scan through a single interface."""
    archived_csv = export_latest_results(all_results, directory, generated_at)
    export_csv(failed_results, directory / "failures.csv")
    cross_groups = (
        export_cross_reports(all_results, directory) if all_results.height else {}
    )
    export_parquet(matched, directory / "matched.parquet")
    # export_csv(matched, directory / "matched.csv")
    return ExportSummary(archived_csv=archived_csv, cross_groups=cross_groups)
