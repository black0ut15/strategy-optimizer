"""
Inspect cached crypto OHLCV data in SQLite database.
Run from C:\trading-data:  python inspect_data.py
"""

import sqlite3
import pandas as pd

DB_FILE = "crypto_ohlcv.db"

def inspect():
    conn = sqlite3.connect(DB_FILE)

    # ── What's in the database? ───────────────
    print("=" * 60)
    print("CACHED DATASETS")
    print("=" * 60)
    summary = pd.read_sql("""
        SELECT 
            symbol,
            source,
            interval,
            COUNT(*) as bars,
            MIN(timestamp) as earliest,
            MAX(timestamp) as latest
        FROM bars
        GROUP BY symbol, source, interval
        ORDER BY symbol, interval
    """, conn)
    print(summary.to_string(index=False))

    # ── Sample data for each dataset ─────────
    datasets = conn.execute("""
        SELECT DISTINCT symbol, source, interval FROM bars
    """).fetchall()

    for symbol, source, interval in datasets:
        print(f"\n{'=' * 60}")
        print(f"SAMPLE: {symbol} | {interval} | {source}")
        print("=" * 60)

        df = pd.read_sql(f"""
            SELECT timestamp, open, high, low, close, volume
            FROM bars
            WHERE symbol=? AND source=? AND interval=?
            ORDER BY timestamp ASC
            LIMIT 5
        """, conn, params=(symbol, source, interval))
        print("First 5 rows:")
        print(df.to_string(index=False))

        df_tail = pd.read_sql(f"""
            SELECT timestamp, open, high, low, close, volume
            FROM bars
            WHERE symbol=? AND source=? AND interval=?
            ORDER BY timestamp DESC
            LIMIT 5
        """, conn, params=(symbol, source, interval))
        print("\nLast 5 rows:")
        print(df_tail.to_string(index=False))

        # Check for gaps
        df_full = pd.read_sql(f"""
            SELECT timestamp FROM bars
            WHERE symbol=? AND source=? AND interval=?
            ORDER BY timestamp ASC
        """, conn, params=(symbol, source, interval))

        df_full["timestamp"] = pd.to_datetime(df_full["timestamp"], utc=True)
        df_full = df_full.set_index("timestamp")

        interval_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30,
                            "1h": 60, "4h": 240, "1d": 1440}.get(interval, 5)

        expected_diff = pd.Timedelta(minutes=interval_minutes)
        diffs = df_full.index.to_series().diff().dropna()
        gaps = diffs[diffs > expected_diff * 2]

        if gaps.empty:
            print(f"\nGap check: No significant gaps found.")
        else:
            print(f"\nGap check: {len(gaps)} gaps found (expected for weekends/holidays on some pairs):")
            for ts, gap in gaps.head(5).items():
                print(f"  {ts} — gap of {gap}")

    conn.close()
    print("\n" + "=" * 60)
    print("Inspection complete.")
    print("=" * 60)

if __name__ == "__main__":
    inspect()
