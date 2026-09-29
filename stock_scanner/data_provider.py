"""AkShare boundary: remote calls and conversion to the internal Polars schema."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, TypeVar

import polars as pl
import requests

LOG = logging.getLogger(__name__)
T = TypeVar("T")

DAILY_SCHEMA = {
    "symbol": pl.String,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
}


class RateLimiter:
    """Thread-safe minimum interval limiter shared by all AkShare calls."""

    def __init__(self, requests_per_second: float) -> None:
        self._interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delay = self._interval - (time.monotonic() - self._last_call)
            if delay > 0:
                time.sleep(delay)
            self._last_call = time.monotonic()


class AkShareProvider:
    """Fetch A-share symbols and daily bars with retry and request throttling."""

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        retries: int = 5,
        requests_per_second: float = 8.0,
        ak_module: Any | None = None,
    ) -> None:
        if ak_module is None:
            import akshare as ak_module

        self.ak = ak_module
        self.timeout = timeout
        self.retries = retries
        self.rate_limiter = RateLimiter(requests_per_second)

    def _retry(self, operation: Callable[[], T], description: str) -> T:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self.rate_limiter.wait()
            try:
                return operation()
            except Exception as exc:  # remote libraries expose inconsistent exceptions
                last_error = exc
                if attempt < self.retries:
                    delay = min(0.5 * (2 ** (attempt - 1)), 6) + random.uniform(0, 0.3)
                    LOG.warning(
                        "%s failed (%s/%s): %s", description, attempt, self.retries, exc
                    )
                    time.sleep(delay)
        assert last_error is not None
        raise RuntimeError(
            f"{description} failed after {self.retries} attempts: "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    def stock_list(self) -> pl.DataFrame:
        pdf = self._retry(self.ak.stock_info_a_code_name, "fetch stock list")
        frame = pl.from_pandas(pdf, include_index=False).rename(
            {"code": "symbol", "name": "name"}
        )
        return (
            frame.select(
                pl.col("symbol").cast(pl.String).str.zfill(6),
                pl.col("name").cast(pl.String),
            )
            .unique("symbol", keep="last")
            .sort("symbol")
        )

    def daily_bars(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        def fetch() -> Any:
            return self.ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="",
                timeout=self.timeout,
            )

        pdf = self._retry(fetch, f"fetch daily bars for {symbol}")
        if pdf.empty:
            return pl.DataFrame(schema=DAILY_SCHEMA)

        rename = {
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        }
        frame = pl.from_pandas(pdf, include_index=False).rename(rename)
        required = [name for name in DAILY_SCHEMA if name != "symbol"]
        missing = set(required) - set(frame.columns)
        if missing:
            raise ValueError(
                f"AkShare response for {symbol} misses columns: {sorted(missing)}"
            )
        return (
            frame.select(
                pl.lit(symbol).alias("symbol"),
                pl.col("date").cast(pl.Date),
                *(
                    pl.col(column).cast(pl.Float64, strict=False)
                    for column in required[1:]
                ),
            )
            .drop_nulls(["date", "close"])
            .sort("date")
        )


class TencentProvider:
    """Tencent daily K-lines for SH/SZ, with host rotation and AkShare fallback."""

    HOSTS = (
        "https://web.ifzq.gtimg.cn",
        "https://proxy.finance.qq.com/ifzqgtimg",
        "https://ifzq.gtimg.cn",
    )
    PATH = "/appstock/app/fqkline/get"
    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        retries: int = 5,
        requests_per_second: float = 8.0,
        fallback: AkShareProvider | None = None,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.rate_limiter = RateLimiter(requests_per_second)
        self.fallback = fallback or AkShareProvider(
            timeout=timeout,
            retries=retries,
            requests_per_second=min(requests_per_second, 3.0),
        )
        self._host_lock = threading.Lock()
        self._host_cursor = 0
        self._down_until: dict[str, float] = {}
        self._local = threading.local()

    def stock_list(self) -> pl.DataFrame:
        return self.fallback.stock_list()

    @staticmethod
    def _market_prefix(symbol: str) -> str:
        if symbol.startswith(("4", "8", "92")):
            return "bj"
        return "sh" if symbol.startswith(("5", "6", "9")) else "sz"

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {"User-Agent": self.USER_AGENT, "Referer": "https://gu.qq.com/"}
            )
            self._local.session = session
        return session

    def _available_hosts(self) -> list[str]:
        now = time.monotonic()
        with self._host_lock:
            start = self._host_cursor
            self._host_cursor = (self._host_cursor + 1) % len(self.HOSTS)
            ordered = list(self.HOSTS[start:] + self.HOSTS[:start])
            available = [host for host in ordered if self._down_until.get(host, 0) <= now]
        return available or ordered

    def _cool_down(self, host: str) -> None:
        with self._host_lock:
            self._down_until[host] = time.monotonic() + 30.0

    def _fetch_window(self, market_symbol: str, start: date, end: date) -> list[list[Any]]:
        parameter = f"{market_symbol},day,{start.isoformat()},{end.isoformat()},640,"
        errors: list[str] = []
        for host in self._available_hosts():
            self.rate_limiter.wait()
            try:
                response = self._session().get(
                    host + self.PATH,
                    params={"param": parameter},
                    timeout=(min(self.timeout, 8), self.timeout),
                )
                response.raise_for_status()
                payload = response.json() if response.text.strip() else {}
                data = payload.get("data") if isinstance(payload, dict) else None
                node = data.get(market_symbol) if isinstance(data, dict) else None
                if not isinstance(node, dict):
                    raise RuntimeError("empty or malformed response")
                items = node.get("day")
                if not isinstance(items, list):
                    raise RuntimeError("daily K-line list is missing")
                return items
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                errors.append(f"{host}: {type(exc).__name__}: {exc}")
                self._cool_down(host)
        raise RuntimeError("all Tencent K-line hosts failed: " + "; ".join(errors))

    def _tencent_daily_bars(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        prefix = self._market_prefix(symbol)
        if prefix == "bj":
            raise ValueError("Tencent historical K-lines do not support BSE")
        market_symbol = prefix + symbol
        rows: list[dict[str, Any]] = []
        cursor = start
        while cursor <= end:
            stop = min(cursor + timedelta(days=699), end)
            for item in self._fetch_window(market_symbol, cursor, stop):
                if len(item) < 6:
                    raise RuntimeError(f"Tencent returned a short K-line row: {item!r}")
                rows.append(
                    {
                        "symbol": symbol,
                        "date": item[0],
                        "open": item[1],
                        "high": item[3],
                        "low": item[4],
                        "close": item[2],
                        "volume": item[5],
                        "amount": None,
                    }
                )
            cursor = stop + timedelta(days=1)
        if not rows:
            return pl.DataFrame(schema=DAILY_SCHEMA)
        return (
            pl.DataFrame(rows)
            .select(
                pl.col("symbol").cast(pl.String),
                pl.col("date").str.to_date(),
                *(pl.col(column).cast(pl.Float64, strict=False) for column in DAILY_SCHEMA if column not in ("symbol", "date")),
            )
            .unique(["symbol", "date"], keep="last")
            .sort("date")
        )

    def daily_bars(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        try:
            return self._tencent_daily_bars(symbol, start, end)
        except Exception as exc:
            LOG.warning("Tencent failed for %s, falling back to AkShare: %s", symbol, exc)
            return self.fallback.daily_bars(symbol, start, end)
