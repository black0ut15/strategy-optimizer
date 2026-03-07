"""Data Manager — handles data loading with automatic warmup buffer.

TradingView loads extra historical bars before the visible chart range
so that indicators (especially MESA MAMA/FAMA) are already converged
before the first bar. This module replicates that behavior:

  1. User requests N days of data at interval X
  2. We calculate the warmup buffer needed for that interval
  3. Fetch (N + buffer) days of data
  4. Return full dataset + warmup_bars count
  5. BacktestEngine skips warmup_bars for trading, but computes indicators over all bars

Usage:
    from data_manager import DataManager

    dm = DataManager()

    # Returns (Bars, warmup_bars) — pass both to BacktestEngine
    bars, warmup = dm.load_csv("BTCUSDT_5m_365d_bt.csv", interval="5m")

    # Or fetch fresh data with auto-warmup
    bars, warmup = dm.fetch_crypto("BTCUSDT", interval="5m", days_back=365)
    bars, warmup = dm.fetch_stocks("SPY", interval="5Min", days_back=365)
    bars, warmup = dm.fetch_futures("ES", interval="5m", days_back=365)

    # Run backtest with proper warmup
    result = BacktestEngine.run_detail(strategy, bars, params,
                                       warmup_bars=warmup)
"""

import os
import sys
import numpy as np
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Tuple, Optional

# Add parent paths for imports
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fetchers"))

from backtest_engine import Bars


# ─────────────────────────────────────────────
# Warmup Configuration
# ─────────────────────────────────────────────

# TradingView loads approximately 20,000 bars for chart warmup on most timeframes.
# The MESA Hilbert transform needs the most warmup (~500 bars minimum),
# but we match TV's full buffer to get identical indicator values.
#
# TV warmup = ~20,000 bars regardless of timeframe.
# We convert that to calendar days for each interval.

BARS_PER_DAY = {
    # Crypto (24/7 market)
    "1m":  1440,
    "3m":  480,
    "5m":  288,
    "15m": 96,
    "30m": 48,
    "1h":  24,
    "2h":  12,
    "4h":  6,
    "6h":  4,
    "1d":  1,
    # Stocks (6.5h trading day)
    "1Min":  390,
    "5Min":  78,
    "15Min": 26,
    "1Hour": 7,   # ~6.5 per day
    "1Day":  1,
}

# Target warmup: 20,000 bars (matches TradingView default)
DEFAULT_WARMUP_BARS = 20_000

# Minimum warmup: 500 bars (enough for MESA to converge)
MIN_WARMUP_BARS = 500


def calc_warmup(interval: str, warmup_bars: int = DEFAULT_WARMUP_BARS) -> Tuple[int, int]:
    """Calculate warmup buffer for a given interval.

    Args:
        interval: Bar interval string (e.g. "5m", "1h", "5Min")
        warmup_bars: Number of warmup bars desired (default: 20,000)

    Returns:
        (warmup_days, warmup_bars): Extra days to fetch, and bar count for engine
    """
    bars_day = BARS_PER_DAY.get(interval)
    if bars_day is None:
        # Unknown interval — use minimum warmup
        print(f"  Warning: unknown interval '{interval}', using minimum warmup")
        return 7, MIN_WARMUP_BARS

    warmup_bars = max(warmup_bars, MIN_WARMUP_BARS)
    warmup_days = int(np.ceil(warmup_bars / bars_day))

    # Cap at reasonable limits
    warmup_days = min(warmup_days, 365)  # Never more than 1 year extra
    actual_bars = warmup_days * bars_day

    return warmup_days, actual_bars


# ─────────────────────────────────────────────
# Data Manager
# ─────────────────────────────────────────────

class DataManager:
    """Unified data loading with automatic warmup buffer.

    Handles three data sources:
      - CSV files (pre-downloaded data)
      - Crypto fetcher (Binance/Coinbase)
      - Stock fetcher (Alpaca)
      - Futures fetcher (InsightSentry/RapidAPI)

    All methods return (Bars, warmup_bars) tuples ready for BacktestEngine.
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir

    # ── CSV Loading ──────────────────────────

    def load_csv(
        self,
        filepath: str,
        interval: str = "5m",
        warmup_bars: int = 0,
    ) -> Tuple[Bars, int]:
        """Load bars from CSV file.

        If warmup_bars > 0, uses that value directly.
        If warmup_bars == 0, no warmup is applied.
        Use warmup_bars=-1 to auto-calculate based on interval.

        Args:
            filepath: Path to CSV with columns: timestamp,open,high,low,close,volume
            interval: Bar interval (used for auto warmup calculation)
            warmup_bars: Warmup bar count. 0=none, -1=auto, >0=explicit.

        Returns:
            (Bars, warmup_bars) tuple
        """
        bars = Bars.from_csv(filepath)

        if warmup_bars == -1:
            # Auto: can't fetch more data for a CSV, so estimate from data length
            _, ideal_warmup = calc_warmup(interval)
            # Use up to 20% of data as warmup, capped at ideal
            warmup_bars = min(ideal_warmup, len(bars) // 5)
            print(f"  Auto warmup for {interval}: {warmup_bars} bars "
                  f"({warmup_bars / BARS_PER_DAY.get(interval, 288):.0f} days)")
        elif warmup_bars < 0:
            warmup_bars = 0

        return bars, warmup_bars

    # ── Crypto Fetching ──────────────────────

    def fetch_crypto(
        self,
        symbol: str,
        interval: str = "5m",
        days_back: int = 365,
        source: str = "binance",
        futures_mode: bool = False,
        warmup_bars: int = DEFAULT_WARMUP_BARS,
        end: Optional[datetime] = None,
        export_path: Optional[str] = None,
    ) -> Tuple[Bars, int]:
        """Fetch crypto data with automatic warmup buffer.

        Fetches (days_back + warmup_days) of data so the backtester
        has enough history for indicator convergence before trading begins.

        Args:
            symbol: Binance symbol (e.g. "BTCUSDT")
            interval: Bar interval (e.g. "5m", "1h")
            days_back: Trading period in days
            source: "binance" or "coinbase"
            futures_mode: Use Binance Futures endpoint
            warmup_bars: Warmup buffer size (default: 20,000)
            end: End datetime (default: now)
            export_path: If set, also save CSV to this path

        Returns:
            (Bars, warmup_bars) — Bars includes warmup period,
            warmup_bars is the count to pass to BacktestEngine
        """
        try:
            from crypto_fetcher import CryptoFetcher
        except ImportError:
            fetcher_dir = os.path.join(os.path.dirname(__file__), "..", "fetchers")
            sys.path.insert(0, fetcher_dir)
            from crypto_fetcher import CryptoFetcher

        warmup_days, actual_warmup_bars = calc_warmup(interval, warmup_bars)
        total_days = days_back + warmup_days

        print(f"\n{'='*60}")
        print(f"  DataManager: Fetching {symbol} {interval}")
        print(f"  Trading period: {days_back} days")
        print(f"  Warmup buffer:  {warmup_days} days ({actual_warmup_bars} bars)")
        print(f"  Total fetch:    {total_days} days")
        print(f"{'='*60}")

        fetcher = CryptoFetcher(futures_mode=futures_mode)

        if source == "coinbase":
            # Convert interval to Coinbase granularity
            coinbase_map = {"1m": "60", "5m": "300", "15m": "900",
                           "1h": "3600", "6h": "21600", "1d": "86400"}
            granularity = coinbase_map.get(interval, "300")
            df = fetcher.get_bars_coinbase(symbol, interval=granularity,
                                            days_back=total_days, end=end)
        else:
            df = fetcher.get_bars(symbol, interval=interval,
                                  days_back=total_days, end=end)

        if df.empty:
            raise RuntimeError(f"No data returned for {symbol}")

        # Convert to backtester format
        csv_path = export_path or self._temp_csv_path(symbol, interval, total_days)
        self._df_to_csv(df, csv_path)

        bars = Bars.from_csv(csv_path)

        # The actual warmup bars may differ from requested if data is shorter
        bars_per_day = BARS_PER_DAY.get(interval, 288)
        actual_warmup = min(actual_warmup_bars, len(bars) - bars_per_day * days_back)
        actual_warmup = max(0, actual_warmup)

        print(f"\n  Loaded: {len(bars)} bars total")
        print(f"  Warmup: first {actual_warmup} bars (indicators only, no trading)")
        print(f"  Trading: bars {actual_warmup}–{len(bars)} ({len(bars)-actual_warmup} bars)")

        return bars, actual_warmup

    # ── Stock Fetching ───────────────────────

    def fetch_stocks(
        self,
        symbol: str,
        interval: str = "5Min",
        days_back: int = 365,
        warmup_bars: int = DEFAULT_WARMUP_BARS,
        end: Optional[datetime] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        export_path: Optional[str] = None,
    ) -> Tuple[Bars, int]:
        """Fetch stock data from Alpaca with automatic warmup buffer.

        Args:
            symbol: Ticker (e.g. "SPY", "AAPL")
            interval: "1Min", "5Min", "15Min", "1Hour", "1Day"
            days_back: Trading period in days
            warmup_bars: Warmup buffer size
            end: End datetime
            api_key: Alpaca API key (or ALPACA_KEY env var)
            api_secret: Alpaca API secret (or ALPACA_SECRET env var)
            export_path: Optional CSV export path

        Returns:
            (Bars, warmup_bars)
        """
        try:
            from alpaca_fetcher import AlpacaFetcher
        except ImportError:
            fetcher_dir = os.path.join(os.path.dirname(__file__), "..", "fetchers")
            sys.path.insert(0, fetcher_dir)
            from alpaca_fetcher import AlpacaFetcher

        warmup_days, actual_warmup_bars = calc_warmup(interval, warmup_bars)
        total_days = days_back + warmup_days

        print(f"\n{'='*60}")
        print(f"  DataManager: Fetching {symbol} {interval}")
        print(f"  Trading period: {days_back} days")
        print(f"  Warmup buffer:  {warmup_days} days ({actual_warmup_bars} bars)")
        print(f"  Total fetch:    {total_days} days")
        print(f"{'='*60}")

        key = api_key or os.environ.get("ALPACA_KEY", "")
        secret = api_secret or os.environ.get("ALPACA_SECRET", "")
        if not key or not secret:
            raise RuntimeError("Alpaca API key/secret required. "
                             "Set ALPACA_KEY and ALPACA_SECRET env vars.")

        fetcher = AlpacaFetcher(api_key=key, api_secret=secret)
        df = fetcher.get_bars(symbol, interval=interval,
                              days_back=total_days, end=end)

        if df.empty:
            raise RuntimeError(f"No data returned for {symbol}")

        csv_path = export_path or self._temp_csv_path(symbol, interval, total_days)
        self._df_to_csv(df, csv_path)
        bars = Bars.from_csv(csv_path)

        bars_per_day = BARS_PER_DAY.get(interval, 78)
        actual_warmup = min(actual_warmup_bars, len(bars) - bars_per_day * days_back)
        actual_warmup = max(0, actual_warmup)

        print(f"\n  Loaded: {len(bars)} bars total")
        print(f"  Warmup: first {actual_warmup} bars")
        print(f"  Trading: bars {actual_warmup}–{len(bars)} ({len(bars)-actual_warmup} bars)")

        return bars, actual_warmup

    # ── Futures Fetching ─────────────────────

    def fetch_futures(
        self,
        symbol: str,
        interval: str = "5m",
        days_back: int = 365,
        warmup_bars: int = DEFAULT_WARMUP_BARS,
        api_key: Optional[str] = None,
        export_path: Optional[str] = None,
    ) -> Tuple[Bars, int]:
        """Fetch futures data from InsightSentry (via RapidAPI) with warmup buffer.

        Args:
            symbol: Futures symbol (e.g. "ES", "NQ", "GC", "CL")
            interval: Bar interval (e.g. "5m")
            days_back: Trading period in days
            warmup_bars: Warmup buffer size
            api_key: RapidAPI key (or RAPIDAPI_KEY env var)
            export_path: Optional CSV export path

        Returns:
            (Bars, warmup_bars)
        """
        try:
            from futures_fetcher import fetch_all_bars, export_csv
        except ImportError:
            fetcher_dir = os.path.join(os.path.dirname(__file__), "..", "fetchers")
            sys.path.insert(0, fetcher_dir)
            from futures_fetcher import fetch_all_bars, export_csv

        warmup_days, actual_warmup_bars = calc_warmup(interval, warmup_bars)
        total_days = days_back + warmup_days

        print(f"\n{'='*60}")
        print(f"  DataManager: Fetching {symbol} futures {interval}")
        print(f"  Trading period: {days_back} days")
        print(f"  Warmup buffer:  {warmup_days} days ({actual_warmup_bars} bars)")
        print(f"  Total fetch:    {total_days} days")
        print(f"{'='*60}")

        key = api_key or os.environ.get("RAPIDAPI_KEY", "")
        if not key:
            raise RuntimeError("RapidAPI key required. Set RAPIDAPI_KEY env var.")

        # futures_fetcher uses exchange/product format
        exchange_map = {
            "ES": ("CME", "ES"), "NQ": ("CME", "NQ"), "YM": ("CME", "YM"),
            "RTY": ("CME", "RTY"), "MES": ("CME", "MES"), "MNQ": ("CME", "MNQ"),
            "CL": ("NYMEX", "CL"), "NG": ("NYMEX", "NG"),
            "GC": ("COMEX", "GC"), "SI": ("COMEX", "SI"),
            "ZB": ("CME", "ZB"), "ZN": ("CME", "ZN"),
            "ZC": ("CME", "ZC"), "ZS": ("CME", "ZS"), "ZW": ("CME", "ZW"),
        }
        exchange, product = exchange_map.get(symbol.upper(), ("CME", symbol.upper()))

        # Convert interval to futures_fetcher format
        interval_map = {"5m": "5min", "15m": "15min", "30m": "30min",
                        "1h": "1hour", "1d": "1day"}
        bar_interval = interval_map.get(interval, interval)

        rows = fetch_all_bars(key, exchange, product, bar_interval, total_days)
        if not rows:
            raise RuntimeError(f"No data returned for {symbol} futures")

        csv_path = export_path or self._temp_csv_path(f"{symbol}_fut", interval, total_days)
        export_csv(rows, symbol, bar_interval, total_days,
                   output_dir=os.path.dirname(csv_path))

        bars = Bars.from_csv(csv_path)

        bars_per_day = BARS_PER_DAY.get(interval, 288)
        actual_warmup = min(actual_warmup_bars, len(bars) - bars_per_day * days_back)
        actual_warmup = max(0, actual_warmup)

        print(f"\n  Loaded: {len(bars)} bars total")
        print(f"  Warmup: first {actual_warmup} bars")
        print(f"  Trading: bars {actual_warmup}–{len(bars)} ({len(bars)-actual_warmup} bars)")

        return bars, actual_warmup

    # ── Helpers ───────────────────────────────

    def _temp_csv_path(self, symbol: str, interval: str, days: int) -> str:
        """Generate a temporary CSV path for fetched data."""
        os.makedirs(os.path.join(self.data_dir, "temp"), exist_ok=True)
        return os.path.join(self.data_dir, "temp",
                           f"{symbol}_{interval}_{days}d_bt.csv")

    @staticmethod
    def _df_to_csv(df, filepath: str) -> None:
        """Convert pandas DataFrame to backtester CSV format."""
        import pandas as pd

        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        bt = df.copy().reset_index()
        bt["timestamp"] = pd.to_datetime(bt["timestamp"], utc=True)
        bt["timestamp"] = bt["timestamp"].apply(
            lambda x: int(x.timestamp() * 1000)
        ).astype("int64")

        for col in ["open", "high", "low", "close"]:
            bt[col] = bt[col].round(8)
        bt["volume"] = bt["volume"].round(8)

        bt[["timestamp", "open", "high", "low", "close", "volume"]].to_csv(
            filepath, index=False
        )


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    usage = """
Data Manager — fetch data with automatic warmup buffer

Usage:
  python data_manager.py <source> <symbol> <interval> <days> [options]

Sources:
  crypto    — Binance/Coinbase (free, no key)
  futures   — InsightSentry via RapidAPI
  stocks    — Alpaca
  csv       — Load from existing CSV file

Examples:
  python data_manager.py crypto BTCUSDT 5m 365
  python data_manager.py crypto BTCUSDT 5m 365 --futures
  python data_manager.py stocks SPY 5Min 365
  python data_manager.py futures ES 5m 365
  python data_manager.py csv path/to/data.csv 5m

Options:
  --futures     Use Binance Futures endpoint (crypto only)
  --warmup N    Override warmup bars (default: 20000)
  --export PATH Export CSV to specific path
  --no-warmup   Disable warmup buffer
"""

    if len(sys.argv) < 3:
        print(usage)
        sys.exit(1)

    source = sys.argv[1]
    symbol = sys.argv[2]
    interval = sys.argv[3] if len(sys.argv) > 3 else "5m"
    days = int(sys.argv[4]) if len(sys.argv) > 4 else 365

    # Parse options
    futures_mode = "--futures" in sys.argv
    no_warmup = "--no-warmup" in sys.argv
    warmup_override = DEFAULT_WARMUP_BARS
    if "--warmup" in sys.argv:
        idx = sys.argv.index("--warmup")
        warmup_override = int(sys.argv[idx + 1])
    if no_warmup:
        warmup_override = 0

    export_path = None
    if "--export" in sys.argv:
        idx = sys.argv.index("--export")
        export_path = sys.argv[idx + 1]

    dm = DataManager()

    if source == "csv":
        bars, warmup = dm.load_csv(symbol, interval=interval,
                                    warmup_bars=-1 if not no_warmup else 0)
    elif source == "crypto":
        bars, warmup = dm.fetch_crypto(symbol, interval=interval,
                                        days_back=days,
                                        futures_mode=futures_mode,
                                        warmup_bars=warmup_override,
                                        export_path=export_path)
    elif source == "stocks":
        bars, warmup = dm.fetch_stocks(symbol, interval=interval,
                                        days_back=days,
                                        warmup_bars=warmup_override,
                                        export_path=export_path)
    elif source == "futures":
        bars, warmup = dm.fetch_futures(symbol, interval=interval,
                                         days_back=days,
                                         warmup_bars=warmup_override,
                                         export_path=export_path)
    else:
        print(f"Unknown source: {source}")
        print(usage)
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Ready for backtesting:")
    print(f"  Total bars:  {len(bars)}")
    print(f"  Warmup bars: {warmup}")
    print(f"  Trading bars: {len(bars) - warmup}")
    print(f"{'='*60}")
    print(f"\n  Example usage in Python:")
    print(f"    result = BacktestEngine.run_detail(strategy, bars, params,")
    print(f"                                       warmup_bars={warmup})")
