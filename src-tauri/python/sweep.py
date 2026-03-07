"""Parameter Sweep Optimizer.

Runs a strategy across all parameter combinations using multiprocessing.
Can be invoked as:
  1. CLI:       python sweep.py --strategy mesa_mama_fama --data data.csv --config sweep.json
  2. Subprocess: called from Rust/Tauri with JSON on stdin, results on stdout
  3. Library:    sweep.run_sweep(strategy_class, bars, config) -> results

Config JSON format (same as frontend generates):
{
  "parameters": {
    "fast_limit": [0.1, 0.9, 0.05],      // [min, max, step] range
    "slow_limit": [0.02],                  // fixed value
    "use_filter": [true, false],           // bool options
    "exit_mode": ["FPO", "Swing"]          // string options
  },
  "sweep_settings": {
    "fee_pct": 0.035,
    "initial_capital": 50000,
    "top_n": 200,
    "sort_by": "pf",
    "min_trades": 0,
    "max_dd_pct": 100.0,
    "warmup_bars": 0
  }
}
"""

import json
import sys
import os
import time
import itertools
import multiprocessing as mp
from typing import Any, Dict, List, Tuple, Type, Optional
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

from strategy_base import Strategy
from backtest_engine import BacktestEngine, Bars


# ═══════════════════════════════════════════════════════════════════════════
# Parameter grid expansion
# ═══════════════════════════════════════════════════════════════════════════

def expand_param(name: str, val: Any, param_meta: dict) -> List[Any]:
    """Expand a config parameter value into a list of concrete values.

    Handles:
      - [min, max, step]  → numeric range
      - [v]               → single fixed value
      - [true, false]     → bool options
      - ["a", "b", "c"]   → string options
    """
    if not isinstance(val, list):
        return [val]

    if len(val) == 1:
        return [val[0]]

    # Check for numeric range: [min, max, step]
    if (len(val) == 3 and all(isinstance(v, (int, float)) for v in val)
            and val[2] > 0 and val[0] <= val[1]):
        start, stop, step = val
        values = []
        v = start
        while v <= stop + step * 0.001:
            values.append(round(v, 10))
            v += step
        if not values:
            values = [start]
        # Cast to int if the parameter default is int
        p = param_meta.get(name)
        if p and isinstance(p.default, (int, bool)):
            if isinstance(p.default, bool):
                values = [bool(v) for v in values]
            else:
                values = [int(round(v)) for v in values]
        return values

    # List of explicit values (bool/string/numeric)
    return val


def build_param_grid(
    config_params: Dict[str, Any],
    strategy_class: Type[Strategy],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Build cartesian product of all parameter combinations.

    Returns:
        (param_names, list_of_param_dicts)
    """
    param_meta = strategy_class.params
    names = []
    value_lists = []

    for name, val in config_params.items():
        values = expand_param(name, val, param_meta)
        if values:
            names.append(name)
            value_lists.append(values)

    # Cartesian product
    if not value_lists:
        return names, [{}]

    grid = []
    for combo in itertools.product(*value_lists):
        grid.append(dict(zip(names, combo)))

    return names, grid


# ═══════════════════════════════════════════════════════════════════════════
# Worker function (runs in subprocess via multiprocessing)
# ═══════════════════════════════════════════════════════════════════════════

def _worker_init(bars_path: str, strategy_name: str):
    """Initialize worker process — load data once per process."""
    global _worker_bars, _worker_strategy_class
    _worker_bars = Bars.from_csv(bars_path)
    _worker_strategy_class = _resolve_strategy(strategy_name)


def _worker_run(args: Tuple[Dict[str, Any], float, float, int, bool, bool]) -> Optional[dict]:
    """Run a single backtest. Returns result dict or None if filtered."""
    params, fee_pct, initial_capital, warmup_bars, fill_on_bar_close, calc_on_order_fills = args

    strategy = _worker_strategy_class()
    stats = BacktestEngine.run_fast(
        strategy, _worker_bars, params,
        initial_capital=initial_capital,
        fee_pct=fee_pct,
        warmup_bars=warmup_bars,
        fill_on_bar_close=fill_on_bar_close,
        calc_on_order_fills=calc_on_order_fills,
    )

    if stats.total_trades == 0:
        return None

    return {
        "params": params,
        "pf": round(stats.profit_factor, 4),
        "net": round(stats.net_profit, 2),
        "dd": round(stats.max_drawdown, 2),
        "dd_pct": round(stats.max_drawdown_pct, 2),
        "wr": round(stats.win_rate, 1),
        "trades": stats.total_trades,
        "gross_profit": round(stats.gross_profit, 2),
        "gross_loss": round(stats.gross_loss, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Strategy resolver
# ═══════════════════════════════════════════════════════════════════════════

def _resolve_strategy(name: str) -> Type[Strategy]:
    """Import and return a strategy class by name."""
    name_lower = name.lower().replace(" ", "_").replace("-", "_")

    strategy_map = {
        "mesa_mama_fama": ("strategies.mesa_mama_fama", "MesaMamaFama"),
        "mesamama": ("strategies.mesa_mama_fama", "MesaMamaFama"),
        "larry_williams": ("strategies.larry_williams", "LarryWilliamsCombined"),
        "larrywilliams": ("strategies.larry_williams", "LarryWilliamsCombined"),
        "pure_orb": ("strategies.pure_orb", "PureORB"),
        "pureorb": ("strategies.pure_orb", "PureORB"),
        "orb": ("strategies.pure_orb", "PureORB"),
    }

    if name_lower in strategy_map:
        module_name, class_name = strategy_map[name_lower]
    else:
        # Try dynamic import: assume module is strategies.<name> with class <Name>
        module_name = f"strategies.{name_lower}"
        # CamelCase the name
        class_name = "".join(w.capitalize() for w in name_lower.split("_"))

    import importlib
    try:
        module = importlib.import_module(module_name)
    except Exception as e:
        import traceback
        raise ValueError(f"Cannot import strategy module '{module_name}': {e}\n{traceback.format_exc()}")

    cls = getattr(module, class_name, None)
    if cls is None:
        # Try finding any Strategy subclass in the module
        # Use name-based check to avoid identity issues across different import paths
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and attr.__name__ != "Strategy":
                # Check by base class name instead of identity
                bases = [b.__name__ for b in attr.__mro__]
                if "Strategy" in bases:
                    cls = attr
                    break

    if cls is None:
        # Last resort: try any class that has 'params' and 'on_bar'
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and hasattr(attr, "params") and hasattr(attr, "on_bar"):
                cls = attr
                break

    if cls is None:
        available = [a for a in dir(module) if not a.startswith("_")]
        all_classes = [a for a in dir(module) if isinstance(getattr(module, a, None), type)]
        raise ValueError(
            f"No Strategy subclass found in '{module_name}'. "
            f"Looked for class '{class_name}'. "
            f"Module file: {getattr(module, '__file__', 'unknown')}. "
            f"All names: {available}. "
            f"Classes: {all_classes}"
        )

    return cls


# ═══════════════════════════════════════════════════════════════════════════
# Main sweep runner
# ═══════════════════════════════════════════════════════════════════════════

def run_sweep(
    strategy_name: str,
    data_path: str,
    config: dict,
    progress_callback=None,
    num_workers: int = None,
) -> List[dict]:
    """Run parameter sweep and return sorted results.

    Args:
        strategy_name: Strategy class name or alias.
        data_path: Path to OHLCV CSV file.
        config: Sweep configuration dict (parameters + sweep_settings).
        progress_callback: Optional fn(completed, total) called periodically.
        num_workers: Number of parallel workers (default: CPU count).

    Returns:
        List of result dicts, sorted and filtered per sweep_settings.
    """
    strategy_class = _resolve_strategy(strategy_name)

    settings = config.get("sweep_settings", {})
    fee_pct = settings.get("fee_pct", 0.035)
    initial_capital = settings.get("initial_capital", 10000.0)
    top_n = settings.get("top_n", 200)
    sort_by = settings.get("sort_by", "pf")
    min_trades = settings.get("min_trades", 0)
    max_dd_pct = settings.get("max_dd_pct", 100.0)
    warmup_bars = settings.get("warmup_bars", 0)

    # TradingView fill/recalculation modes
    fill_on_bar_close = settings.get("fill_on_bar_close", False)
    calc_on_order_fills = settings.get("calc_on_order_fills", True)

    # Build parameter grid
    param_names, grid = build_param_grid(config.get("parameters", {}), strategy_class)
    total = len(grid)

    if total == 0:
        return [], 0, 0.0

    # Optional chunking for distributed mode
    chunk_start = settings.get("chunk_start", 0)
    chunk_end = settings.get("chunk_end", 0)
    if chunk_end > chunk_start:
        grid = grid[chunk_start:min(chunk_end, total)]
        total = len(grid)

    if total == 0:
        return [], 0, 0.0

    if num_workers is None:
        num_workers = max(1, mp.cpu_count())

    # Prepare worker args
    work_items = [(params, fee_pct, initial_capital, warmup_bars,
                   fill_on_bar_close, calc_on_order_fills) for params in grid]

    t0 = time.time()
    results = []

    if total <= 4 or num_workers <= 1:
        # Small grid — run in-process (no multiprocessing overhead)
        _worker_init(data_path, strategy_name)
        for idx, item in enumerate(work_items):
            r = _worker_run(item)
            if r is not None:
                results.append(r)
            if progress_callback and (idx % 50 == 0 or idx == total - 1):
                progress_callback(idx + 1, total)
    else:
        # Parallel execution
        with mp.Pool(
            processes=num_workers,
            initializer=_worker_init,
            initargs=(data_path, strategy_name),
        ) as pool:
            completed = 0
            for r in pool.imap_unordered(_worker_run, work_items, chunksize=max(1, total // (num_workers * 10))):
                if r is not None:
                    results.append(r)
                completed += 1
                if progress_callback and (completed % 100 == 0 or completed == total):
                    progress_callback(completed, total)

    elapsed = time.time() - t0

    # Filter
    if min_trades > 0:
        results = [r for r in results if r["trades"] >= min_trades]
    if max_dd_pct < 100.0:
        results = [r for r in results if r["dd_pct"] <= max_dd_pct]

    # Sort
    sort_keys = {
        "pf": lambda r: r["pf"],
        "net": lambda r: r["net"],
        "wr": lambda r: r["wr"],
        "trades": lambda r: r["trades"],
        "dd": lambda r: -r["dd"],  # lower is better
    }
    key_fn = sort_keys.get(sort_by, sort_keys["pf"])
    results.sort(key=key_fn, reverse=True)

    # Truncate
    results = results[:top_n]

    return results, total, elapsed


# ═══════════════════════════════════════════════════════════════════════════
# CLI + subprocess interface
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """CLI entry point.

    Modes:
      1. --stdin: Read JSON from stdin (for Rust subprocess invocation)
         Input:  {"strategy": "mesa_mama_fama", "data_path": "...", "config": {...}}
         Output: {"results": [...], "total_combos": N, "elapsed": S}

      2. CLI args: --strategy NAME --data PATH --config PATH [--workers N]
    """
    import argparse

    parser = argparse.ArgumentParser(description="Strategy Parameter Sweep")
    parser.add_argument("--stdin", action="store_true",
                        help="Read JSON from stdin (subprocess mode)")
    parser.add_argument("--strategy", "-s", type=str, help="Strategy name")
    parser.add_argument("--data", "-d", type=str, help="Data CSV path")
    parser.add_argument("--config", "-c", type=str, help="Config JSON path")
    parser.add_argument("--workers", "-w", type=int, default=None,
                        help="Number of parallel workers")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output JSON path (default: stdout)")
    args = parser.parse_args()

    if args.stdin:
        # Subprocess mode — read from stdin, write to stdout
        input_data = json.loads(sys.stdin.read())
        strategy = input_data["strategy"]
        data_path = input_data["data_path"]
        config = input_data["config"]
        num_workers = input_data.get("workers", None)

        def progress_cb(done, total):
            # Write progress to stderr so Rust can read it
            pct = done / total if total > 0 else 0
            sys.stderr.write(f"PROGRESS:{pct:.6f}\n")
            sys.stderr.flush()

        results, total, elapsed = run_sweep(
            strategy, data_path, config,
            progress_callback=progress_cb,
            num_workers=num_workers,
        )

        output = {
            "results": results,
            "total_combos": total,
            "elapsed": round(elapsed, 2),
        }
        sys.stdout.write(json.dumps(output))
        sys.stdout.flush()

    else:
        # CLI mode
        if not args.strategy or not args.data or not args.config:
            parser.print_help()
            sys.exit(1)

        with open(args.config, "r") as f:
            config = json.load(f)

        def progress_cb(done, total):
            pct = done / total * 100 if total > 0 else 0
            rate = done / max(0.01, time.time() - t0)
            eta = (total - done) / max(1, rate)
            print(f"\r  {pct:5.1f}% | {done:,}/{total:,} | "
                  f"{rate:,.0f}/s | ETA {eta:.0f}s", end="", flush=True)

        print(f"Strategy: {args.strategy}")
        print(f"Data:     {args.data}")
        print(f"Config:   {args.config}")

        t0 = time.time()
        results, total, elapsed = run_sweep(
            args.strategy, args.data, config,
            progress_callback=progress_cb,
            num_workers=args.workers,
        )

        print(f"\n\nCompleted {total:,} combinations in {elapsed:.1f}s "
              f"({total/elapsed:,.0f}/s)")
        print(f"Results: {len(results)} (after filtering)")

        if results:
            print(f"\nTop 10:")
            print(f"  {'PF':>8} {'Net':>12} {'DD%':>7} {'WR%':>7} {'Trades':>7}")
            for r in results[:10]:
                print(f"  {r['pf']:>8.3f} ${r['net']:>10,.2f} {r['dd_pct']:>6.1f}% "
                      f"{r['wr']:>6.1f}% {r['trades']:>7d}")

        if args.output:
            with open(args.output, "w") as f:
                json.dump({"results": results, "total_combos": total,
                           "elapsed": elapsed}, f, indent=2)
            print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
