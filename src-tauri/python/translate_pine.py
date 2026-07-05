"""
Pine Script → Python Strategy Translator

Uses Claude API to translate TradingView Pine Script strategies
into Python strategy classes compatible with the backtester framework.

Usage:
  python translate_pine.py strategy.pine              # outputs strategy.py
  python translate_pine.py strategy.pine -o my_strat  # outputs my_strat.py
  
  # Or via stdin (used by Rust IPC):
  echo '{"pine_code":"...","name":"my_strategy"}' | python translate_pine.py --stdin
"""

import os
import sys
import json
import argparse
import re

# Add parent dir for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


SYSTEM_PROMPT = r"""You are a Pine Script to Python translator for a trading strategy backtester.

You translate TradingView Pine Script strategies into Python classes that inherit from the Strategy base class.

## OUTPUT FORMAT

You must output ONLY valid Python code. No markdown, no explanations, no backticks. Just the .py file contents.

## FRAMEWORK API

```python
from strategy_base import Strategy, Param
from backtest_engine import BarData, Context, Bars
import numpy as np
import ta as ta_lib  # technical analysis library

class MyStrategy(Strategy):
    params = {
        "fast_limit": Param(0.38, min=0.01, max=1.0, step=0.01, group="Settings"),
        "use_filter": Param(True, group="Filters"),
        "mode": Param("Both", options=["Both", "Long only", "Short only"], group="Settings"),
    }
    
    def init(self, ctx: Context) -> None:
        """Pre-compute ALL indicators here using vectorized numpy operations.
        Called once before bar loop. Access bars via ctx.bars."""
        bars = ctx.bars
        p = self.p
        self.ema = ta_lib.ema(bars.close, 20)
        self.atr = ta_lib.atr(bars.high, bars.low, bars.close, 14)
    
    def on_bar(self, bar: BarData, ctx: Context) -> None:
        """Called each bar. Access current values via self.indicator[ctx.bar_index].
        Use ctx.entry(), ctx.close(), ctx.close_all(), ctx.exit() for orders."""
        i = ctx.bar_index
        p = self.p
        # ... trading logic ...
```

## AVAILABLE TA FUNCTIONS (in ta_lib)

- ta_lib.ema(source, length) -> np.ndarray
- ta_lib.sma(source, length) -> np.ndarray  
- ta_lib.atr(high, low, close, length) -> np.ndarray
- ta_lib.rma(source, length) -> np.ndarray
- ta_lib.crossover(a, b) -> np.ndarray (boolean)
- ta_lib.crossunder(a, b) -> np.ndarray (boolean)
- ta_lib.mesa_mama_fama(source, fast_limit, slow_limit) -> Tuple[np.ndarray, np.ndarray]
- ta_lib.nz(arr, replacement=0.0) -> np.ndarray

If a ta function doesn't exist, implement it inline in init() using numpy.

## CONTEXT API (ctx)

Properties:
- ctx.bars - Bars object with .open, .high, .low, .close, .volume, .hl2, .hlc3, .ohlc4, .hlcc4, .timestamp arrays
- ctx.bars.get_source("ohlc4") - get source array by name
- ctx.bar_index - current bar number (int)
- ctx.position_size - current position (>0 long, <0 short, 0 flat)
- ctx.position_avg_price - average entry price
- ctx.strategy_equity - equity including unrealized P&L
- ctx.equity - realized equity only

Methods:
- ctx.entry(id: str, direction: str, qty: float, qty_func=None) - queue entry order
  direction is "long" or "short"
- ctx.close(id: str, comment: str = "") - close named position
- ctx.close_all(comment: str = "") - close all positions
- ctx.exit(id: str, from_entry: str = "", stop: float = None, limit: float = None) - set bracket orders

## BAR DATA (bar)
- bar.open, bar.high, bar.low, bar.close, bar.volume, bar.timestamp

## TRANSLATION RULES

1. Pine `input.float(default, "Title", ...)` → `Param(default, min=..., max=..., step=..., group="...")`
2. Pine `input.bool(default, "Title")` → `Param(default, group="...")`  
3. Pine `input.string(default, "Title", options=[...])` → `Param(default, options=[...], group="...")`
4. Pine `input.source(hl2, "Title")` → `Param("hl2", options=["close","open","high","low","hl2","hlc3","ohlc4","hlcc4"], group="...")`
5. Pine `var float x = na` → instance variable in init(): `self.x = 0.0` (or np.nan)
6. Pine `x := value` → `self.x = value` (state variables) or local assignment
7. Pine `close[1]` → `bars.close[i-1]` (in init use array slicing, in on_bar use index)
8. Pine `ta.crossover(a, b)` → `ta_lib.crossover(a, b)` in init, `self.cross_arr[i]` in on_bar
9. Pine `strategy.entry("Long", strategy.long, qty=q)` → `ctx.entry("Long", "long", qty=q)`
10. Pine `strategy.close("Long")` → `ctx.close("Long")`
11. Pine `strategy.close_all()` → `ctx.close_all()`
12. Pine `strategy.exit("XL", "Long", stop=s, limit=l)` → `ctx.exit("XL", from_entry="Long", stop=s, limit=l)`
13. Pine `strategy.position_size` → `ctx.position_size`
14. Pine `strategy.equity` → `ctx.strategy_equity`
15. Pine `nz(x)` → use ta_lib.nz for arrays, or `(x if not np.isnan(x) else 0)` for scalars
16. Pine `na` → `np.nan`
17. Pine `math.abs(x)` → `abs(x)` or `np.abs(x)`
18. Pine `math.max(a,b)` → `max(a,b)` or `np.maximum(a,b)`
19. Pine `bar_index` → `ctx.bar_index`
20. Pine custom functions → translate inline or as helper methods

## IMPORTANT PATTERNS

### Indicator computation (always in init, vectorized):
```python
def init(self, ctx):
    bars = ctx.bars
    self.ema20 = ta_lib.ema(bars.close, self.p.ema_len)
    self.atr = ta_lib.atr(bars.high, bars.low, bars.close, self.p.atr_len)
```

### Per-bar state tracking (use instance variables):
```python
def init(self, ctx):
    self._pending_long = False
    self._last_entry_bar = 0
    
def on_bar(self, bar, ctx):
    if some_condition:
        self._pending_long = True
```

### Skip visual-only code:
- plot(), plotshape(), bgcolor(), fill() → skip entirely
- label.new(), line.new(), box.new(), table.* → skip entirely
- alertcondition(), alert() → skip entirely
- color.* → skip entirely
- barstate.islast blocks → skip entirely
- str.tostring() → skip

### barstate.isconfirmed:
In backtesting on historical data, every bar is confirmed. So `if barstate.isconfirmed` blocks should have their contents kept but the condition removed.

### Pine `var` keyword:
`var float x = 0` means "initialize once, persist across bars". Translate to instance variable set in init().

### Position sizing with qty_func:
For strategies that size based on equity, pass a qty_func for fill-time recalculation:
```python
def _make_sizer(self, margin_pct, leverage, min_qty):
    def sizer(fill_price, equity):
        notional = equity * margin_pct * leverage
        qty = notional / fill_price if fill_price > 0 else 0
        return max(min_qty, np.floor(qty / min_qty) * min_qty)
    return sizer

# In on_bar:
ctx.entry("Long", "long", qty=qty, qty_func=self._make_sizer(0.1, 5, 0.001))
```

## OUTPUT

Produce a complete, runnable Python file. Include all imports at the top. Use descriptive comments matching the Pine Script's logic sections. Preserve parameter names from Pine Script where possible (converting to snake_case).
"""


def translate_pine_to_python(pine_code: str, strategy_name: str = "translated_strategy") -> str:
    """Translate Pine Script to Python using Claude API.
    
    Args:
        pine_code: The Pine Script source code
        strategy_name: Name for the output strategy class
        
    Returns:
        Python source code string
    """
    try:
        import requests
    except ImportError:
        raise RuntimeError("requests library required: pip install requests")
    
    # Check for API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        # Try reading from a config file
        config_path = os.path.join(SCRIPT_DIR, ".anthropic_key")
        if os.path.exists(config_path):
            api_key = open(config_path).read().strip()
    
    if not api_key:
        raise ValueError(
            "No Anthropic API key found. Set ANTHROPIC_API_KEY environment variable "
            "or create a .anthropic_key file in the python directory."
        )
    
    # Build the prompt
    user_prompt = f"""Translate this Pine Script strategy to a Python strategy class named `{_to_class_name(strategy_name)}`.

The file should be named `{strategy_name}.py` and work with the backtester framework described in the system prompt.

Here is the Pine Script:

```pine
{pine_code}
```

Remember: Output ONLY Python code, no markdown formatting or explanations."""

    # Call Claude API
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 16000,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": user_prompt}
            ],
        },
        timeout=120,
    )
    
    if response.status_code != 200:
        raise RuntimeError(f"Claude API error {response.status_code}: {response.text[:500]}")
    
    data = response.json()
    
    # Extract text content
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block["text"]
    
    # Clean up: remove markdown code fences if present
    text = text.strip()
    if text.startswith("```python"):
        text = text[len("```python"):].strip()
    if text.startswith("```"):
        text = text[3:].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    
    return text


def _to_class_name(name: str) -> str:
    """Convert snake_case to CamelCase."""
    return "".join(word.capitalize() for word in name.replace("-", "_").split("_"))


def _to_snake_case(name: str) -> str:
    """Convert a strategy title to snake_case filename."""
    # Remove version numbers and special chars
    clean = re.sub(r'[^\w\s]', '', name)
    clean = re.sub(r'\s+', '_', clean.strip())
    return clean.lower()


def main():
    parser = argparse.ArgumentParser(description="Translate Pine Script to Python strategy")
    parser.add_argument("pine_file", nargs="?", help="Path to .pine file")
    parser.add_argument("-o", "--output", type=str, default=None, help="Output name (without .py)")
    parser.add_argument("--stdin", action="store_true", help="Read JSON from stdin (IPC mode)")
    parser.add_argument("--api-key", type=str, default=None, help="Anthropic API key")
    args = parser.parse_args()
    
    if args.api_key:
        os.environ["ANTHROPIC_API_KEY"] = args.api_key
    
    if args.stdin:
        # IPC mode: read JSON from stdin, output JSON to stdout
        input_data = json.loads(sys.stdin.read())
        pine_code = input_data["pine_code"]
        name = input_data.get("name", "translated_strategy")
        
        if input_data.get("api_key"):
            os.environ["ANTHROPIC_API_KEY"] = input_data["api_key"]
        
        try:
            python_code = translate_pine_to_python(pine_code, name)
            
            # Save to strategies dir
            strategies_dir = os.path.join(SCRIPT_DIR, "strategies")
            os.makedirs(strategies_dir, exist_ok=True)
            filepath = os.path.join(strategies_dir, f"{name}.py")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(python_code)
            
            # Run post-translation validator
            validation_info = ""
            try:
                from validate_translation import validate_and_fix
                vresult = validate_and_fix(filepath, auto_fix=True)
                if vresult.fixes_applied:
                    validation_info += f"Auto-fixed {len(vresult.fixes_applied)} issues: " + "; ".join(vresult.fixes_applied)
                    # Re-read the fixed file
                    with open(filepath, "r", encoding="utf-8") as f:
                        python_code = f.read()
                if vresult.warnings:
                    validation_info += (" | " if validation_info else "") + f"Warnings: " + "; ".join(vresult.warnings)
            except Exception as ve:
                validation_info = f"Validator error: {ve}"
            
            output = {
                "success": True,
                "name": name,
                "class_name": _to_class_name(name),
                "filepath": filepath,
                "code": python_code,
                "lines": len(python_code.splitlines()),
                "validation": validation_info,
            }
            sys.stdout.write(json.dumps(output))
        except Exception as e:
            output = {
                "success": False,
                "error": str(e),
            }
            sys.stdout.write(json.dumps(output))
    
    else:
        # CLI mode
        if not args.pine_file:
            parser.print_help()
            return
        
        with open(args.pine_file, "r", encoding="utf-8") as f:
            pine_code = f.read()
        
        # Derive output name from filename
        name = args.output or _to_snake_case(
            os.path.splitext(os.path.basename(args.pine_file))[0]
        )
        
        print(f"Translating {args.pine_file} -> {name}.py ...")
        python_code = translate_pine_to_python(pine_code, name)
        
        # Save to strategies dir
        strategies_dir = os.path.join(SCRIPT_DIR, "strategies")
        os.makedirs(strategies_dir, exist_ok=True)
        filepath = os.path.join(strategies_dir, f"{name}.py")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(python_code)
        
        print(f"Saved {len(python_code.splitlines())} lines -> {filepath}")
        print(f"Class: {_to_class_name(name)}")
        
        # Run post-translation validator
        try:
            from validate_translation import validate_and_fix
            print(f"\nRunning post-translation validator...")
            vresult = validate_and_fix(filepath, auto_fix=True)
            print(vresult.summary())
            if vresult.fixes_applied:
                print(f"\n  File updated with {len(vresult.fixes_applied)} auto-fixes.")
        except Exception as ve:
            print(f"\nValidator error: {ve}")
        
        print(f"\nTo test: python -c \"from strategies.{name} import {_to_class_name(name)}; print('OK')\"")


if __name__ == "__main__":
    main()
