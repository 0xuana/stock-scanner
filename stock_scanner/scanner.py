"""Command-line orchestration for the A-share MACD scanner."""

from __future__ import annotations

import argparse
import calendar
import contextlib
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

from .cache import ParquetCache
from .data_provider import AkShareProvider, TencentProvider
from .export import export_scan_outputs
from .filters import apply_filter
from .indicators import macd
from .timeframe import build_timeframes

load_dotenv(".env")
DEFAULT_DATA_DIR = Path(os.getenv("DATA_STORAGE_DIR") or "data")

LOG = logging.getLogger(__name__)
STATE_COLUMNS = (
    "dif",
    "dea",
    "hist",
    "golden_cross",
    "death_cross",
    "above_zero",
    "below_zero",
    "hist_rising",
    "hist_falling",
)


@dataclass(frozen=True)
class ScanConfig:
    data_dir: Path = DEFAULT_DATA_DIR
    results_dir: Path = Path("results")
    initial_start: date = date(2010, 1, 1)
    end: date = date.today()
    workers: int = 24
    timeout: float = 20.0
    retries: int = 5
    requests_per_second: float = 12.0
    limit: int | None = None
    data_source: str = "tencent"
    include_st: bool = False


def latest_macd_state(symbol: str, name: str, daily: pl.DataFrame) -> dict[str, object]:
    row: dict[str, object] = {"symbol": symbol, "name": name}
    calculated_timeframes: dict[str, pl.DataFrame] = {}
    for timeframe, bars in build_timeframes(daily).items():
        calculated = macd(bars)
        calculated_timeframes[timeframe] = calculated
        latest = calculated.tail(1).to_dicts()[0]
        for column in STATE_COLUMNS:
            row[f"{timeframe}_{column}"] = latest[column]
        if timeframe == "weekly":
            recent_crosses = calculated.tail(5).get_column("golden_cross").to_list()
            distances = [
                distance
                for distance, crossed in enumerate(reversed(recent_crosses))
                if crossed
            ]
            row["weekly_golden_cross_bars_ago"] = min(distances) if distances else None

    daily_macd = calculated_timeframes["daily"]
    latest_daily = daily_macd.tail(1).to_dicts()[0]
    latest_date = latest_daily["date"]
    month_number = latest_date.year * 12 + latest_date.month - 1 - 3
    cutoff_year, cutoff_month_zero = divmod(month_number, 12)
    cutoff_month = cutoff_month_zero + 1
    cutoff_date = latest_date.replace(
        year=cutoff_year,
        month=cutoff_month,
        day=min(latest_date.day, calendar.monthrange(cutoff_year, cutoff_month)[1]),
    )
    latest_close = float(latest_daily["close"])
    three_month_bars = daily_macd.filter(pl.col("date") >= cutoff_date)
    three_month_low = three_month_bars.select(pl.col("low").min()).item()
    three_month_low_date = (
        three_month_bars.filter(pl.col("low") == three_month_low)
        .get_column("date")
        .tail(1)
        .item()
        if three_month_low is not None
        else None
    )
    row["latest_close"] = latest_close
    row["three_month_low"] = three_month_low
    row["three_month_low_date"] = three_month_low_date
    row["three_month_low_distance_pct"] = (
        (latest_close - float(three_month_low)) / float(three_month_low) * 100
        if three_month_low is not None and float(three_month_low) > 0
        else None
    )

    weekly_distance = row.get("weekly_golden_cross_bars_ago")
    row["weekly_golden_cross_date"] = None
    row["daily_cross_days_before_weekly"] = None
    row["daily_golden_cross_before_weekly_date"] = None
    if weekly_distance is not None:
        weekly_macd = calculated_timeframes["weekly"]
        weekly_cross_date = weekly_macd.row(
            weekly_macd.height - 1 - int(weekly_distance), named=True
        )["date"]
        row["weekly_golden_cross_date"] = weekly_cross_date
        previous_daily_crosses = daily_macd.filter(
            pl.col("golden_cross") & (pl.col("date") <= weekly_cross_date)
        )
        if not previous_daily_crosses.is_empty():
            daily_cross_date = previous_daily_crosses.get_column("date").tail(1).item()
            row["daily_cross_days_before_weekly"] = (
                weekly_cross_date - daily_cross_date
            ).days
            row["daily_golden_cross_before_weekly_date"] = daily_cross_date
    return row


def _root_error(exc: Exception) -> tuple[str, str]:
    root: BaseException = exc
    while root.__cause__ is not None:
        root = root.__cause__
    return type(root).__name__, str(root)


def build_stock_universe(
    stocks: pl.DataFrame, *, include_st: bool = False
) -> pl.DataFrame:
    """Filter the stock table before scheduling per-symbol work."""
    if include_st:
        return stocks
    return stocks.filter(~pl.col("name").str.to_uppercase().str.contains(r"\*?ST"))


def run_scan(
    config: ScanConfig, provider: AkShareProvider | TencentProvider | None = None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if provider is None:
        provider_class = (
            TencentProvider if config.data_source == "tencent" else AkShareProvider
        )
        provider = provider_class(
            timeout=config.timeout,
            retries=config.retries,
            requests_per_second=config.requests_per_second,
        )
    all_stocks = provider.stock_list()
    stocks = build_stock_universe(all_stocks, include_st=config.include_st)
    LOG.info(
        "stock universe ready: total=%s selected=%s excluded_st=%s",
        all_stocks.height,
        stocks.height,
        all_stocks.height - stocks.height,
    )
    if config.limit is not None:
        stocks = stocks.head(config.limit)
    cache = ParquetCache(config.data_dir)
    rows: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []

    def scan_one(stock: dict[str, object]) -> dict[str, object]:
        symbol, name = str(stock["symbol"]), str(stock["name"])
        daily = cache.update(
            provider,
            symbol,
            initial_start=config.initial_start,
            end=config.end,
        )
        if daily.is_empty():
            raise ValueError("no daily bars available")
        return latest_macd_state(symbol, name, daily)

    stock_rows = stocks.to_dicts()
    with ThreadPoolExecutor(max_workers=config.workers) as executor:
        future_symbols = {
            executor.submit(scan_one, stock): str(stock["symbol"]) for stock in stock_rows
        }
        for completed, future in enumerate(as_completed(future_symbols), start=1):
            symbol = future_symbols[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                error_type, error = _root_error(exc)
                LOG.error("scan failed for %s: %s: %s", symbol, error_type, error)
                failures.append(
                    {"symbol": symbol, "error_type": error_type, "error": error}
                )
            if completed % 100 == 0 or completed == len(future_symbols):
                LOG.info(
                    "processed %s/%s stocks; failures=%s",
                    completed,
                    len(future_symbols),
                    len(failures),
                )

    all_results = (
        pl.DataFrame(rows).sort("symbol")
        if rows
        else pl.DataFrame({"symbol": [], "name": []})
    )
    failed_results = (
        pl.DataFrame(failures).sort("symbol")
        if failures
        else pl.DataFrame(
            schema={"symbol": pl.String, "error_type": pl.String, "error": pl.String}
        )
    )
    matched = apply_filter(all_results) if rows else all_results
    exports = export_scan_outputs(
        all_results, failed_results, matched, config.results_dir
    )
    LOG.info("timestamped latest CSV written: %s", exports.archived_csv)
    for key, group in exports.cross_groups.items():
        LOG.info("golden-cross group %s: %s stocks", key, group.height)
    return matched, failed_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan all A-shares using multi-timeframe MACD"
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2018, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument(
        "--rate", type=float, default=12.0, help="maximum market-data requests per second"
    )
    parser.add_argument(
        "--limit", type=int, help="only scan the first N symbols (useful for smoke tests)"
    )
    parser.add_argument(
        "--data-source",
        choices=("tencent", "akshare"),
        default="tencent",
        help="daily K-line source; Tencent rotates three low-ban-risk hosts",
    )
    parser.add_argument(
        "--include-st",
        action="store_true",
        help="include ST and *ST stocks in the scan universe",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path("logs/scanner.log"),
        help="append all output to this log file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    with args.log_file.open("a", encoding="utf-8", buffering=1) as output:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
                handlers=[logging.FileHandler(args.log_file, encoding="utf-8")],
                force=True,
            )
            if args.workers <= 0:
                LOG.error("--workers must be positive")
                raise SystemExit(2)
            if args.rate <= 0:
                LOG.error("--rate must be positive")
                raise SystemExit(2)
            LOG.info(
                "scan started: source=%s workers=%s rate=%s retries=%s timeout=%s",
                args.data_source,
                args.workers,
                args.rate,
                args.retries,
                args.timeout,
            )
            matched, failures = run_scan(
                ScanConfig(
                    data_dir=args.data_dir,
                    results_dir=args.results_dir,
                    initial_start=args.start,
                    end=args.end,
                    workers=args.workers,
                    timeout=args.timeout,
                    retries=args.retries,
                    requests_per_second=args.rate,
                    limit=args.limit,
                    data_source=args.data_source,
                    include_st=args.include_st,
                )
            )
            LOG.info(
                "scan finished: matched=%s failures=%s results=%s",
                matched.height,
                failures.height,
                args.results_dir,
            )
            if not matched.is_empty():
                LOG.info("matched stocks:\n%s", matched.select("symbol", "name"))


if __name__ == "__main__":
    main()
