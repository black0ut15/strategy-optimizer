"""Check MESA indicator source and computation."""
import sys, os
sys.path.insert(0, os.path.join(os.environ.get("APPDATA", ""), "StrategyOptimizer"))
sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from backtest_engine import Bars, Context
from strategy_base import ParamAccessor
import ta as ta_lib

data_dir = os.path.join(os.environ.get("APPDATA", ""), "StrategyOptimizer", "data", "crypto")
bars = Bars.from_csv(os.path.join(data_dir, "BTCUSDT_5m_365d_bt.csv"))

from strategies.mesa_mama_fama_crypto_v33_btc import MesaMamaFamaCryptoV33Btc
from strategies.mesa_mama_fama import MesaMamaFama

# Check what source each uses
s1 = MesaMamaFamaCryptoV33Btc()
s1.p = ParamAccessor(s1.get_param_defaults())
print(f"Translated mesa_src default: {s1.p.mesa_src}")

s2 = MesaMamaFama()
s2.p = ParamAccessor(s2.get_param_defaults())
# Check hand-written source param name
for k, v in s2.get_param_defaults().items():
    if 'src' in k.lower() or 'mesa' in k.lower() or 'source' in k.lower():
        print(f"Hand-written param: {k} = {v}")

# Compute MESA with same source and compare
source_hl2 = bars.hl2
print(f"\nSource hl2[0:5]: {source_hl2[:5]}")
print(f"Source hl2 shape: {source_hl2.shape}")

# Direct computation
mama1, fama1 = ta_lib.mesa_mama_fama(source_hl2, 0.38, 0.035)
print(f"\nDirect MESA (hl2, 0.38, 0.035):")
print(f"  mama[100]={mama1[100]:.6f} mama[500]={mama1[500]:.6f}")
print(f"  fama[100]={fama1[100]:.6f} fama[500]={fama1[500]:.6f}")

# Now init both and check what they computed
ctx1 = Context(bars, 1000000, 0.0)
s1.init(ctx1)
print(f"\nTranslated init:")
print(f"  mama[100]={s1.mama[100]:.6f} mama[500]={s1.mama[500]:.6f}")
print(f"  fama[100]={s1.fama[100]:.6f} fama[500]={s1.fama[500]:.6f}")

ctx2 = Context(bars, 1000000, 0.0)
s2.init(ctx2)
print(f"\nHand-written init:")
print(f"  mama[100]={s2.mama[100]:.6f} mama[500]={s2.mama[500]:.6f}")
print(f"  fama[100]={s2.fama[100]:.6f} fama[500]={s2.fama[500]:.6f}")

# Check if get_source returns same thing
src_via_get = bars.get_source("hl2")
print(f"\nbars.get_source('hl2') == bars.hl2: {np.array_equal(src_via_get, bars.hl2)}")

# Check if hand-written uses a different source
# Look at the init code
import inspect
init_src = inspect.getsource(s2.init)
for line in init_src.split('\n'):
    if 'mesa' in line.lower() or 'mama' in line.lower() or 'source' in line.lower() or 'get_source' in line.lower():
        print(f"H init: {line.strip()}")
