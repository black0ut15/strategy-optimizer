"""
Run a single parameter combo in detail mode and output trade log as CSV.

Usage via stdin (from Rust IPC):
  echo '{"strategy":"mesa_mama_fama","data_path":"data.csv","params":{...},"settings":{...}}' | python run_detail.py

Output: JSON with stats + trades array
"""

import json
import sys
import os
import numpy as np
from dataclasses import asdict

# Strategy files in %APPDATA%/StrategyOptimizer/strategies/
# Engine files in script dir. AppData strategies must take priority.
_script_dir = os.path.dirname(os.path.abspath(__file__))
_appdata = os.environ.get("APPDATA") or os.environ.get("USERPROFILE", "")
if _appdata:
    _strat_parent = os.path.join(_appdata, "StrategyOptimizer")
    if os.path.isdir(os.path.join(_strat_parent, "strategies")):
        conflicting = [p for p in sys.path if p != _strat_parent and os.path.isdir(os.path.join(p, "strategies"))]
        remaining = [p for p in sys.path if p not in conflicting and p != _strat_parent]
        sys.path = [_strat_parent] + remaining + conflicting
if _script_dir not in sys.path:
    sys.path.append(_script_dir)

from backtest_engine import BacktestEngine, Bars


def _resolve_strategy(name: str):
    """Import and return strategy class by name."""
    import importlib
    mod = importlib.import_module(f"strategies.{name}")
    # Find the Strategy subclass
    from strategy_base import Strategy
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        if (isinstance(attr, type) and issubclass(attr, Strategy) 
                and attr is not Strategy):
            return attr
    raise ValueError(f"No Strategy subclass found in strategies.{name}")


def main():
    input_data = json.loads(sys.stdin.read())

    strategy_name = input_data["strategy"]
    data_path = input_data["data_path"]
    params = input_data.get("params", {})
    settings = input_data.get("settings", {})

    initial_capital = settings.get("initial_capital", 1_000_000.0)
    fee_pct = settings.get("fee_pct", 0.035)
    warmup_bars = settings.get("warmup_bars", 0)
    fill_on_bar_close = settings.get("fill_on_bar_close", False)
    calc_on_order_fills = settings.get("calc_on_order_fills", True)

    # Load data
    data = np.genfromtxt(data_path, delimiter=",", skip_header=1)
    if data.ndim == 1:
        data = data.reshape(1, -1)

    o = data[:, 1]
    h = data[:, 2]
    l = data[:, 3]
    c = data[:, 4]
    bars = Bars(
        timestamp=data[:, 0],
        open=o,
        high=h,
        low=l,
        close=c,
        volume=data[:, 5] if data.shape[1] > 5 else np.zeros(len(data)),
        hl2=(h + l) / 2.0,
        hlc3=(h + l + c) / 3.0,
        ohlc4=(o + h + l + c) / 4.0,
        hlcc4=(h + l + c + c) / 4.0,
    )

    # Resolve strategy
    StrategyCls = _resolve_strategy(strategy_name)
    strategy = StrategyCls()

    # Run detail backtest
    result = BacktestEngine.run_detail(
        strategy, bars, params,
        initial_capital=initial_capital,
        fee_pct=fee_pct,
        warmup_bars=warmup_bars,
        fill_on_bar_close=fill_on_bar_close,
        calc_on_order_fills=calc_on_order_fills,
    )

    # Build output
    trades_list = []
    for t in result.trades:
        trades_list.append({
            "trade_num": t.trade_num,
            "direction": t.direction,
            "entry_bar": t.entry_bar,
            "exit_bar": t.exit_bar,
            "entry_price": round(t.entry_price, 6),
            "exit_price": round(t.exit_price, 6),
            "qty": round(t.qty, 8),
            "gross_pnl": round(t.gross_pnl, 2),
            "fee": round(t.fee, 2),
            "net_pnl": round(t.net_pnl, 2),
            "bars_held": t.bars_held,
            "exit_reason": t.exit_reason,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
        })

    output = {
        "stats": {
            "pf": round(result.stats.profit_factor, 4),
            "net": round(result.stats.net_profit, 2),
            "dd": round(result.stats.max_drawdown, 2),
            "dd_pct": round(result.stats.max_drawdown_pct, 2),
            "wr": round(result.stats.win_rate, 1),
            "trades": result.stats.total_trades,
            "gross_profit": round(result.stats.gross_profit, 2),
            "gross_loss": round(result.stats.gross_loss, 2),
        },
        "trade_log": trades_list,
    }

    sys.stdout.write(json.dumps(output))


if __name__ == "__main__":
    main()
