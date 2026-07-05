"""
Crypto OHLCV Data Fetcher
==========================
Sources:
  - Binance (primary)   -- 1000 bars/request, years of history, no auth required
  - Coinbase (fallback) -- 300 bars/request, public endpoint, no auth required

Features:
  - Completely free, no API keys needed
  - Paginated fetching for full year+ of 1-min/5-min bars
  - SQLite caching -- only fetches missing data on subsequent calls
  - Same interface as tradestation_fetcher.py / alpaca_fetcher.py

Setup:
  pip install requests pandas pyarrow

Usage:
  fetcher = CryptoFetcher()                        # spot mode (default)
  fetcher = CryptoFetcher(futures_mode=True)       # crypto futures (USDT-M perpetuals)

  # Spot Binance (primary)
  df = fetcher.get_bars("BTCUSDT",  interval="1m",  days_back=365)
  df = fetcher.get_bars("ETHUSDT",  interval="5m",  days_back=365)
  df = fetcher.get_bars("SOLUSDT",  interval="1h",  days_back=365)
  df = fetcher.get_bars("DOGEUSDT", interval="1d",  days_back=730)

  # Coinbase (fallback or alternative)
  df = fetcher.get_bars_coinbase("BTC-USD", interval="60",   days_back=365)  # 1-min
  df = fetcher.get_bars_coinbase("ETH-USD", interval="300",  days_back=365)  # 5-min
  df = fetcher.get_bars_coinbase("SOL-USD", interval="3600", days_back=365)  # 1-hour

  # Multiple symbols
  dfs = fetcher.get_multiple(["BTCUSDT","ETHUSDT","SOLUSDT"], interval="5m", days_back=365)

Binance intervals:
  1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M

Coinbase intervals (seconds):
  60 (1m), 300 (5m), 900 (15m), 3600 (1h), 21600 (6h), 86400 (1d)

Binance symbol format:  BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, etc.
Coinbase symbol format: BTC-USD, ETH-USD, SOL-USD, etc.
"""

import os
import time
import sqlite3
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone


# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

BINANCE_BASE    = "https://api.binance.us/api/v3"    # Binance US spot (works for US users)
BINANCE_INTL    = "https://api.binance.com/api/v3"   # Binance Global spot (fallback)
BINANCE_FUTURES = "https://fapi.binance.com/fapi/v1" # Binance Futures (USDT-M perpetuals)
COINBASE_BASE = "https://api.exchange.coinbase.com"

BINANCE_LIMIT  = 1000   # max bars per Binance request
COINBASE_LIMIT = 300    # max bars per Coinbase request
RATE_LIMIT     = 0.25   # seconds between requests

DB_FILE = os.path.join("data", "crypto", "crypto_ohlcv.db")

# Interval in milliseconds -- used for pagination
INTERVAL_MS = {
    "1m":  60_000,
    "3m":  180_000,
    "5m":  300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h":  3_600_000,
    "2h":  7_200_000,
    "4h":  14_400_000,
    "6h":  21_600_000,
    "8h":  28_800_000,
    "12h": 43_200_000,
    "1d":  86_400_000,
    "3d":  259_200_000,
    "1w":  604_800_000,
}


# ─────────────────────────────────────────────
# SQLite Cache
# ─────────────────────────────────────────────

class OHLCVCache:
    def __init__(self, db_file: str = DB_FILE):
        self.db_file = db_file
        os.makedirs(os.path.dirname(db_file) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bars (
                    symbol    TEXT NOT NULL,
                    source    TEXT NOT NULL,
                    interval  TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open      REAL,
                    high      REAL,
                    low       REAL,
                    close     REAL,
                    volume    REAL,
                    PRIMARY KEY (symbol, source, interval, timestamp)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bars
                ON bars(symbol, source, interval, timestamp)
            """)

    def _conn(self):
        return sqlite3.connect(self.db_file)

    def get_cached_range(self, symbol: str, source: str, interval: str):
        with self._conn() as conn:
            row = conn.execute("""
                SELECT MIN(timestamp), MAX(timestamp) FROM bars
                WHERE symbol=? AND source=? AND interval=?
            """, (symbol, source, interval)).fetchone()
        if row and row[0]:
            return pd.Timestamp(row[0], tz="UTC"), pd.Timestamp(row[1], tz="UTC")
        return None, None

    def save_bars(self, symbol: str, source: str, interval: str, df: pd.DataFrame):
        if df.empty:
            return
        rows = [
            (symbol, source, interval, str(idx),
             float(row["open"]), float(row["high"]),
             float(row["low"]),  float(row["close"]),
             float(row["volume"]))
            for idx, row in df.iterrows()
        ]
        with self._conn() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO bars
                (symbol, source, interval, timestamp, open, high, low, close, volume)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, rows)

    def load_bars(self, symbol: str, source: str, interval: str,
                  start: datetime, end: datetime) -> pd.DataFrame:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT timestamp, open, high, low, close, volume FROM bars
                WHERE symbol=? AND source=? AND interval=?
                  AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
            """, (symbol, source, interval, str(start), str(end))).fetchall()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=["timestamp","open","high","low","close","volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df.set_index("timestamp")


# ─────────────────────────────────────────────
# Main Fetcher
# ─────────────────────────────────────────────

class CryptoFetcher:
    """
    Fetch crypto OHLCV bars from Binance (primary) or Coinbase (fallback).
    Completely free -- no API keys required.

    Args:
        db_file:         SQLite cache path (default: crypto_ohlcv.db)
        use_binance_intl: Use global Binance instead of Binance US (default: False)
    """

    def __init__(self, db_file: str = DB_FILE, use_binance_intl: bool = False, futures_mode: bool = False):
        self.cache        = OHLCVCache(db_file)
        self.futures_mode = futures_mode
        if futures_mode:
            self.binance_base = BINANCE_FUTURES
        else:
            self.binance_base = BINANCE_INTL if use_binance_intl else BINANCE_BASE
        self._session     = requests.Session()

    def _ensure_utc(self, dt: datetime) -> datetime:
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    # ── Binance ───────────────────────────────

    def _fetch_binance_chunk(self, symbol: str, interval: str,
                             start_ms: int, end_ms: int) -> list:
        """Fetch up to BINANCE_LIMIT bars from Binance."""
        params = {
            "symbol":    symbol,
            "interval":  interval,
            "startTime": start_ms,
            "endTime":   end_ms,
            "limit":     BINANCE_LIMIT,
        }
        if self.futures_mode:
            # Futures endpoint -- no fallback needed
            try:
                resp = self._session.get(f"{self.binance_base}/klines", params=params, timeout=10)
                if resp.status_code == 200:
                    return resp.json()
            except requests.RequestException:
                pass
            return []
        else:
            # Spot: try Binance US first, fall back to global if geo-restricted
            for base in [self.binance_base, BINANCE_INTL]:
                try:
                    resp = self._session.get(f"{base}/klines", params=params, timeout=10)
                    if resp.status_code == 200:
                        return resp.json()
                    elif resp.status_code == 451:
                        continue
                except requests.RequestException:
                    continue
            return []

    def _fetch_binance_range(self, symbol: str, interval: str,
                             start: datetime, end: datetime) -> pd.DataFrame:
        """Paginate through Binance to fetch full date range."""
        interval_ms = INTERVAL_MS.get(interval)
        if not interval_ms:
            raise ValueError(f"Unknown interval '{interval}'. Valid: {list(INTERVAL_MS.keys())}")

        start_ms = int(start.timestamp() * 1000)
        end_ms   = int(end.timestamp()   * 1000)
        all_bars = []
        current  = start_ms
        page     = 1

        print(f"  Fetching {symbol} {interval} from Binance: {start.date()} -> {end.date()}")

        while current < end_ms:
            chunk_end = min(current + BINANCE_LIMIT * interval_ms, end_ms)
            print(f"    Page {page}: {datetime.fromtimestamp(current/1000, tz=timezone.utc).date()} ...")

            bars = self._fetch_binance_chunk(symbol, interval, current, chunk_end)
            if not bars:
                print("    No data returned, stopping.")
                break

            all_bars.extend(bars)
            last_open_ms = bars[-1][0]
            current = last_open_ms + interval_ms
            page += 1
            time.sleep(RATE_LIMIT)

        if not all_bars:
            return pd.DataFrame()

        return self._binance_to_df(all_bars)

    def _binance_to_df(self, bars: list) -> pd.DataFrame:
        df = pd.DataFrame(bars, columns=[
            "open_time","open","high","low","close","volume",
            "close_time","quote_volume","trades",
            "taker_buy_base","taker_buy_quote","ignore"
        ])
        df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.set_index("timestamp")
        for col in ["open","high","low","close","volume"]:
            df[col] = df[col].astype(float)
        return df[["open","high","low","close","volume"]].sort_index()

    # ── Coinbase ──────────────────────────────

    def _fetch_coinbase_chunk(self, symbol: str, granularity: int,
                              start: datetime, end: datetime) -> list:
        """Fetch up to COINBASE_LIMIT bars from Coinbase."""
        params = {
            "granularity": granularity,
            "start":       start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end":         end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            resp = self._session.get(
                f"{COINBASE_BASE}/products/{symbol}/candles",
                params=params,
                timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"    Coinbase request failed: {e}")
            return []

    def _fetch_coinbase_range(self, symbol: str, granularity: int,
                              start: datetime, end: datetime) -> pd.DataFrame:
        """Paginate through Coinbase (300 bars max per request)."""
        all_bars = []
        window   = timedelta(seconds=granularity * COINBASE_LIMIT)
        current  = start
        page     = 1

        print(f"  Fetching {symbol} {granularity}s from Coinbase: {start.date()} -> {end.date()}")

        while current < end:
            chunk_end = min(current + window, end)
            print(f"    Page {page}: {current.date()} ...")

            bars = self._fetch_coinbase_chunk(symbol, granularity, current, chunk_end)
            if not bars:
                break

            all_bars.extend(bars)
            current = chunk_end + timedelta(seconds=granularity)
            page += 1
            time.sleep(RATE_LIMIT)

        if not all_bars:
            return pd.DataFrame()

        return self._coinbase_to_df(all_bars)

    def _coinbase_to_df(self, bars: list) -> pd.DataFrame:
        # Coinbase returns [timestamp, low, high, open, close, volume]
        df = pd.DataFrame(bars, columns=["ts","low","high","open","close","volume"])
        df["timestamp"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        df = df.set_index("timestamp").sort_index()
        for col in ["open","high","low","close","volume"]:
            df[col] = df[col].astype(float)
        return df[["open","high","low","close","volume"]]

    # ── Caching Logic ─────────────────────────

    def _get_with_cache(self, symbol: str, source: str, interval_key: str,
                        start: datetime, end: datetime, fetch_fn) -> pd.DataFrame:
        cached_start, cached_end = self.cache.get_cached_range(symbol, source, interval_key)

        if cached_start is not None:
            print(f"  Cache found: {cached_start.date()} -> {cached_end.date()}")

            # Always re-fetch the last 6 hours to ensure data is current
            freshness_cutoff = end - timedelta(hours=6)

            fully_covered = (
                cached_start <= start and
                cached_end >= end - timedelta(minutes=5)
            )
            if fully_covered:
                # Even if "fully covered", re-fetch recent bars for freshness
                if cached_end < freshness_cutoff:
                    print(f"  Cache stale — re-fetching from {cached_end.date()}")
                else:
                    print("  Fully cached -- loading from DB...")
                    return self.cache.load_bars(symbol, source, interval_key, start, end)

            # Fetch older gap
            if cached_start > start:
                print(f"  Fetching older gap: {start.date()} -> {cached_start.date()}")
                old_df = fetch_fn(start, cached_start - timedelta(minutes=1))
                if not old_df.empty:
                    self.cache.save_bars(symbol, source, interval_key, old_df)
                    print(f"  Saved {len(old_df):,} older bars.")

            # Fetch newer gap (always fetch from at least freshness_cutoff)
            refetch_from = min(cached_end + timedelta(minutes=1), freshness_cutoff)
            if refetch_from < end:
                print(f"  Fetching newer data: {refetch_from.date()} -> {end.date()}")
                new_df = fetch_fn(refetch_from, end)
                if not new_df.empty:
                    self.cache.save_bars(symbol, source, interval_key, new_df)
                    print(f"  Saved {len(new_df):,} newer bars.")
        else:
            print(f"  No cache -- fetching full range...")
            df = fetch_fn(start, end)
            if not df.empty:
                self.cache.save_bars(symbol, source, interval_key, df)
                print(f"  Saved {len(df):,} bars to cache.")
            else:
                print("  No data returned.")
                return pd.DataFrame()

        return self.cache.load_bars(symbol, source, interval_key, start, end)

    # ── Public API ────────────────────────────

    def get_bars(self, symbol: str, interval: str = "5m",
                 days_back: int = 365, use_cache: bool = True,
                 end: datetime = None, start: datetime = None,
                 warmup_days: int = 0) -> pd.DataFrame:
        """
        Fetch OHLCV bars from Binance. No API key required.

        Args:
            symbol:    Binance symbol (e.g. "BTCUSDT", "ETHUSDT", "SOLUSDT")
            interval:  Bar size: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d, 1w
            days_back: Calendar days of history (used if start is not specified)
            use_cache: Use SQLite cache
            end:       End datetime (default: now)
            start:     Start datetime (overrides days_back if specified)
            warmup_days: Extra days to prepend for indicator warmup (default: 0)

        Returns:
            pd.DataFrame with columns: open, high, low, close, volume
        """
        symbol = symbol.upper()
        end    = self._ensure_utc(end or datetime.now(timezone.utc))
        if start is not None:
            start = self._ensure_utc(start) - timedelta(days=warmup_days)
            total_days = (end - start).days
        else:
            total_days = days_back + warmup_days
            start = end - timedelta(days=total_days)

        cache_source = "binance_futures" if self.futures_mode else "binance"
        mode_label   = "Binance Futures (fapi)" if self.futures_mode else "Binance"
        if warmup_days > 0:
            print(f"\nFetching {symbol} {interval} ({days_back}+{warmup_days} warmup = {total_days} days) from {mode_label}")
        else:
            print(f"\nFetching {symbol} {interval} ({days_back} days) from {mode_label}")
        if self.futures_mode:
            print(f"  Endpoint: {self.binance_base}/klines")

        if not use_cache:
            return self._fetch_binance_range(symbol, interval, start, end)

        return self._get_with_cache(
            symbol, cache_source, interval, start, end,
            fetch_fn=lambda s, e: self._fetch_binance_range(symbol, interval, s, e)
        )

    def get_bars_coinbase(self, symbol: str, interval: str = "300",
                          days_back: int = 365, use_cache: bool = True,
                          end: datetime = None) -> pd.DataFrame:
        """
        Fetch OHLCV bars from Coinbase. No API key required.

        Args:
            symbol:    Coinbase product ID (e.g. "BTC-USD", "ETH-USD", "SOL-USD")
            interval:  Granularity in seconds: "60", "300", "900", "3600", "21600", "86400"
            days_back: Calendar days of history
            use_cache: Use SQLite cache
            end:       End datetime (default: now)

        Returns:
            pd.DataFrame with columns: open, high, low, close, volume
        """
        end         = self._ensure_utc(end or datetime.now(timezone.utc))
        start       = end - timedelta(days=days_back)
        granularity = int(interval)

        print(f"\nFetching {symbol} {interval}s ({days_back} days) from Coinbase")

        if not use_cache:
            return self._fetch_coinbase_range(symbol, granularity, start, end)

        return self._get_with_cache(
            symbol, "coinbase", interval, start, end,
            fetch_fn=lambda s, e: self._fetch_coinbase_range(symbol, granularity, s, e)
        )

    def get_multiple(self, symbols: list, interval: str = "5m",
                     days_back: int = 365, source: str = "binance") -> dict:
        """
        Fetch bars for multiple symbols.
        Returns dict of {symbol: DataFrame}.

        Args:
            symbols:   List of symbols (Binance or Coinbase format depending on source)
            interval:  Bar interval
            days_back: Days of history
            source:    "binance" or "coinbase"

        Example:
            dfs = fetcher.get_multiple(["BTCUSDT","ETHUSDT","SOLUSDT"], interval="5m")
            dfs = fetcher.get_multiple(["BTC-USD","ETH-USD"], interval="3600", source="coinbase")
        """
        results = {}
        fn = self.get_bars if source == "binance" else self.get_bars_coinbase

        for symbol in symbols:
            try:
                results[symbol] = fn(symbol, interval=interval, days_back=days_back)
                print(f"  Done {symbol}: {len(results[symbol]):,} bars")
            except Exception as e:
                print(f"  ERROR {symbol}: {e}")
                results[symbol] = pd.DataFrame()
            time.sleep(RATE_LIMIT)

        return results

    def export_backtester_csv(self, symbol: str, interval: str, days_back: int = 365,
                              source: str = "binance", filepath: str = None,
                              warmup_bars: int = 0) -> str:
        """
        Export OHLCV data in backtester format:
          timestamp,open,high,low,close,volume
          1706140800000,42150.5,42380.0,42050.2,42275.8,1234.56

        Timestamp is Unix milliseconds (integer).
        No index column, no timezone info -- clean for Rust consumption.

        When warmup_bars > 0, extra bars are prepended so the backtester
        can compute indicators before the trading window starts.
        The returned file includes a comment header with the warmup count.

        Args:
            symbol:    Symbol to export
            interval:  Bar interval
            days_back: Days of history (trading window)
            source:    "binance" or "coinbase"
            filepath:  Output path (default: SYMBOL_INTERVAL_DAYSd_bt.csv)
            warmup_bars: Extra bars to prepend for indicator warmup (default: 0)

        Returns:
            Path to the exported file
        """
        # Calculate warmup_days from warmup_bars + interval
        warmup_days = 0
        if warmup_bars > 0:
            interval_ms = INTERVAL_MS.get(interval, 300_000)
            warmup_ms = warmup_bars * interval_ms
            warmup_days = max(1, int(warmup_ms / 86_400_000) + 1)  # +1 for safety
            print(f"  Warmup: {warmup_bars} bars = {warmup_days} extra days")

        fn = self.get_bars if source == "binance" else self.get_bars_coinbase
        if source == "binance":
            df = fn(symbol, interval=interval, days_back=days_back, warmup_days=warmup_days)
        else:
            # Coinbase doesn't have warmup_days param yet — just fetch more days
            df = fn(symbol, interval=interval, days_back=days_back + warmup_days)

        if df.empty:
            print("No data to export.")
            return ""

        # Convert ISO timestamp index -> Unix milliseconds integer
        bt = df.copy().reset_index()
        bt["timestamp"] = pd.to_datetime(bt["timestamp"], utc=True)
        bt["timestamp"] = bt["timestamp"].apply(lambda x: int(x.timestamp() * 1000)).astype("int64")

        # Round OHLCV to reasonable precision
        for col in ["open", "high", "low", "close"]:
            bt[col] = bt[col].round(8)
        bt["volume"] = bt["volume"].round(8)

        if filepath is None:
            filepath = os.path.join("data", "crypto", f"{symbol}_{interval}_{days_back}d_bt.csv")

        bt[["timestamp", "open", "high", "low", "close", "volume"]].to_csv(
            filepath, index=False
        )
        total_bars = len(bt)
        actual_warmup = min(warmup_bars, total_bars)
        trading_bars = total_bars - actual_warmup
        print(f"Exported {total_bars:,} bars -> {filepath}")
        if warmup_bars > 0:
            print(f"  Warmup: first {actual_warmup} bars | Trading window: {trading_bars} bars")
            print(f"  Pass warmup_bars={actual_warmup} to BacktestEngine to skip warmup period")
        print(f"Format: timestamp (Unix ms), open, high, low, close, volume")
        print(f"Sample row: {bt.iloc[0].to_dict()}")
        return filepath

    def export_csv(self, symbol: str, interval: str, days_back: int = 365,
                   source: str = "binance", filepath: str = None) -> str:
        """Export with ISO timestamps (human-readable format)."""
        fn = self.get_bars if source == "binance" else self.get_bars_coinbase
        df = fn(symbol, interval=interval, days_back=days_back)
        if filepath is None:
            filepath = os.path.join("data", "crypto", f"{symbol}_{interval}_{days_back}d.csv")
        df.to_csv(filepath)
        print(f"Exported {len(df):,} bars -> {filepath}")
        return filepath

    def export_parquet(self, symbol: str, interval: str, days_back: int = 365,
                       source: str = "binance", filepath: str = None) -> str:
        """Export to Parquet format (efficient storage, preserves types)."""
        fn = self.get_bars if source == "binance" else self.get_bars_coinbase
        df = fn(symbol, interval=interval, days_back=days_back)
        if filepath is None:
            filepath = os.path.join("data", "crypto", f"{symbol}_{interval}_{days_back}d.parquet")
        df.to_parquet(filepath)
        print(f"Exported {len(df):,} bars -> {filepath}")
        return filepath

    def list_binance_symbols(self) -> list:
        """Return all available trading pairs (spot or futures depending on mode)."""
        try:
            resp = self._session.get(f"{self.binance_base}/exchangeInfo", timeout=10)
            data = resp.json()
            return sorted([s["symbol"] for s in data.get("symbols", [])
                           if s.get("status") == "TRADING"])
        except Exception as e:
            print(f"Could not fetch symbols: {e}")
            return []


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # CLI usage:
    #   python crypto_fetcher.py [symbol] [interval] [days] [source] [command]
    #
    # source options:
    #   binance         -- Binance spot (default)
    #   futures         -- Binance USDT-M futures (fapi)
    #   coinbase        -- Coinbase
    #
    # Commands:
    #   (none)   -- fetch and display summary
    #   export   -- export backtester CSV (Unix ms timestamps)
    #   csv      -- export human-readable CSV (ISO timestamps)
    #
    # Examples:
    #   python crypto_fetcher.py BTCUSDT 5m 365
    #   python crypto_fetcher.py BTCUSDT 5m 365 binance export
    #   python crypto_fetcher.py BTCUSDT 5m 365 futures export
    #   python crypto_fetcher.py ETHUSDT 1m 365 futures export
    #   python crypto_fetcher.py BTC-USD 300 365 coinbase export

    symbol   = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    interval = sys.argv[2] if len(sys.argv) > 2 else "5m"
    days     = int(sys.argv[3]) if len(sys.argv) > 3 else 365
    source   = sys.argv[4] if len(sys.argv) > 4 else "binance"
    command  = sys.argv[5] if len(sys.argv) > 5 else ""

    futures_mode = (source == "futures")
    fetcher = CryptoFetcher(futures_mode=futures_mode)

    if command == "export":
        # Write with the exact filename Rust expects: SYMBOL_INTERVAL_DAYSd_bt.csv
        # Use the CLI 'days' arg (which includes warmup) as the filename days
        data_dir = os.path.join("data", "crypto")
        os.makedirs(data_dir, exist_ok=True)
        filepath = os.path.join(data_dir, f"{symbol}_{interval}_{days}d_bt.csv")
        fetcher.export_backtester_csv(symbol, interval=interval, days_back=days,
                                      source="binance" if futures_mode else source,
                                      filepath=filepath)

    elif command == "csv":
        fetcher.export_csv(symbol, interval=interval, days_back=days,
                           source="binance" if futures_mode else source)

    else:
        if source == "coinbase":
            df = fetcher.get_bars_coinbase(symbol, interval=interval, days_back=days)
        else:
            df = fetcher.get_bars(symbol, interval=interval, days_back=days)

        if df.empty:
            print("No data returned.")
        else:
            print(f"\nResult: {len(df):,} bars")
            print(f"Range:  {df.index[0]} -> {df.index[-1]}")
            print(df.head())
            print("...")
            print(df.tail())
