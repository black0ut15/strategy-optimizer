"""
Alpaca Stocks/ETFs OHLCV Data Fetcher
========================================
Source: Alpaca Market Data API v2 (official)
  - https://data.alpaca.markets/v2/stocks/{symbol}/bars

Features:
  - Official API -- stable, well-documented
  - Requires live Alpaca brokerage account + API keys (for SIP feed)
  - 6+ years of intraday history on live account
  - Up to 10,000 bars per request -- very fast pagination
  - SQLite caching -- only fetches missing data on subsequent calls
  - Same interface and export format as crypto_fetcher.py
  - Backtester CSV export: Unix ms timestamps, integer volume

Setup:
  pip install requests pandas

  Get keys from: https://app.alpaca.markets/account/live-api-keys
  Use LIVE keys (not paper) for SIP feed + full history

Usage:
  fetcher = AlpacaFetcher(api_key="YOUR_KEY", api_secret="YOUR_SECRET")

  # Intraday bars
  df = fetcher.get_bars("SPY",  interval="5Min",  days_back=365)
  df = fetcher.get_bars("AAPL", interval="1Min",  days_back=365)
  df = fetcher.get_bars("QQQ",  interval="15Min", days_back=365)

  # Daily bars
  df = fetcher.get_bars("SPY",  interval="1Day",  days_back=365)

  # Multiple symbols
  dfs = fetcher.get_multiple(["SPY","QQQ","AAPL","NVDA"], interval="5Min")

  # Export for Rust backtester (Unix ms timestamps)
  fetcher.export_backtester_csv("SPY", interval="5Min", days_back=365)

Alpaca timeframe format:
  "1Min", "5Min", "15Min", "1Hour", "1Day"

Notes:
  - SIP feed = consolidated tape from all 15 US exchanges (live account)
  - IEX feed = free but limited coverage (paper account)
  - Market hours only for intraday (9:30-16:00 ET)
  - Pagination via next_page_token - up to 10,000 bars/request
  - Rate limit: 200 requests/min on live account
"""

import os
import time
import sqlite3
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone


# Config
DATA_URL   = "https://data.alpaca.markets"
BARS_LIMIT = 10_000
RATE_LIMIT = 0.35
DB_FILE    = os.path.join("data", "stocks", "stocks_ohlcv.db")


# SQLite Cache
class OHLCVCache:
    def __init__(self, db_file=DB_FILE):
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bars ON bars(symbol, source, interval, timestamp)")

    def _conn(self):
        return sqlite3.connect(self.db_file)

    def get_cached_range(self, symbol, source, interval):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MIN(timestamp), MAX(timestamp) FROM bars WHERE symbol=? AND source=? AND interval=?",
                (symbol, source, interval)
            ).fetchone()
        if row and row[0]:
            return pd.Timestamp(row[0], tz="UTC"), pd.Timestamp(row[1], tz="UTC")
        return None, None

    def save_bars(self, symbol, source, interval, df):
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

    def load_bars(self, symbol, source, interval, start, end):
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


# Main Fetcher
class AlpacaFetcher:
    """
    Fetch stock/ETF OHLCV bars from Alpaca Market Data API v2.
    Use live API keys for SIP feed (full history, all exchanges).
    """

    def __init__(self, api_key, api_secret, db_file=DB_FILE, feed="iex"):
        self.feed    = feed
        self.cache   = OHLCVCache(db_file)
        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID":     api_key,
            "APCA-API-SECRET-KEY": api_secret,
        })

    def _ensure_utc(self, dt):
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    def _fetch_bars_range(self, symbol, timeframe, start, end):
        all_bars   = []
        page_token = None
        page       = 1

        print(f"  Fetching {symbol} {timeframe} from Alpaca: {start.date()} -> {end.date()}")

        while True:
            params = {
                "timeframe":  timeframe,
                "start":      start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end":        end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit":      BARS_LIMIT,
                "feed":       self.feed,
                "sort":       "asc",
            }
            if page_token:
                params["page_token"] = page_token

            try:
                resp = self.session.get(
                    f"{DATA_URL}/v2/stocks/{symbol}/bars",
                    params=params,
                    timeout=20
                )
                if resp.status_code in (403, 422):
                    print(f"    Alpaca {resp.status_code}: {resp.json()}")
                    break
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"    Request failed: {e}")
                break

            bars = data.get("bars", [])
            if bars:
                all_bars.extend(bars)
                print(f"    Page {page}: {len(bars):,} bars (total: {len(all_bars):,})", end="\r")

            page_token = data.get("next_page_token")
            if not page_token:
                break

            page += 1
            time.sleep(RATE_LIMIT)

        print()

        if not all_bars:
            return pd.DataFrame()

        df = pd.DataFrame(all_bars).rename(columns={
            "t": "timestamp", "o": "open", "h": "high",
            "l": "low",       "c": "close", "v": "volume",
        })
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp").sort_index()
        for col in ["open","high","low","close","volume"]:
            if col in df.columns:
                df[col] = df[col].astype(float)
        return df[["open","high","low","close","volume"]]

    def _get_with_cache(self, symbol, timeframe, start, end):
        source = f"alpaca_{self.feed}"
        cached_start, cached_end = self.cache.get_cached_range(symbol, source, timeframe)

        if cached_start is not None:
            print(f"  Cache found: {cached_start.date()} -> {cached_end.date()}")
            buffer = timedelta(minutes=10)

            if cached_start <= start and cached_end >= end - buffer:
                print("  Fully cached -- loading from DB...")
                return self.cache.load_bars(symbol, source, timeframe, start, end)

            if cached_start > start + timedelta(hours=1):
                print(f"  Fetching older gap: {start.date()} -> {cached_start.date()}")
                old_df = self._fetch_bars_range(symbol, timeframe, start, cached_start - timedelta(minutes=1))
                if not old_df.empty:
                    self.cache.save_bars(symbol, source, timeframe, old_df)
                    print(f"  Saved {len(old_df):,} older bars.")

            if cached_end < end - buffer:
                fetch_from = cached_end + timedelta(minutes=1)
                print(f"  Fetching newer gap: {fetch_from.date()} -> {end.date()}")
                new_df = self._fetch_bars_range(symbol, timeframe, fetch_from, end)
                if not new_df.empty:
                    self.cache.save_bars(symbol, source, timeframe, new_df)
                    print(f"  Saved {len(new_df):,} newer bars.")
        else:
            print("  No cache -- fetching full range...")
            df = self._fetch_bars_range(symbol, timeframe, start, end)
            if not df.empty:
                self.cache.save_bars(symbol, source, timeframe, df)
                print(f"  Saved {len(df):,} bars to cache.")
            else:
                print("  No data returned.")
                return pd.DataFrame()

        return self.cache.load_bars(symbol, source, timeframe, start, end)

    def get_bars(self, symbol, interval="5Min", days_back=365, use_cache=True, end=None):
        """
        Fetch OHLCV bars from Alpaca.

        Args:
            symbol:    Ticker (e.g. "SPY", "AAPL", "QQQ")
            interval:  "1Min", "5Min", "15Min", "1Hour", "1Day"
            days_back: Calendar days of history
            use_cache: Use SQLite cache
            end:       End datetime (default: now UTC)
        """
        symbol = symbol.upper()
        end    = self._ensure_utc(end or datetime.now(timezone.utc))
        start  = end - timedelta(days=days_back)

        print(f"\nFetching {symbol} {interval} ({days_back} days) from Alpaca")

        if not use_cache:
            return self._fetch_bars_range(symbol, interval, start, end)
        return self._get_with_cache(symbol, interval, start, end)

    def get_multiple(self, symbols, interval="5Min", days_back=365):
        """Fetch bars for multiple symbols. Returns {symbol: DataFrame}."""
        results = {}
        for symbol in symbols:
            try:
                results[symbol] = self.get_bars(symbol, interval=interval, days_back=days_back)
                print(f"  Done {symbol}: {len(results[symbol]):,} bars")
            except Exception as e:
                print(f"  ERROR {symbol}: {e}")
                results[symbol] = pd.DataFrame()
        return results

    def export_backtester_csv(self, symbol, interval="5Min", days_back=365, filepath=None):
        """
        Export in backtester format:
          timestamp,open,high,low,close,volume
          1706140800000,479.21,479.85,479.01,479.72,1234567

        Timestamp = Unix milliseconds (integer).
        """
        df = self.get_bars(symbol, interval=interval, days_back=days_back)
        if df.empty:
            print("No data to export.")
            return ""

        bt = df.copy().reset_index()
        bt["timestamp"] = pd.to_datetime(bt["timestamp"], utc=True)
        bt["timestamp"] = bt["timestamp"].apply(lambda x: int(x.timestamp() * 1000))
        bt["timestamp"] = bt["timestamp"].astype(int)
        for col in ["open","high","low","close"]:
            bt[col] = bt[col].round(4)
        bt["volume"] = bt["volume"].round(0).astype("int64")

        if filepath is None:
            filepath = os.path.join("data", "stocks", f"{symbol}_{interval}_{days_back}d_bt.csv")

        bt[["timestamp","open","high","low","close","volume"]].to_csv(filepath, index=False)
        print(f"Exported {len(bt):,} bars -> {filepath}")
        print(f"Sample: {bt.iloc[0].to_dict()}")
        return filepath

    def export_csv(self, symbol, interval="5Min", days_back=365, filepath=None):
        """Export with ISO timestamps (human-readable)."""
        df = self.get_bars(symbol, interval=interval, days_back=days_back)
        if filepath is None:
            filepath = os.path.join("data", "stocks", f"{symbol}_{interval}_{days_back}d.csv")
        df.to_csv(filepath)
        print(f"Exported {len(df):,} bars -> {filepath}")
        return filepath


# CLI
if __name__ == "__main__":
    import sys, os

    api_key    = os.environ.get("ALPACA_KEY", "")
    api_secret = os.environ.get("ALPACA_SECRET", "")

    if not api_key or not api_secret:
        print("ERROR: Set credentials:")
        print("  set ALPACA_KEY=your_key_id")
        print("  set ALPACA_SECRET=your_secret_key")
        sys.exit(1)

    fetcher = AlpacaFetcher(api_key=api_key, api_secret=api_secret)

    # Usage: python alpaca_fetcher.py [symbol] [interval] [days] [command]
    # Commands: (none) | export | csv
    # Examples:
    #   python alpaca_fetcher.py SPY 5Min 365
    #   python alpaca_fetcher.py SPY 5Min 365 export
    #   python alpaca_fetcher.py AAPL 1Min 365 export
    #   python alpaca_fetcher.py QQQ 1Day 730 export

    symbol   = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    interval = sys.argv[2] if len(sys.argv) > 2 else "5Min"
    days     = int(sys.argv[3]) if len(sys.argv) > 3 else 365
    command  = sys.argv[4] if len(sys.argv) > 4 else ""

    if command == "export":
        fetcher.export_backtester_csv(symbol, interval=interval, days_back=days)
    elif command == "csv":
        fetcher.export_csv(symbol, interval=interval, days_back=days)
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
