"""Fix the prev_position_size tracking bug in translated MESA strategy."""
import os
import sys

filepath = os.path.join(
    os.environ.get("APPDATA", ""),
    "StrategyOptimizer", "strategies", "mesa_mama_fama_crypto_v33_btc.py"
)

if not os.path.exists(filepath):
    print(f"File not found: {filepath}")
    sys.exit(1)

with open(filepath, "r", encoding="utf-8") as f:
    code = f.read()

# Fix 1: Replace the broken prev_position_size detection
old = 'prev_position_size = ctx.get_previous_position_size() if hasattr(ctx, \'get_previous_position_size\') else 0'
new = '''if not hasattr(self, '_prev_pos'):
            self._prev_pos = 0.0
        prev_position_size = self._prev_pos'''

if old in code:
    code = code.replace(old, new)
    print("Fixed: prev_position_size tracking")
else:
    print("WARNING: Could not find prev_position_size pattern to fix")

# Fix 2: Add self._prev_pos = ctx.position_size at end of on_bar
# Find the last line of on_bar (the cooldown line)
old_end = '            self._cooldownBars = 1'
new_end = '''            self._cooldownBars = 1

        # Track position for next bar's newEntry detection
        self._prev_pos = ctx.position_size'''

if old_end in code and 'self._prev_pos = ctx.position_size' not in code:
    code = code.replace(old_end, new_end)
    print("Fixed: Added _prev_pos tracking at end of on_bar")
else:
    # Try adding it before the last line
    if 'self._prev_pos = ctx.position_size' not in code:
        # Just append it to the class
        code = code.rstrip() + '\n\n        # Track position for next bar\n        self._prev_pos = ctx.position_size\n'
        print("Fixed: Appended _prev_pos tracking")

# Also init _prev_pos in init()
if 'self._prev_pos' not in code.split('def on_bar')[0]:
    code = code.replace('self._lastLabelBar = -1', 'self._lastLabelBar = -1\n        self._prev_pos = 0.0')
    print("Fixed: Added _prev_pos init")

with open(filepath, "w", encoding="utf-8") as f:
    f.write(code)

print(f"\nPatched: {filepath}")
print("Run a sweep to test!")
