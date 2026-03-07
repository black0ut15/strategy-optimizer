"""Trace both strategies bar by bar to find first divergence."""
import sys, os
sys.path.insert(0, os.path.join(os.environ.get("APPDATA", ""), "StrategyOptimizer"))
sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from backtest_engine import Bars, BacktestEngine, Context, BarData
from strategy_base import ParamAccessor

data_dir = os.path.join(os.environ.get("APPDATA", ""), "StrategyOptimizer", "data", "crypto")
bars = Bars.from_csv(os.path.join(data_dir, "BTCUSDT_5m_365d_bt.csv"))

from strategies.mesa_mama_fama_crypto_v33_btc import MesaMamaFamaCryptoV33Btc
from strategies.mesa_mama_fama import MesaMamaFama

# Set up both strategies
s1 = MesaMamaFamaCryptoV33Btc()
ctx1 = Context(bars, 1000000, 0.0)
s1.p = ParamAccessor(s1.get_param_defaults())
s1.init(ctx1)

s2 = MesaMamaFama()
ctx2 = Context(bars, 1000000, 0.0)
s2.p = ParamAccessor(s2.get_param_defaults())
s2.init(ctx2)

# Verify indicators match
print("=== Indicator Comparison ===")
for idx in [100, 500, 1000, 5000]:
    m1 = s1.mama[idx]
    f1 = s1.fama[idx]
    m2 = s2.mama[idx] if hasattr(s2, 'mama') else 0
    f2 = s2.fama[idx] if hasattr(s2, 'fama') else 0
    print(f"Bar {idx}: T_mama={m1:.2f} H_mama={m2:.2f} diff={abs(m1-m2):.4f}  T_fama={f1:.2f} H_fama={f2:.2f}")

# Check crossover arrays
cu1 = s1.mamaCrossUp
cu2 = s2.mama_cross_up if hasattr(s2, 'mama_cross_up') else None
if cu2 is not None:
    mismatches = np.sum(cu1 != cu2)
    print(f"\nCrossover Up mismatches: {mismatches} out of {len(cu1)}")
    if mismatches > 0:
        first_mm = np.where(cu1 != cu2)[0][0]
        print(f"  First mismatch at bar {first_mm}: T={cu1[first_mm]} H={cu2[first_mm]}")

# Run both side by side
print("\n=== Bar-by-bar execution ===")
prev_close1 = 0.0
prev_close2 = 0.0

for i in range(min(500, len(bars))):
    bar = BarData(int(bars.timestamp[i]), bars.open[i], bars.high[i], bars.low[i], bars.close[i], bars.volume[i])
    
    # Strategy 1 (translated)
    ctx1.bar_index = i
    old_pos1 = ctx1.position_size
    ctx1._fill_pending_orders(bar, prev_bar_close=prev_close1)
    s1.on_bar(bar, ctx1)
    ctx1._process_orders(bar)
    new_pos1 = ctx1.position_size
    
    # Strategy 2 (hand-written)
    ctx2.bar_index = i
    old_pos2 = ctx2.position_size
    ctx2._fill_pending_orders(bar, prev_bar_close=prev_close2)
    s2.on_bar(bar, ctx2)
    ctx2._process_orders(bar)
    new_pos2 = ctx2.position_size
    
    # Report any position change or divergence
    pos1_changed = old_pos1 != new_pos1
    pos2_changed = old_pos2 != new_pos2
    
    if pos1_changed or pos2_changed:
        print(f"Bar {i} (close={bar.close:.2f}):")
        if pos1_changed:
            print(f"  Translated:   pos {old_pos1:.3f} -> {new_pos1:.3f}")
        if pos2_changed:
            print(f"  Hand-written: pos {old_pos2:.3f} -> {new_pos2:.3f}")
        if pos1_changed != pos2_changed:
            print(f"  *** DIVERGENCE: only {'translated' if pos1_changed else 'hand-written'} changed")
    
    # Check if pending signals differ
    t_pending_l = getattr(s1, '_pendingLong', None)
    t_pending_s = getattr(s1, '_pendingShort', None)
    h_pending_l = getattr(s2, '_pending_long', None)
    h_pending_s = getattr(s2, '_pending_short', None)
    
    if t_pending_l != h_pending_l or t_pending_s != h_pending_s:
        if i < 300:
            print(f"  Bar {i}: Pending diverge: T_long={t_pending_l} H_long={h_pending_l} T_short={t_pending_s} H_short={h_pending_s}")
    
    prev_close1 = bar.close
    prev_close2 = bar.close

print(f"\nFinal: T_pos={ctx1.position_size:.3f} H_pos={ctx2.position_size:.3f}")
print(f"T_trades={len(ctx1._trades)} H_trades={len(ctx2._trades)}")
