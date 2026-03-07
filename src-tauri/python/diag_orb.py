"""Deep ORB diagnostic."""
import sys, os
sys.path.insert(0, os.path.join(os.environ.get("APPDATA", ""), "StrategyOptimizer"))
sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from backtest_engine import Bars, Context, BarData
from strategy_base import ParamAccessor

data_dir = os.path.join(os.environ.get("APPDATA", ""), "StrategyOptimizer", "data", "stocks")
bars = Bars.from_csv(os.path.join(data_dir, "SPY_5Min_365d_bt.csv"))

from strategies.pure_orb_mes_3ct import PureOrbMes3ct
s = PureOrbMes3ct()
ctx = Context(bars, 1000000, 0.0)
s.p = ParamAccessor(s.get_param_defaults())
s.init(ctx)

p = s.p
pc = 0.0
for i in range(min(200, len(bars))):
    bar = BarData(int(bars.timestamp[i]), bars.open[i], bars.high[i], bars.low[i], bars.close[i], bars.volume[i])
    ctx.bar_index = i
    ctx._fill_pending_orders(bar, prev_bar_close=pc)
    
    # Check state BEFORE on_bar
    in_session = not np.isnan(s._pine_time("5", p.i_cash_session))
    is_flatten = not np.isnan(s._pine_time("5", p.i_flatten_session))
    in_entry = not np.isnan(s._pine_time("5", p.i_entry_session))
    
    s.on_bar(bar, ctx)
    ctx._process_orders(bar)
    
    # Report key state changes
    if i < 15 or (hasattr(s, '_orLocked') and s._orLocked and i < 20):
        print("Bar %d: orLocked=%s orHigh=%.2f orLow=%.2f orBarCount=%s" % (
            i, s._orLocked, s._orHigh, s._orLow, 
            getattr(s, '_orBarCount', '?')))
        print("  inSession=%s inEntry=%s isFlatten=%s isFlat=%s trades=%d" % (
            in_session, in_entry, is_flatten, ctx.position_size == 0, s._tradesToday))
        print("  canTrade=%s" % (
            s._orLocked and in_session and in_entry and not is_flatten 
            and ctx.position_size == 0 and s._tradesToday < p.i_max_trades_per_day))
        if s._orLocked and in_entry and not is_flatten:
            long_entry = s._orHigh + getattr(p, 'i_or_pad', 0)
            short_entry = s._orLow - getattr(p, 'i_or_pad', 0)
            print("  longEntry=%.2f shortEntry=%.2f close=%.2f" % (long_entry, short_entry, bar.close))
            print("  close>longEntry=%s close<shortEntry=%s" % (bar.close > long_entry, bar.close < short_entry))
    
    if len(ctx._pending_entries) > 0:
        print("  *** ENTRY QUEUED at bar %d" % i)
    
    pc = bar.close

print("\nFinal: trades=%d pos=%.3f" % (len(ctx._trades), ctx.position_size))
print("Params: or_minutes=%s max_trades=%s direction=%s" % (
    getattr(p, 'i_or_minutes', '?'), 
    getattr(p, 'i_max_trades_per_day', '?'),
    getattr(p, 'i_direction', '?')))
