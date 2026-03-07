"""Profile the backtester to find speed bottlenecks."""
import sys, os, time, cProfile, pstats, io
sys.path.insert(0, os.path.join(os.environ.get("APPDATA", ""), "StrategyOptimizer"))
sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))

from backtest_engine import Bars, BacktestEngine

# Load data
data_dir = os.path.join(os.environ.get("APPDATA", ""), "StrategyOptimizer", "data", "crypto")
bars = Bars.from_csv(os.path.join(data_dir, "BTCUSDT_5m_365d_bt.csv"))
print(f"Bars: {len(bars)}")

# Use MESA strategy (most representative)
from strategies.mesa_mama_fama import MesaMamaFama

# Time a single run first
t0 = time.perf_counter()
stats = BacktestEngine.run_fast(MesaMamaFama(), bars, {"src": "hl2"}, 1000000, 0.0)
t1 = time.perf_counter()
print(f"Single run: {t1-t0:.2f}s, {stats.total_trades} trades")
print(f"Rate: {1/(t1-t0):.2f} combos/sec")
print()

# Profile
print("Profiling...")
pr = cProfile.Profile()
pr.enable()
BacktestEngine.run_fast(MesaMamaFama(), bars, {"src": "hl2"}, 1000000, 0.0)
pr.disable()

s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
ps.print_stats(20)
print(s.getvalue())

# Also profile by tottime (self time, not including subcalls)
s2 = io.StringIO()
ps2 = pstats.Stats(pr, stream=s2).sort_stats("tottime")
ps2.print_stats(20)
print("=== By self time ===")
print(s2.getvalue())
