"""Find first bar where LW translated and hand-written diverge."""
import sys, os
sys.path.insert(0, os.path.join(os.environ.get("APPDATA", ""), "StrategyOptimizer"))
sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from backtest_engine import Bars, Context, BarData
from strategy_base import ParamAccessor

data_dir = os.path.join(os.environ.get("APPDATA", ""), "StrategyOptimizer", "data", "crypto")
bars = Bars.from_csv(os.path.join(data_dir, "BTCUSDT_5m_365d_bt.csv"))

from strategies.larry_williams_combined_system import LarryWilliamsCombinedSystem
from strategies.larry_williams import LarryWilliamsCombined

s1 = LarryWilliamsCombinedSystem()
ctx1 = Context(bars, 1000000, 0.0)
s1.p = ParamAccessor(s1.get_param_defaults())
s1.init(ctx1)

s2 = LarryWilliamsCombined()
ctx2 = Context(bars, 1000000, 0.0)
s2.p = ParamAccessor(s2.get_param_defaults())
s2.init(ctx2)

pc = 0.0
diverge_count = 0
for i in range(len(bars)):
    bar = BarData(int(bars.timestamp[i]), bars.open[i], bars.high[i], bars.low[i], bars.close[i], bars.volume[i])
    
    ctx1.bar_index = i
    ctx1._fill_pending_orders(bar, prev_bar_close=pc)
    s1.on_bar(bar, ctx1)
    ctx1._process_orders(bar)
    
    ctx2.bar_index = i
    ctx2._fill_pending_orders(bar, prev_bar_close=pc)
    s2.on_bar(bar, ctx2)
    ctx2._process_orders(bar)
    
    t_pos = ctx1.position_size
    h_pos = ctx2.position_size
    t_flat = t_pos == 0
    h_flat = h_pos == 0
    
    # Only report when one enters/exits and other doesn't
    if (t_flat != h_flat):
        diverge_count += 1
        if diverge_count <= 5:
            t_dir = "long" if t_pos > 0 else ("short" if t_pos < 0 else "flat")
            h_dir = "long" if h_pos > 0 else ("short" if h_pos < 0 else "flat")
            print(f"Bar {i}: T={t_dir}({t_pos:.1f}) H={h_dir}({h_pos:.1f})")
            
            if diverge_count == 1:
                p = s1.p
                wr_t = s1._f_simpleRange(bar, ctx1, bars, i, p)
                print(f"  workingRange={wr_t:.2f}")
                print(f"  open={bars.open[i]:.2f} close={bars.close[i]:.2f}")
                buyEntry = bars.open[i] + wr_t * p.buy_mult
                sellEntry = bars.open[i] - wr_t * p.sell_mult
                print(f"  buyEntry={buyEntry:.2f} sellEntry={sellEntry:.2f}")
                try: print(f"  patSTLow={s1._f_isSTLow(bar, ctx1, bars, i, p)}")
                except Exception as e: print(f"  patSTLow=ERR:{e}")
                try: print(f"  consecDown={s1._f_consecDown(bar, ctx1, bars, i, p, int(p.consec_bear_bars))}")
                except Exception as e: print(f"  consecDown=ERR:{e}")
                try: print(f"  smashBuy={s1._f_smashDayBuy(bar, ctx1, bars, i, p)}")
                except Exception as e: print(f"  smashBuy=ERR:{e}")
                try: print(f"  pullback={s1._f_pullbackInUptrend(bar, ctx1, bars, i, p)}")
                except Exception as e: print(f"  pullback=ERR:{e}")
                try: print(f"  outsideBar={s1._f_outsideBarDownClose(bar, ctx1, bars, i, p)}")
                except Exception as e: print(f"  outsideBar=ERR:{e}")
    
    pc = bar.close

print(f"\nTotal divergent bars: {diverge_count}")
print(f"T_trades={len(ctx1._trades)} H_trades={len(ctx2._trades)}")
