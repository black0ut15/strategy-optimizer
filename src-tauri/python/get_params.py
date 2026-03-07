"""Extract strategy parameter metadata as JSON.

Usage:
    python get_params.py <strategy_name>

Outputs JSON array of parameter info for the frontend UI.
"""

import json
import sys
import os

# Strategy files live in %APPDATA%/StrategyOptimizer/strategies/
# Engine files live in the script dir (src-tauri/python/)
# 
# Problem: Python auto-adds the script's directory to sys.path[0],
# and that directory has a strategies/ subpackage. We need the AppData
# strategies/ to take priority. Fix: ensure any path containing a 
# conflicting strategies/ package is moved AFTER AppData.

script_dir = os.path.dirname(os.path.abspath(__file__))
_appdata = os.environ.get("APPDATA") or os.environ.get("USERPROFILE", "")
_strat_parent = ""

if _appdata:
    _strat_parent = os.path.join(_appdata, "StrategyOptimizer")
    _strat_dir = os.path.join(_strat_parent, "strategies")
    
    if os.path.isdir(_strat_dir):
        # Remove all paths that have their own strategies/ subdir (to avoid package conflict)
        # Then re-add them after AppData
        conflicting = []
        remaining = []
        for p in sys.path:
            if p != _strat_parent and os.path.isdir(os.path.join(p, "strategies")):
                conflicting.append(p)
            else:
                remaining.append(p)
        
        # Rebuild: AppData first, then non-conflicting, then conflicting at end
        sys.path = [_strat_parent] + [p for p in remaining if p != _strat_parent] + conflicting
    else:
        # No AppData strategies yet — use source dir normally
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

# Ensure script_dir is in path (for engine imports like strategy_base, backtest_engine)
if script_dir not in sys.path:
    sys.path.append(script_dir)


def get_strategy_params(strategy_name: str) -> list:
    """Import a strategy and return its parameter metadata."""
    name_lower = strategy_name.lower().replace(" ", "_").replace("-", "_")

    strategy_map = {
        "mesa_mama_fama": ("strategies.mesa_mama_fama", "MesaMamaFama"),
        "larry_williams": ("strategies.larry_williams", "LarryWilliamsCombined"),
        "pure_orb": ("strategies.pure_orb", "PureORB"),
    }

    if name_lower in strategy_map:
        module_name, class_name = strategy_map[name_lower]
    else:
        module_name = f"strategies.{name_lower}"
        class_name = "".join(w.capitalize() for w in name_lower.split("_"))

    import importlib
    module = importlib.import_module(module_name)

    cls = getattr(module, class_name, None)
    if cls is None:
        from strategy_base import Strategy
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and issubclass(attr, Strategy)
                    and attr is not Strategy):
                cls = attr
                break

    if cls is None:
        raise ValueError(f"No Strategy subclass found in '{module_name}'")

    return cls.get_param_info()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python get_params.py <strategy_name>", file=sys.stderr)
        sys.exit(1)

    params = get_strategy_params(sys.argv[1])
    print(json.dumps(params))
