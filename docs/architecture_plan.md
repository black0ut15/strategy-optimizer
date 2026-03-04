# Strategy Optimizer v2 — Architecture Plan

## Vision

A desktop application where users upload trading strategies (Pine Script or Python), configure parameter sweep ranges, and run distributed optimization across local network machines. Users can drill into any result to see the full trade list.

---

## Design Principles

1. **Python is the execution layer.** All backtesting runs as Python. Pine Script is an input format that gets transpiled to Python.
2. **Speed through Rust orchestration.** Rust (Tauri) manages the UI, distributes work, and collects results. Python does the per-bar math.
3. **Two-mode backtester.** Fast mode returns summary stats only (for sweeps). Detail mode returns full trade list (for drill-down).
4. **Strategies are portable.** A generated Python strategy file is self-contained — users can run it outside the app, share it, or edit it.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Tauri Desktop App (Rust + React)                       │
│                                                         │
│  ┌──────────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ Strategy Lab  │  │  Data    │  │  Results Viewer   │  │
│  │              │  │ Fetcher  │  │                   │  │
│  │ Upload .pine │  │          │  │ Summary table     │  │
│  │ Upload .py   │  │ Crypto   │  │ Click row →       │  │
│  │ Preview code │  │ Futures  │  │   trade list      │  │
│  │ Set params   │  │ Stocks   │  │   equity curve    │  │
│  └──────┬───────┘  └──────────┘  └────────┬──────────┘  │
│         │                                  │             │
│  ┌──────▼──────────────────────────────────▼──────────┐  │
│  │           Sweep Coordinator (Rust)                 │  │
│  │                                                    │  │
│  │  • Builds parameter grid                           │  │
│  │  • Distributes chunks to local + remote workers    │  │
│  │  • Collects results, sorts, filters                │  │
│  │  • Re-runs single combo in detail mode on demand   │  │
│  └──────┬────────────────────────────┬────────────────┘  │
│         │                            │                   │
└─────────┼────────────────────────────┼───────────────────┘
          │                            │
    ┌─────▼─────┐              ┌───────▼───────┐
    │ Local     │              │ Remote Worker  │
    │ Worker    │              │ (lightweight)  │
    │           │              │                │
    │ Python    │              │ Python         │
    │ subprocess│              │ subprocess     │
    └─────┬─────┘              └───────┬────────┘
          │                            │
    ┌─────▼────────────────────────────▼────────┐
    │        Python Backtester Engine            │
    │                                            │
    │  • Loads strategy .py file                 │
    │  • Loads OHLCV data (CSV/SQLite)           │
    │  • Runs bar-by-bar with given params       │
    │  • Returns summary stats OR full trade log │
    └────────────────────────────────────────────┘
```

---

## Component Breakdown

### 1. Python Backtester Engine (`backtest_engine.py`)

The core. ~300 lines. No dependencies beyond numpy.

```python
# The engine provides:
class Context:
    equity: float           # realized + unrealized
    position_size: float    # +qty = long, -qty = short, 0 = flat
    position_avg_price: float
    bar_index: int
    
    def entry(self, id: str, direction: str, qty: float)
    def close(self, id: str)
    def close_all(self)

# The engine tracks:
- Open position (direction, qty, entry price, entry bar)
- Pending orders (filled at next bar or at stop/limit prices)
- Equity curve (realized equity + unrealized per bar)
- Peak equity and max drawdown
- Trade log: [{entry_time, exit_time, direction, qty, entry_price,
               exit_price, pnl, fee, bars_held, exit_reason}]

# Two run modes:
def run_fast(strategy, bars, params) -> SummaryStats
    # Returns: pf, net_profit, max_dd, win_rate, total_trades, 
    #          gross_profit, gross_loss
    # Does NOT store trade log (saves memory for sweeps)

def run_detail(strategy, bars, params) -> DetailedResult  
    # Returns: SummaryStats + full trade log + equity curve
    # Used when user clicks a result row
```

**Order Fill Model (matching TradingView):**
- `process_orders_on_close=false`: orders placed on bar N fill at bar N+1 open
- Stop/limit orders check intrabar high/low
- Entry on reversal: close existing at fill price, open new at same price
- Fees: configurable per-side percentage on notional

**Performance target:** 100,000 bars × 1 combo = <50ms in Python with numpy vectorization for indicators. For 1M combos, the distributed system handles parallelism.

### 2. Strategy API (`strategy_base.py`)

What users implement (or the translator generates):

```python
from backtest_engine import Strategy, Param, Context
import numpy as np

class MesaMamaFama(Strategy):
    # Parameters — auto-discovered by optimizer
    params = {
        "fast_limit":    Param(0.38, min=0.01, max=1.0, step=0.01, 
                               group="MESA Settings"),
        "slow_limit":    Param(0.035, min=0.001, max=0.5, step=0.001,
                               group="MESA Settings"),
        "sep_threshold": Param(0.003, min=0.001, max=0.01, step=0.0001,
                               group="MESA Settings"),
        "margin_pct":    Param(10.0, min=1.0, max=25.0, step=0.5,
                               group="Sizing"),
        "max_leverage":  Param(5.0, min=1.0, max=20.0, step=0.5,
                               group="Sizing"),
        "min_hold_bars": Param(144, min=10, max=500, step=1,
                               group="Exit Management"),
        # ... all other inputs
    }

    def init(self, ctx: Context):
        """Called once before first bar. Set up indicators."""
        # Pre-compute MESA MAMA/FAMA for all bars (vectorized)
        self.mama, self.fama = self.compute_mesa(
            ctx.bars, self.p.fast_limit, self.p.slow_limit
        )
        self.regime_ema = ta.ema(ctx.bars.close, self.p.regime_ema_len)
    
    def on_bar(self, bar, ctx: Context):
        """Called for each bar. Implement entry/exit logic."""
        i = ctx.bar_index
        mama = self.mama[i]
        fama = self.fama[i]
        
        # ... signal logic ...
        
        if long_signal and ctx.position_size == 0:
            qty = self.compute_qty(ctx.equity, bar.close)
            ctx.entry("Long", "long", qty=qty)
    
    def compute_mesa(self, bars, fast, slow):
        """Vectorized MESA computation."""
        # ... numpy implementation ...
        return mama_array, fama_array
```

**Key design decisions:**
- `params` dict is the single source of truth for parameter discovery
- `init()` runs once — pre-compute indicators here (vectorized = fast)
- `on_bar()` runs per-bar — just logic, no heavy math
- `self.p.fast_limit` accesses current param values (set by optimizer)
- Strategy is a plain `.py` file — no magic, no decorators, no framework lock-in

### 3. Pine Script → Python Translator

Reuses existing tokenizer + parser + AST. New backend: `PythonEmitter`.

**Translation approach:**

| Pine Script | Python Output |
|------------|--------------|
| `input.float(0.38, "Fast Limit")` | `Param(0.38, ...)` in params dict |
| `input.bool(true, "Enable X")` | `Param(True, ...)` in params dict |
| `var float x = 0.0` | `self.x = 0.0` in `init()` |
| `ta.ema(close, 200)` | `ta.ema(bars.close, 200)` in `init()` |
| `ta.crossover(a, b)` | `ta.crossover(a, b)` (mapped to helper) |
| `strategy.entry("Long", strategy.long, qty=q)` | `ctx.entry("Long", "long", qty=q)` |
| `strategy.close("Long")` | `ctx.close("Long")` |
| `strategy.equity` | `ctx.equity` |
| `strategy.position_size` | `ctx.position_size` |
| `barstate.isconfirmed` | `True` (always, since we run on closed bars) |
| `f_clamp(x, lo, hi)` | `def f_clamp(x, lo, hi):` (user functions → methods) |
| `label.new(...)` / `plot(...)` / `table.*` | Skipped (visual only) |

**What the translator produces:**
1. A `.py` file implementing the Strategy API
2. All `input.*` calls → `params` dict entries
3. `var` declarations → `init()` body
4. Per-bar logic → `on_bar()` body  
5. User functions → class methods
6. Indicator computations → either vectorized in `init()` or per-bar

**Limitations (documented for users):**
- Security models, multi-timeframe, request.* not supported
- Some Pine builtins may need manual implementation
- Generated code should be reviewed — it's a starting point, not guaranteed

### 4. Technical Analysis Library (`ta.py`)

Shared between manual Python strategies and translated Pine scripts.
~200 lines of numpy-based indicator functions.

```python
def ema(data: np.ndarray, length: int) -> np.ndarray
def sma(data: np.ndarray, length: int) -> np.ndarray
def atr(high, low, close, length: int) -> np.ndarray
def crossover(a: np.ndarray, b: np.ndarray) -> np.ndarray  # bool array
def crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray
def highest(data, length) -> np.ndarray
def lowest(data, length) -> np.ndarray
def stdev(data, length) -> np.ndarray
def rsi(data, length) -> np.ndarray
def macd(data, fast, slow, signal) -> tuple
# ... extend as needed
```

### 5. Rust Sweep Coordinator

Existing distributed system, modified to call Python instead of Pine VM.

**Sweep flow:**
1. User configures sweep ranges in UI
2. Coordinator builds parameter grid (same as now)
3. For each combo, calls Python: 
   `python run_backtest.py --strategy mesa.py --data btc_5m.csv --params '{"fast_limit":0.38,...}' --mode fast`
4. Python returns JSON summary stats
5. Coordinator collects, sorts, filters, displays in Results table

**Detail drill-down flow:**
1. User clicks a result row
2. Coordinator re-runs that single combo in detail mode:
   `python run_backtest.py --strategy mesa.py --data btc_5m.csv --params '{"fast_limit":0.38,...}' --mode detail`
3. Python returns JSON with full trade list + equity curve
4. UI displays trade table + equity chart

**Performance optimization:**
- Batch mode: pass multiple param combos in one Python call to amortize startup
- Data pre-loading: Python process loads CSV once, runs N combos
- Worker pool: keep Python processes alive, feed them work via stdin/stdout
- Numpy vectorization: indicators computed once per combo, not per-bar

### 6. UI Changes (React)

**Strategy Lab tab — new flow:**

```
┌─────────────────────────────────────────┐
│  Upload Strategy                         │
│                                          │
│  [Pine Script ▼]  [Choose File...]       │
│                                          │
│  ┌─ Preview ──────────────────────────┐  │
│  │ # Generated Python                 │  │
│  │ class MesaMamaFama(Strategy):      │  │
│  │     params = {                     │  │
│  │         "fast_limit": Param(0.38), │  │
│  │         ...                        │  │
│  │     }                              │  │
│  │     def on_bar(self, bar, ctx):    │  │
│  │         ...                        │  │
│  └────────────────────────────────────┘  │
│  [Edit] [Save as .py]                    │
│                                          │
│  ┌─ Parameters ───────────────────────┐  │
│  │ Fast Limit    [0.38] → [0.30:0.50] │  │
│  │ Slow Limit    [0.035]→ [0.02:0.05] │  │
│  │ ...                                │  │
│  └────────────────────────────────────┘  │
│                                          │
│  Initial Capital: [$1,000,000]           │
│  Fee %: [0.04]                           │
│  [Run Sweep]                             │
└──────────────────────────────────────────┘
```

**Results tab — new trade detail panel:**

```
┌─────────────────────────────────────────────────────────┐
│  Results                              [Export CSV]       │
│  ┌───┬──────┬──────────┬──────────┬───────┬──────────┐  │
│  │ # │ PF   │ Net $    │ Max DD $ │ Win % │ Trades   │  │
│  ├───┼──────┼──────────┼──────────┼───────┼──────────┤  │
│  │ 1 │ 1.60 │ $44,471  │ $15,256  │ 55.9% │ 598  [→] │  │
│  │ 2 │ 1.45 │ $38,200  │ $12,100  │ 54.2% │ 612  [→] │  │
│  └───┴──────┴──────────┴──────────┴───────┴──────────┘  │
│                                                          │
│  ▼ Trade Detail (Result #1)                              │
│  ┌───┬───────┬────────┬────────┬──────┬────────┬──────┐ │
│  │ # │ Dir   │ Entry  │ Exit   │ Qty  │ P&L    │ Bars │ │
│  ├───┼───────┼────────┼────────┼──────┼────────┼──────┤ │
│  │ 1 │ Long  │ 90,483 │ 90,077 │ 5.53 │ -2,240 │  49  │ │
│  │ 2 │ Short │ 89,485 │ 90,030 │ 5.57 │ -3,034 │  95  │ │
│  └───┴───────┴────────┴────────┴──────┴────────┴──────┘ │
│                                                          │
│  ┌─ Equity Curve ─────────────────────────────────────┐  │
│  │  📈 [line chart showing equity over time]          │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

---

## File Structure

```
strategy-optimizer/
├── src/                          # React frontend (existing)
│   ├── App.jsx
│   └── components/
│       ├── StrategyLab.jsx       # Modified: upload selector, preview
│       ├── DataFetcher.jsx       # Unchanged
│       └── Results.jsx           # Modified: trade detail panel
│
├── src-tauri/                    # Rust backend
│   ├── src/
│   │   ├── main.rs              # Modified: Python subprocess calls
│   │   ├── bin/worker.rs        # Modified: Python subprocess calls  
│   │   └── pine/                # Keep existing parser for translator
│   │       ├── tokenizer.rs
│   │       ├── parser.rs
│   │       ├── ast.rs
│   │       ├── input_extractor.rs
│   │       └── python_emitter.rs  # NEW: AST → Python translator
│   │
│   └── python/                   # NEW: Python backtester
│       ├── backtest_engine.py    # Core engine (~300 lines)
│       ├── strategy_base.py      # Strategy API base class
│       ├── ta.py                 # Technical analysis library
│       ├── run_backtest.py       # CLI entry point for Rust to call
│       └── strategies/           # Generated/uploaded strategy files
│           └── mesa_mama_fama.py # Hand-translated MESA strategy
│
├── data/                         # Existing data directory
│   ├── crypto/
│   ├── futures/
│   └── stocks/
│
└── fetchers/                     # Existing Python fetchers
    ├── crypto_fetcher.py
    ├── futures_fetcher.py
    └── alpaca_fetcher.py
```

---

## Build Priority

### Phase 1: Python Backtester + MESA Validation (THIS SPRINT)
1. `backtest_engine.py` — core engine with fast and detail modes
2. `strategy_base.py` — strategy API
3. `ta.py` — EMA, ATR, crossover (just what MESA needs)
4. `mesa_mama_fama.py` — hand-translated MESA strategy
5. Validate against TradingView: match trade count, P&L, win rate
6. `run_backtest.py` — CLI wrapper for Rust to call

### Phase 2: Wire into Tauri App
7. Modify `main.rs` to call Python subprocess instead of Pine VM
8. Modify `worker.rs` same way
9. Update `StrategyLab.jsx` — add Python upload path
10. Update `Results.jsx` — add trade detail drill-down

### Phase 3: Pine → Python Translator  
11. `python_emitter.rs` — AST walker that emits Python
12. Wire into UI: upload .pine → show generated .py → edit → run
13. Test with MESA script + other sample scripts

### Phase 4: Polish
14. Batch mode for Python (multiple combos per process)
15. Worker pool (persistent Python processes)
16. Equity curve charting in Results
17. Strategy template library

---

## Performance Estimates

| Component | Time | Notes |
|-----------|------|-------|
| Python startup | ~200ms | One-time per worker process |
| CSV load (100K bars) | ~50ms | numpy fromfile |
| MESA indicator compute | ~20ms | Vectorized numpy |
| Per-bar strategy loop | ~100ms/100K bars | Pure Python loop |
| **Total per combo** | **~170ms** | After startup |
| **1M combos × 3 workers** | **~16 hours** | Unoptimized |
| **With batch mode (100/batch)** | **~10 hours** | Amortize startup |
| **With persistent workers** | **~8 hours** | No startup overhead |

For faster sweeps: the per-bar loop is the bottleneck. Options:
- Cython/numba JIT for the hot loop → 10-50x faster
- Rust backtester with Python strategy compiled to a config → fastest
- GPU acceleration for indicator computation → diminishing returns

Phase 1 targets correctness over speed. Optimize in Phase 4.

---

## Key Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Python per-combo too slow for large sweeps | Batch mode, persistent workers, Cython later |
| Pine translator can't handle all scripts | Document limitations, let users edit generated Python |
| MESA math doesn't match TradingView | Validate trade-by-trade against TV export |
| Trade detail re-run is slow | Cache results, or store trade logs for top N |
| Windows Python path issues | Bundle Python or use existing detection from data fetchers |
