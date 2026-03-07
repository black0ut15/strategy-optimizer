"""Validate Python backtester against TradingView trade export.

Usage:
    cd src-tauri/python
    PYTHONPATH=. python validate.py <data_csv> <tv_trade_csv>

Compares trade count, direction sequence, and P&L distribution.
"""

import sys
import os
import csv
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from backtest_engine import BacktestEngine, Bars
from strategies.mesa_mama_fama import MesaMamaFama


def load_tv_trades(filepath: str) -> list:
    """Parse TradingView trade export CSV into trade list."""
    trades = []
    rows = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    # Group by trade number (each trade has entry + exit row)
    trade_map = {}
    for row in rows:
        num = int(row["Trade #"])
        if num not in trade_map:
            trade_map[num] = {"entry": None, "exit": None}
        if "Entry" in row["Type"]:
            trade_map[num]["entry"] = row
        elif "Exit" in row["Type"]:
            trade_map[num]["exit"] = row

    for num in sorted(trade_map.keys()):
        t = trade_map[num]
        if t["entry"] and t["exit"]:
            entry_type = t["entry"]["Type"].lower()
            direction = "long" if "long" in entry_type else "short"
            trades.append({
                "num": num,
                "direction": direction,
                "entry_price": float(t["entry"]["Price USDT"]),
                "exit_price": float(t["exit"]["Price USDT"]),
                "qty": float(t["entry"]["Position size (qty)"]),
                "net_pnl": float(t["exit"]["Net P&L USDT"]),
                "entry_time": t["entry"]["Date and time"],
                "exit_time": t["exit"]["Date and time"],
            })

    return trades


def main():
    if len(sys.argv) < 3:
        print("Usage: PYTHONPATH=. python validate.py <data_csv> <tv_trade_csv>")
        sys.exit(1)

    data_file = sys.argv[1]
    tv_file = sys.argv[2]

    print("Loading data...")
    bars = Bars.from_csv(data_file)
    print(f"  {len(bars)} bars")

    print("Loading TradingView trades...")
    tv_trades = load_tv_trades(tv_file)
    print(f"  {len(tv_trades)} trades from TV export")
    print(f"  (Note: TV caps export at ~200 rows = ~100 trades)")

    print("\nRunning Python backtester...")
    strategy = MesaMamaFama()
    result = BacktestEngine.run_detail(
        strategy, bars, strategy.get_param_defaults(),
        initial_capital=1_000_000.0, fee_pct=0.035,
    )
    py_trades = result.trades
    print(f"  {len(py_trades)} trades from Python engine")

    # Compare
    print(f"\n{'='*60}")
    print(f"  COMPARISON SUMMARY")
    print(f"{'='*60}")

    s = result.stats
    print(f"\n  Python Engine Results:")
    print(f"    Net Profit:    ${s.net_profit:>12,.2f}")
    print(f"    Profit Factor: {s.profit_factor:>12.2f}")
    print(f"    Win Rate:      {s.win_rate:>11.1f}%")
    print(f"    Total Trades:  {s.total_trades:>12d}")
    print(f"    Max Drawdown:  ${s.max_drawdown:>12,.2f}")

    # TV summary from the export
    tv_wins = sum(1 for t in tv_trades if t["net_pnl"] > 0)
    tv_losses = sum(1 for t in tv_trades if t["net_pnl"] <= 0)
    tv_net = sum(t["net_pnl"] for t in tv_trades)
    tv_wr = tv_wins / len(tv_trades) * 100 if tv_trades else 0

    print(f"\n  TradingView Export ({len(tv_trades)} trades visible):")
    print(f"    Visible Net:   ${tv_net:>12,.2f}")
    print(f"    Visible WR:    {tv_wr:>11.1f}%")
    print(f"    (Full TV: ~598 trades, PF ~1.60, WR ~55.9%)")

    print(f"\n  Trade Count Match: {len(py_trades)} vs ~598 TV")
    print(f"    {'GOOD' if abs(len(py_trades) - 598) <= 5 else 'NEEDS INVESTIGATION'}")

    # Direction sequence check (last N trades from TV vs Python)
    if tv_trades and py_trades:
        # TV export shows last ~109 trades
        # We can't directly align them since data is from different exchanges
        tv_dirs = [t["direction"] for t in tv_trades]
        py_dirs = [t.direction for t in py_trades[-len(tv_trades):]]

        match_count = sum(1 for a, b in zip(tv_dirs, py_dirs) if a == b)
        total = min(len(tv_dirs), len(py_dirs))
        print(f"\n  Direction Match (last {total} trades): {match_count}/{total}")
        print(f"    ({match_count/total*100:.0f}% — note: different exchange data)")

    print(f"\n{'='*60}")
    print("  Note: Differences are expected with different exchange data.")
    print("  Binance Spot OHLCV ≠ Bitunix Perpetual OHLCV.")
    print("  Key metric: trade count within ±1% validates signal logic.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
