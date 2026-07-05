#!/usr/bin/env python3
"""
Futures OHLCV Fetcher - InsightSentry (Ultra Plan)
SQLite-cached with backtester CSV export.
Matches architecture of crypto_fetcher.py and alpaca_fetcher.py

Uses /history endpoint for deep historical data (20+ years)
Uses /series endpoint for recent data (up to 30k bars)

Usage:
    python futures_fetcher.py ES 5m 365
    python futures_fetcher.py ES 5m 365 export
    python futures_fetcher.py NQ 1h 180 csv
    python futures_fetcher.py MES 5m 90

Environment:
    RAPIDAPI_KEY - Your RapidAPI key for InsightSentry
"""

import os
import sys
import time
import sqlite3
import requests
from datetime import datetime, timedelta, timezone

# ── Config ──────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "futures", "futures_ohlcv.db")
BASE_URL = "https://insightsentry.p.rapidapi.com"
RAPIDAPI_HOST = "insightsentry.p.rapidapi.com"

# Rate limiting: Ultra plan = 40 req/min -> ~1.5s between requests, use 2s for safety
RATE_LIMIT_DELAY = 2.0

# Symbol mapping: user-friendly -> (exchange_prefix, product_code)
SYMBOL_MAP = {
    # ── CME E-mini Equity Index ──────────────────────────────────
    "ES":   ("CME_MINI", "ES"),     # E-mini S&P 500 ($50/pt)
    "NQ":   ("CME_MINI", "NQ"),     # E-mini Nasdaq 100 ($20/pt)
    "YM":   ("CME_MINI", "YM"),     # E-mini Dow ($5/pt)
    "RTY":  ("CME_MINI", "RTY"),    # E-mini Russell 2000 ($50/pt)
    
    # ── CME Micro Equity Index ───────────────────────────────────
    "MES":  ("CME_MINI", "MES"),    # Micro E-mini S&P 500 ($5/pt)
    "MNQ":  ("CME_MINI", "MNQ"),    # Micro E-mini Nasdaq 100 ($2/pt)
    "MYM":  ("CME_MINI", "MYM"),    # Micro E-mini Dow ($0.50/pt)
    "M2K":  ("CME_MINI", "M2K"),    # Micro E-mini Russell 2000 ($5/pt)
    
    # ── CME Crypto ───────────────────────────────────────────────
    "BTC":  ("CME", "BTC"),         # Bitcoin Futures ($5/pt) ✓
    "MBT":  ("CME", "MBT"),         # Micro Bitcoin ($0.10/pt) ✓
    "ETH":  ("CME", "ETH"),         # Ether Futures ($50/pt) ✓
    "MET":  ("CME", "MET"),         # Micro Ether ($0.10/pt) ✓
    
    # ── CME FX (Currency) ────────────────────────────────────────
    "6E":   ("CME", "6E"),          # Euro FX ✓
    "6J":   ("CME", "6J"),          # Japanese Yen
    "6B":   ("CME", "6B"),          # British Pound
    "6A":   ("CME", "6A"),          # Australian Dollar
    "6C":   ("CME", "6C"),          # Canadian Dollar
    "6S":   ("CME", "6S"),          # Swiss Franc
    "6N":   ("CME", "6N"),          # New Zealand Dollar
    "6M":   ("CME", "6M"),          # Mexican Peso
    "MJY":  ("CME_MINI", "MJY"),    # Micro JPY/USD ✓
    "M6E":  ("CME_MINI", "M6E"),    # Micro Euro FX ✓
    
    # ── NYMEX Energy ─────────────────────────────────────────────
    "CL":   ("NYMEX", "CL"),        # Crude Oil WTI ($1000/pt)
    "NG":   ("NYMEX", "NG"),        # Natural Gas ($10,000/pt)
    "HO":   ("NYMEX", "HO"),        # Heating Oil ✓
    "RB":   ("NYMEX", "RB"),        # RBOB Gasoline ✓
    "MCL":  ("NYMEX", "MCL"),       # Micro Crude Oil ✓
    "PA":   ("NYMEX", "PA"),        # Palladium ✓
    "PL":   ("NYMEX", "PL"),        # Platinum ✓
    
    # ── COMEX Metals ─────────────────────────────────────────────
    "GC":   ("COMEX", "GC"),        # Gold ($100/oz) ✓
    "SI":   ("COMEX", "SI"),        # Silver ($5000/oz) ✓
    "HG":   ("COMEX", "HG"),        # Copper ✓
    "MGC":  ("COMEX_MINI", "MGC"),  # Micro Gold ($10/oz) ✓
    "SIL":  ("COMEX_MINI", "SIL"),  # Micro Silver ($1000/oz) ✓
    "MHG":  ("COMEX_MINI", "MHG"),  # Micro Copper ✓
    
    # ── CBOT Bonds / Interest Rates ──────────────────────────────
    "ZB":   ("CBOT", "ZB"),         # 30-Year Treasury Bond
    "ZN":   ("CBOT", "ZN"),         # 10-Year Treasury Note
    "ZF":   ("CBOT", "ZF"),         # 5-Year Treasury Note ✓
    "ZT":   ("CBOT", "ZT"),         # 2-Year Treasury Note ✓
    "UB":   ("CBOT", "UB"),         # Ultra Treasury Bond ✓
    "TN":   ("CBOT", "TN"),         # Ultra 10-Year Note ✓
    
    # ── CBOT Grains & Agriculture ────────────────────────────────
    "ZC":   ("CBOT", "ZC"),         # Corn ($50/bu)
    "ZS":   ("CBOT", "ZS"),         # Soybeans ($50/bu)
    "ZW":   ("CBOT", "ZW"),         # Wheat ($50/bu)
    "ZM":   ("CBOT", "ZM"),         # Soybean Meal ✓
    "ZL":   ("CBOT", "ZL"),         # Soybean Oil ✓
    "ZO":   ("CBOT", "ZO"),         # Oats ✓
    "KE":   ("CBOT", "KE"),         # KC HRW Wheat ✓
    
    # ── CME Livestock ────────────────────────────────────────────
    "LE":   ("CME", "LE"),          # Live Cattle ✓
    "HE":   ("CME", "HE"),          # Lean Hogs ✓
    "GF":   ("CME", "GF"),          # Feeder Cattle ✓
    
    # ── CBOE Volatility ──────────────────────────────────────────
    "VX":   ("CBOE", "VX"),         # VIX Futures ✓
}

# NOT available on InsightSentry (tested 2026-03):
# ZR (Rough Rice), M6A/M6B (Micro AUD/GBP FX), MNG (Micro Nat Gas — untested)

# Contract month codes: F=Jan, G=Feb, H=Mar, J=Apr, K=May, M=Jun,
#                      N=Jul, Q=Aug, U=Sep, V=Oct, X=Nov, Z=Dec
ALL_MONTH_CODES = {
    1: 'F', 2: 'G', 3: 'H', 4: 'J', 5: 'K', 6: 'M',
    7: 'N', 8: 'Q', 9: 'U', 10: 'V', 11: 'X', 12: 'Z',
}

# Quarterly contract months: H=March, M=June, U=September, Z=December
CONTRACT_MONTHS = ['H', 'M', 'U', 'Z']

# Which calendar months each contract covers (for quarterly products)
CONTRACT_COVERAGE = {
    'H': [12, 1, 2, 3],   # Dec(prev year), Jan, Feb, Mar
    'M': [3, 4, 5, 6],    # Mar, Apr, May, Jun
    'U': [6, 7, 8, 9],    # Jun, Jul, Aug, Sep
    'Z': [9, 10, 11, 12], # Sep, Oct, Nov, Dec
}

# Products that use monthly contracts (not quarterly)
# These roll on a monthly basis — the active contract is typically the next month
MONTHLY_PRODUCTS = {
    "CL", "NG", "HO", "RB", "MCL", "MNG",    # Energy
    "GC", "SI", "HG", "MGC", "SIL", "MHG",    # Metals
    "PA", "PL",                                  # Precious metals
    "ZC", "ZS", "ZW", "ZM", "ZL", "ZO", "ZR", "KE",  # Grains
    "LE", "HE", "GF",                           # Livestock
    "VX",                                        # Volatility
    "BTC", "MBT", "ETH", "MET",                 # Crypto
}

# Interval mapping
INTERVAL_MAP = {
    "1m":  (1,  "minute"), "3m":  (3,  "minute"), "5m":  (5,  "minute"),
    "10m": (10, "minute"), "15m": (15, "minute"), "30m": (30, "minute"),
    "1h":  (60, "minute"), "2h":  (120,"minute"), "4h":  (240,"minute"),
    "1d":  (1,  "day"),    "1w":  (1,  "week"),
}


# ── Helpers ─────────────────────────────────────────────────────────
def get_headers(api_key):
    return {
        "x-rapidapi-host": RAPIDAPI_HOST,
        "x-rapidapi-key": api_key,
    }


def get_active_contract(exchange, product, dt):
    """Determine which contract is active for a given date.
    
    Monthly products (CL, GC, etc.): active contract is next month's delivery.
    Quarterly products (ES, NQ, etc.): active contract is next quarterly expiry.
    """
    month = dt.month
    year = dt.year

    if product in MONTHLY_PRODUCTS:
        # Monthly: active contract is next month (front month)
        next_month = month + 1
        next_year = year
        if next_month > 12:
            next_month = 1
            next_year += 1
        code = ALL_MONTH_CODES[next_month]
        return f"{exchange}:{product}{code}{next_year}"
    else:
        # Quarterly: find next quarterly expiry
        for contract in CONTRACT_MONTHS:
            coverage = CONTRACT_COVERAGE[contract]
            if month in coverage:
                contract_year = year
                if contract == 'H' and month == 12:
                    contract_year = year + 1
                return f"{exchange}:{product}{contract}{contract_year}"

        return f"{exchange}:{product}H{year}"


def get_contracts_for_period(exchange, product, start_dt, end_dt):
    """Generate list of (contract_symbol, [(year, month)]) needed for date range."""
    contracts = {}
    current = start_dt.replace(day=1)

    while current <= end_dt:
        symbol = get_active_contract(exchange, product, current)
        ym = (current.year, current.month)
        if symbol not in contracts:
            contracts[symbol] = []
        if ym not in contracts[symbol]:
            contracts[symbol].append(ym)
        current = (current + timedelta(days=32)).replace(day=1)

    return list(contracts.items())


# ── Database ────────────────────────────────────────────────────────
def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            symbol   TEXT NOT NULL,
            interval TEXT NOT NULL,
            ts       INTEGER NOT NULL,
            open     REAL NOT NULL,
            high     REAL NOT NULL,
            low      REAL NOT NULL,
            close    REAL NOT NULL,
            volume   REAL NOT NULL,
            PRIMARY KEY (symbol, interval, ts)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup ON ohlcv (symbol, interval, ts)")
    conn.commit()
    return conn


def upsert_bars(conn, symbol, interval, bars):
    if not bars:
        return 0
    rows = []
    for b in bars:
        ts_ms = int(float(b["time"]) * 1000)  # unix seconds -> ms
        rows.append((symbol, interval, ts_ms, b["open"], b["high"], b["low"], b["close"], b["volume"]))
    conn.executemany(
        "INSERT OR REPLACE INTO ohlcv (symbol, interval, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
        rows
    )
    conn.commit()
    return len(rows)


def get_cache_range(conn, symbol, interval):
    row = conn.execute(
        "SELECT MIN(ts), MAX(ts) FROM ohlcv WHERE symbol=? AND interval=?",
        (symbol, interval)
    ).fetchone()
    if row and row[0] is not None:
        return row[0], row[1]
    return None, None


def get_cached_bars(conn, symbol, interval, start_ts, end_ts):
    return conn.execute(
        "SELECT ts, open, high, low, close, volume FROM ohlcv WHERE symbol=? AND interval=? AND ts>=? AND ts<=? ORDER BY ts",
        (symbol, interval, start_ts, end_ts)
    ).fetchall()


# ── API: /history endpoint ──────────────────────────────────────────
def fetch_history_month(api_key, contract_symbol, bar_interval, year, month):
    """Fetch one month of data via /v3/symbols/{symbol}/history."""
    start_date = f"{year:04d}-{month:02d}"
    url = f"{BASE_URL}/v3/symbols/{contract_symbol}/history"
    params = {
        "bar_type": "minute",
        "bar_interval": str(bar_interval),
        "start_date": start_date,
        "badj": "true",
    }

    try:
        resp = requests.get(url, headers=get_headers(api_key), params=params, timeout=120)

        if resp.status_code == 429:
            print(f"\n  Rate limited! Waiting 30s...", flush=True)
            time.sleep(30)
            resp = requests.get(url, headers=get_headers(api_key), params=params, timeout=120)

        if resp.status_code != 200:
            text = resp.text[:200] if resp.text else ""
            # HTTP 400 "Invalid Symbol Code" is expected for contract months
            # that don't exist for this product (e.g., MGC only has certain months)
            if resp.status_code == 400 and "Invalid Symbol Code" in text:
                print(f"(no contract)")
                return []
            elif resp.status_code == 400 and "No data is available" in text:
                print(f"(no data)")
                return []
            print(f"HTTP {resp.status_code}: {text}")
            return []

        data = resp.json()

        if "message" in data or "error" in data:
            msg = data.get("message") or data.get("error")
            if "Invalid Symbol" in str(msg) or "No data" in str(msg):
                print(f"(skipped)")
                return []
            print(f"API error: {msg}")
            return []

        return data.get("series", [])

    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return []


# ── Fetch all bars ──────────────────────────────────────────────────
def fetch_all_bars(api_key, exchange, product, bar_interval, days_back):
    """Fetch all bars by iterating contracts and months via /history."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)

    contracts = get_contracts_for_period(exchange, product, start, now)
    all_bars = []
    request_count = 0

    total_months = sum(len(months) for _, months in contracts)
    print(f"  Contracts: {len(contracts)}, months to fetch: {total_months}")
    est_time = total_months * RATE_LIMIT_DELAY
    print(f"  Estimated time: ~{est_time:.0f}s ({est_time/60:.1f} min)\n")

    for contract_symbol, months in contracts:
        print(f"  ── {contract_symbol} ──")
        for year, month in months:
            if request_count > 0:
                time.sleep(RATE_LIMIT_DELAY)

            print(f"    {year}-{month:02d}...", end=" ", flush=True)
            bars = fetch_history_month(api_key, contract_symbol, bar_interval, year, month)
            print(f"{len(bars)} bars")
            all_bars.extend(bars)
            request_count += 1

    # Deduplicate by timestamp (contract overlaps in rollover months)
    seen = set()
    unique = []
    for b in all_bars:
        ts = b["time"]
        if ts not in seen:
            seen.add(ts)
            unique.append(b)
    unique.sort(key=lambda x: x["time"])

    print(f"\n  Total requests: {request_count}")
    return unique


# ── Export ──────────────────────────────────────────────────────────
def filter_maintenance_halt(rows):
    """Remove bars during CME daily maintenance halt (16:00-17:00 CT).
    
    CME futures halt trading from 16:00-17:00 CT (Central Time) every weekday.
    TradingView doesn't show bars during this period. Removing them from our
    data ensures indicator lookbacks (ATR, highest/lowest, SMA) use the same
    bars as TV, preventing divergence in stops, targets, and trailing exits.
    
    Also removes weekend bars (Saturday 00:00 - Sunday 17:00 CT).
    """
    from datetime import datetime, timezone, timedelta
    
    filtered = []
    removed = 0
    
    for row in rows:
        ts_ms = row[0]
        ts_s = ts_ms / 1000
        
        dt_utc = datetime.fromtimestamp(ts_s, tz=timezone.utc)
        year = dt_utc.year
        
        # DST check: 2nd Sunday of March to 1st Sunday of November
        mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
        mar_sun2 = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
        nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
        nov_sun1 = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
        is_dst = mar_sun2 <= dt_utc < nov_sun1
        ct_offset = timedelta(hours=-5 if is_dst else -6)
        dt_ct = dt_utc + ct_offset
        
        ct_hour = dt_ct.hour
        ct_minute = dt_ct.minute
        ct_weekday = dt_ct.weekday()  # 0=Mon, 5=Sat, 6=Sun
        
        # Skip maintenance halt: 16:00-16:59 CT (bar at 16:00 through 16:55)
        if ct_hour == 16:
            removed += 1
            continue
        
        # Skip weekend: Saturday all day + Sunday before 17:00 CT
        # CME Globex reopens Sunday 17:00 CT
        if ct_weekday == 5:  # Saturday
            removed += 1
            continue
        if ct_weekday == 6 and ct_hour < 17:  # Sunday before 17:00
            removed += 1
            continue
        
        # Skip Friday after 16:00 CT (market closed for weekend)
        if ct_weekday == 4 and ct_hour >= 16:
            removed += 1
            continue
        
        filtered.append(row)
    
    if removed > 0:
        print(f"  Filtered {removed} maintenance halt/weekend bars ({len(filtered)} remaining)")
    
    return filtered


def export_csv(rows, symbol, interval, days, output_dir="."):
    """Export to backtester format: timestamp(unix ms),open,high,low,close,volume
    
    Automatically filters out CME maintenance halt bars (16:00-17:00 CT)
    so the bar stream matches TradingView's chart data.
    """
    # Filter maintenance halt bars before export
    clean_rows = filter_maintenance_halt(rows)
    
    filename = f"{symbol}_{interval}_{days}d_bt.csv"
    filepath = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, "w") as f:
        for ts, o, h, l, c, v in clean_rows:
            f.write(f"{ts},{o},{h},{l},{c},{v}\n")
    print(f"\nExported {len(clean_rows)} bars -> {filepath}")
    return filepath


# ── Main ────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 4:
        print("Usage: python futures_fetcher.py <SYMBOL> <INTERVAL> <DAYS> [export|csv]")
        print("\nSymbols by category:")
        categories = {}
        for sym, (exch, prod) in sorted(SYMBOL_MAP.items()):
            cat = exch.replace("CME_MINI", "CME Equity")
            if sym in ("BTC","MBT","ETH","MET"): cat = "CME Crypto"
            elif sym.startswith("6") or sym.startswith("M6") or sym == "MJY": cat = "CME FX"
            elif exch == "CME" and sym in ("LE","HE","GF"): cat = "CME Livestock"
            categories.setdefault(cat, []).append(sym)
        for cat in sorted(categories):
            print(f"  {cat}: {', '.join(sorted(categories[cat]))}")
        print(f"\n  Total: {len(SYMBOL_MAP)} symbols")
        print("Intervals: " + ", ".join(INTERVAL_MAP.keys()))
        print("\nExamples:")
        print("  python futures_fetcher.py ES 5m 365")
        print("  python futures_fetcher.py ES 5m 365 export")
        print("  python futures_fetcher.py NQ 1h 180 csv")
        sys.exit(1)

    symbol_input = sys.argv[1].upper()
    interval = sys.argv[2].lower()
    days = int(sys.argv[3])
    do_export = len(sys.argv) > 4 and sys.argv[4].lower() in ("export", "csv")

    if symbol_input not in SYMBOL_MAP:
        print(f"Unknown symbol: {symbol_input}")
        print(f"Valid: {', '.join(sorted(SYMBOL_MAP.keys()))}")
        sys.exit(1)

    exchange, product = SYMBOL_MAP[symbol_input]
    symbol_key = symbol_input

    if interval not in INTERVAL_MAP:
        print(f"Invalid interval: {interval}. Valid: {', '.join(INTERVAL_MAP.keys())}")
        sys.exit(1)

    bar_interval, bar_type = INTERVAL_MAP[interval]

    api_key = os.environ.get("RAPIDAPI_KEY")
    if not api_key:
        print("Error: RAPIDAPI_KEY environment variable not set")
        print("Set it with: setx RAPIDAPI_KEY your_key_here")
        sys.exit(1)

    print(f"═══ Futures Fetcher (InsightSentry) ═══")
    print(f"Symbol: {symbol_key} -> {exchange}:{product}")
    print(f"Interval: {interval} ({bar_interval} {bar_type})")
    print(f"Period: {days} days\n")

    conn = init_db()

    now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    # Check cache
    cache_min, cache_max = get_cache_range(conn, symbol_key, interval)
    need_fetch = True

    if cache_min and cache_max:
        cache_start_dt = datetime.fromtimestamp(cache_min / 1000, tz=timezone.utc)
        cache_end_dt = datetime.fromtimestamp(cache_max / 1000, tz=timezone.utc)
        stale_min = (now_ts - cache_max) / 60000
        print(f"Cache: {cache_start_dt:%Y-%m-%d %H:%M} -> {cache_end_dt:%Y-%m-%d %H:%M} UTC")
        print(f"Staleness: {stale_min:.0f} min")

        if cache_min <= start_ts and stale_min < 10:
            print("Cache is fresh -- skipping fetch.\n")
            need_fetch = False
        else:
            if cache_min > start_ts:
                print("Need older data...")
            if stale_min >= 10:
                print("Cache stale, refreshing...")

    if need_fetch:
        bars = fetch_all_bars(api_key, exchange, product, bar_interval, days)
        print(f"\nFetched: {len(bars)} bars total")
        if bars:
            n = upsert_bars(conn, symbol_key, interval, bars)
            print(f"Upserted: {n} bars to SQLite")

    # Query result
    rows = get_cached_bars(conn, symbol_key, interval, start_ts, now_ts)
    print(f"\nAvailable: {len(rows)} bars (raw)")

    # Filter maintenance halt bars (16:00-17:00 CT) to match TradingView
    rows = filter_maintenance_halt(rows)

    if rows:
        first_dt = datetime.fromtimestamp(rows[0][0] / 1000, tz=timezone.utc)
        last_dt = datetime.fromtimestamp(rows[-1][0] / 1000, tz=timezone.utc)
        print(f"Range: {first_dt:%Y-%m-%d %H:%M} -> {last_dt:%Y-%m-%d %H:%M} UTC")

    if do_export and rows:
        export_csv(rows, symbol_key, interval, days, os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "futures"))

    conn.close()
    return rows


if __name__ == "__main__":
    main()
