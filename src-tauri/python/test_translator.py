"""Regression test suite for Pine Script translator.

Run: python test_translator.py
     python test_translator.py --verbose

Tests:
1. All known Pine scripts translate without errors
2. Translated strategies import and run without crashes
3. Trade counts match hand-written baselines exactly
4. PF matches within tolerance
5. Key translator patterns are correctly emitted (_prev_pos, qty_func, NaN guards, etc.)
"""
import sys
import os
import re
import importlib
import traceback
import numpy as np

# Setup paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STRATEGIES_DIR = os.path.join(SCRIPT_DIR, "strategies")

# AppData strategies must come first (same as runtime)
_appdata = os.environ.get("APPDATA", "")
if _appdata:
    _strat_parent = os.path.join(_appdata, "StrategyOptimizer")
    if os.path.isdir(os.path.join(_strat_parent, "strategies")):
        conflicting = [p for p in sys.path if p != _strat_parent and os.path.isdir(os.path.join(p, "strategies"))]
        remaining = [p for p in sys.path if p not in conflicting and p != _strat_parent]
        sys.path = [_strat_parent] + remaining + conflicting
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from backtest_engine import Bars, BacktestEngine
from strategy_base import ParamAccessor

VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv

# ══════════════════════════════════════════════════════════════
# Test Configuration
# ══════════════════════════════════════════════════════════════

# Pine files to translate (relative to repo root)
PINE_DIR = os.path.join(SCRIPT_DIR, "..", "..", "strategies")

STRATEGIES = [
    {
        "pine_file": "lw_combined.pine",
        "output_name": "lw_regression_test",
        "hand_written": "larry_williams.LarryWilliamsCombined",
        "hand_params": {},
        "data": "crypto",  # which dataset to use
        "expected_trades": 10462,
        "expected_pf": 0.9545,
        "trade_tolerance": 0,      # exact match
        "pf_tolerance": 0.001,
        "needs_claude": False,
    },
    {
        "pine_file": "pure_orb.pine",
        "output_name": "pure_orb_regression_test",
        "hand_written": "pure_orb.PureORB",
        "hand_params": {},
        "data": "stocks",
        "expected_trades": 249,
        "expected_pf": 1.0485,
        "trade_tolerance": 0,
        "pf_tolerance": 0.02,  # ORB has small exit timing differences
        "needs_claude": False,
    },
    {
        "pine_file": "mesa_mama_fama_crypto.pine",
        "output_name": "mesa_regression_test",
        "hand_written": "mesa_mama_fama.MesaMamaFama",
        "hand_params": {"src": "hl2"},
        "data": "crypto",
        "expected_trades": 594,
        "expected_pf": 1.2052,
        "trade_tolerance": 0,
        "pf_tolerance": 0.001,
        "needs_claude": True,  # Needs Claude for recursive MESA indicator
    },
]

# Data files
DATA_PATHS = {
    "crypto": None,  # Set dynamically
    "stocks": None,
}

# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

def find_data():
    """Find data files in AppData or local data dir."""
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        crypto_dir = os.path.join(appdata, "StrategyOptimizer", "data", "crypto")
        stocks_dir = os.path.join(appdata, "StrategyOptimizer", "data", "stocks")
        for f in os.listdir(crypto_dir) if os.path.isdir(crypto_dir) else []:
            if f.endswith(".csv") and "365d" in f:
                DATA_PATHS["crypto"] = os.path.join(crypto_dir, f)
                break
        for f in os.listdir(stocks_dir) if os.path.isdir(stocks_dir) else []:
            if f.endswith(".csv") and "365d" in f:
                DATA_PATHS["stocks"] = os.path.join(stocks_dir, f)
                break


def load_bars(data_key):
    """Load bars for a dataset."""
    path = DATA_PATHS.get(data_key)
    if not path or not os.path.exists(path):
        return None
    return Bars.from_csv(path)


def import_class(module_class_str):
    """Import 'module.ClassName' from strategies package."""
    mod_name, cls_name = module_class_str.rsplit(".", 1)
    full_mod = f"strategies.{mod_name}"
    # Clear cached strategies package to ensure correct path resolution
    for key in list(sys.modules.keys()):
        if key.startswith("strategies"):
            del sys.modules[key]
    mod = importlib.import_module(full_mod)
    return getattr(mod, cls_name)


def translate_pine(pine_file, output_name):
    """Run the deterministic translator."""
    from pine_translator import translate
    pine_path = os.path.join(PINE_DIR, pine_file)
    if not os.path.exists(pine_path):
        return False, f"Pine file not found: {pine_path}"
    try:
        with open(pine_path, "r", encoding="utf-8") as f:
            pine_code = f.read()
        python_code, metadata = translate(pine_code, output_name, api_key=None, use_cache=False)
        # Save to AppData strategies dir (same as runtime)
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            out_dir = os.path.join(appdata, "StrategyOptimizer", "strategies")
        else:
            out_dir = STRATEGIES_DIR
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{output_name}.py")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(python_code)
        # Clear pycache
        cache_dir = os.path.join(out_dir, "__pycache__")
        if os.path.isdir(cache_dir):
            import shutil
            shutil.rmtree(cache_dir, ignore_errors=True)
        source = metadata.get("source", "unknown")
        unresolved = metadata.get("unresolved", 0)
        return True, f"source={source}, unresolved={unresolved}"
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════
# Pattern Tests (check emitted code has required patterns)
# ══════════════════════════════════════════════════════════════

def check_patterns(code, strategy_name):
    """Verify key patterns are present in translated code."""
    issues = []
    
    # 1. _prev_pos tracking
    if "self._prev_pos" not in code:
        issues.append("Missing self._prev_pos tracking")
    if "prev_position_size = self._prev_pos" not in code:
        issues.append("Missing prev_position_size = self._prev_pos")
    if "self._prev_pos = ctx.position_size" not in code:
        issues.append("Missing self._prev_pos = ctx.position_size at end of on_bar")
    
    # 2. Position state preamble
    if "isLong = ctx.position_size > 0" not in code:
        issues.append("Missing isLong preamble")
    if "isFlat = ctx.position_size == 0" not in code:
        issues.append("Missing isFlat preamble")
    if "newEntry = " not in code:
        issues.append("Missing newEntry detection")
    
    # 3. Warmup guard
    if not re.search(r"if i < \d+:", code):
        issues.append("Missing warmup guard (if i < N)")
    
    # 4. qty_func on entries
    entry_lines = [l for l in code.split("\n") if "ctx.entry(" in l]
    for el in entry_lines:
        if "qty_func" not in el:
            issues.append(f"Missing qty_func on entry: {el.strip()[:60]}")
    
    # 5. NaN-safe comparisons (if indicators are present)
    if "regimeEma" in code or "regime_ema" in code:
        if "np.isnan" not in code:
            issues.append("Missing NaN guard on indicator comparison")
    
    # 6. Helper functions use _bi not i for bar index
    helper_defs = re.findall(r"def (_f_\w+)\(self, bar, ctx, bars, (\w+)", code)
    for func_name, param in helper_defs:
        if param != "_bi":
            issues.append(f"Helper {func_name} uses '{param}' instead of '_bi' for bar index")
    
    # 7. Date change detection
    if "isNewDay" not in code:
        issues.append("Missing isNewDay date-change detection")
    
    return issues


# ══════════════════════════════════════════════════════════════
# Main Test Runner
# ══════════════════════════════════════════════════════════════

def run_tests():
    find_data()
    
    total = 0
    passed = 0
    failed = 0
    skipped = 0
    
    print("=" * 70)
    print("Pine Script Translator Regression Tests")
    print("=" * 70)
    
    for strat in STRATEGIES:
        name = strat["pine_file"]
        print(f"\n── {name} ──")
        
        # Skip Claude-dependent tests if no API key
        if strat["needs_claude"]:
            print(f"  [SKIP] Needs Claude API (test deterministic patterns only)")
            # Still check patterns on the deterministic output
            ok, result = translate_pine(strat["pine_file"], strat["output_name"])
            if ok:
                appdata = os.environ.get("APPDATA", "")
                if appdata:
                    code_path = os.path.join(appdata, "StrategyOptimizer", "strategies", f"{strat['output_name']}.py")
                else:
                    code_path = os.path.join(STRATEGIES_DIR, f"{strat['output_name']}.py")
                code = open(code_path).read()
                issues = check_patterns(code, name)
                if issues:
                    for iss in issues:
                        print(f"  [WARN] Pattern: {iss}")
                else:
                    print(f"  [OK] All patterns present in deterministic output")
            else:
                print(f"  [FAIL] Translation error: {result}")
                failed += 1
            skipped += 1
            continue
        
        # Test 1: Translate
        total += 1
        ok, result = translate_pine(strat["pine_file"], strat["output_name"])
        if not ok:
            print(f"  [FAIL] Translation: {result}")
            failed += 1
            continue
        print(f"  [OK] Translated ({result})")
        
        # Test 2: Import
        total += 1
        try:
            mod_name = f"strategies.{strat['output_name']}"
            if mod_name in sys.modules:
                del sys.modules[mod_name]
            mod = importlib.import_module(mod_name)
            cls = None
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, type) and attr.__name__ != "Strategy" and hasattr(attr, "params"):
                    cls = attr
                    break
            if cls is None:
                print(f"  [FAIL] No Strategy subclass found")
                failed += 1
                continue
            print(f"  [OK] Imported {cls.__name__} ({len(cls.params)} params)")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] Import: {e}")
            failed += 1
            continue
        
        # Test 3: Pattern checks
        total += 1
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            code_path = os.path.join(appdata, "StrategyOptimizer", "strategies", f"{strat['output_name']}.py")
        else:
            code_path = os.path.join(STRATEGIES_DIR, f"{strat['output_name']}.py")
        code = open(code_path).read()
        issues = check_patterns(code, name)
        if issues:
            for iss in issues:
                print(f"  [FAIL] Pattern: {iss}")
            failed += 1
        else:
            print(f"  [OK] All required patterns present")
            passed += 1
        
        # Test 4: Run backtest
        total += 1
        bars = load_bars(strat["data"])
        if bars is None:
            print(f"  [SKIP] No {strat['data']} data available")
            skipped += 1
            continue
        
        try:
            stats = BacktestEngine.run_fast(cls(), bars, {}, 1000000, 0.0)
            print(f"  [OK] Backtest: {stats.total_trades} trades, PF={stats.profit_factor:.4f}")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] Backtest crashed: {e}")
            if VERBOSE:
                traceback.print_exc()
            failed += 1
            continue
        
        # Test 5: Compare with hand-written baseline
        total += 1
        try:
            hand_cls = import_class(strat["hand_written"])
            hand_stats = BacktestEngine.run_fast(hand_cls(), bars, strat["hand_params"], 1000000, 0.0)
            
            trade_diff = abs(stats.total_trades - hand_stats.total_trades)
            pf_diff = abs(stats.profit_factor - hand_stats.profit_factor)
            
            trade_ok = trade_diff <= strat["trade_tolerance"]
            pf_ok = pf_diff <= strat["pf_tolerance"]
            
            if trade_ok and pf_ok:
                print(f"  [OK] Matches baseline: {stats.total_trades} trades (diff={trade_diff}), PF diff={pf_diff:.4f}")
                passed += 1
            else:
                reasons = []
                if not trade_ok:
                    reasons.append(f"trades {stats.total_trades} vs {hand_stats.total_trades} (diff={trade_diff}, max={strat['trade_tolerance']})")
                if not pf_ok:
                    reasons.append(f"PF {stats.profit_factor:.4f} vs {hand_stats.profit_factor:.4f} (diff={pf_diff:.4f}, max={strat['pf_tolerance']})")
                print(f"  [FAIL] Baseline mismatch: {'; '.join(reasons)}")
                failed += 1
        except Exception as e:
            print(f"  [FAIL] Baseline comparison: {e}")
            if VERBOSE:
                traceback.print_exc()
            failed += 1
    
    # Summary
    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped out of {total} tests")
    print("=" * 70)
    
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
