"""Post-Translation Validator for Pine → Python strategy translations.

Runs automatically after every translation to detect and fix common issues.
Can also be run manually: python validate_translation.py <strategy_file.py>

Checks and auto-fixes:
  1. _size_at_fill hardcoded to 1.0 (should use p.fixed_contracts or dynamic qty)
  2. Fake VWAP (SMA used instead of ta_lib.vwap)
  3. Missing dateBlocked check on entries
  4. Missing contract_value parameter for futures strategies
  5. hasattr(ctx, ...) patterns indicating missing engine features
  6. Missing _pending_qty for dynamic sizing
"""

import re
import sys
import os


class ValidationResult:
    def __init__(self):
        self.issues = []       # (severity, description, line_num)
        self.fixes_applied = []  # description of auto-fixes
        self.warnings = []     # things that need manual attention

    @property
    def has_issues(self):
        return len(self.issues) > 0 or len(self.warnings) > 0

    def summary(self):
        lines = []
        if self.fixes_applied:
            lines.append(f"  AUTO-FIXED ({len(self.fixes_applied)}):")
            for f in self.fixes_applied:
                lines.append(f"    ✓ {f}")
        if self.warnings:
            lines.append(f"  WARNINGS ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"    ⚠ {w}")
        if self.issues:
            lines.append(f"  ERRORS ({len(self.issues)}):")
            for sev, desc, ln in self.issues:
                lines.append(f"    ✗ Line {ln}: {desc}")
        if not lines:
            lines.append("  ✓ All checks passed")
        return "\n".join(lines)


def validate_and_fix(filepath: str, auto_fix: bool = True) -> ValidationResult:
    """Validate a translated strategy file and optionally auto-fix issues.
    
    Args:
        filepath: Path to the translated .py strategy file.
        auto_fix: If True, apply fixes in-place. If False, report only.
    
    Returns:
        ValidationResult with issues found and fixes applied.
    """
    result = ValidationResult()
    
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    original = content
    lines = content.split("\n")
    
    # ─── Check 1: _size_at_fill hardcoded to 1.0 ───────────────────
    size_at_fill_match = re.search(
        r'([ \t]+)def _size_at_fill\(self[^)]*\):\s*\n\s*"""[^"]*"""\s*\n\s*return\s+1\.0',
        content
    )
    if size_at_fill_match:
        result.issues.append(("error", "_size_at_fill returns hardcoded 1.0 — ignores qty params", 
                             content[:size_at_fill_match.start()].count("\n") + 1))
        if auto_fix:
            indent = size_at_fill_match.group(1)
            old = size_at_fill_match.group(0)
            new = (f'{indent}def _size_at_fill(self, fill_price, equity_at_fill):\n'
                   f'{indent}    """Recalculate position size at fill time."""\n'
                   f'{indent}    p = self.p\n'
                   f'{indent}    if hasattr(p, \'use_fixed_contracts\') and p.use_fixed_contracts:\n'
                   f'{indent}        return p.fixed_contracts if hasattr(p, \'fixed_contracts\') else 1.0\n'
                   f'{indent}    return self._pending_qty if hasattr(self, \'_pending_qty\') else 1.0')
            content = content.replace(old, new)
            result.fixes_applied.append("_size_at_fill now uses p.fixed_contracts / _pending_qty")
    
    # ─── Check 2: Fake VWAP (SMA substitution) ─────────────────────
    fake_vwap = re.search(r'([ \t]*)(self\.\w+)\s*=\s*ta_lib\.sma\(bars\.close\s*,\s*\d+\)', content)
    if fake_vwap:
        line_num = content[:fake_vwap.start()].count("\n") + 1
        result.issues.append(("error", "VWAP faked as SMA — should use ta_lib.vwap()", line_num))
        if auto_fix:
            old_line = fake_vwap.group(0)
            indent = fake_vwap.group(1)
            var_name = fake_vwap.group(2)
            new_line = f"{indent}{var_name} = ta_lib.vwap(bars.high, bars.low, bars.close, bars.volume, bars.timestamp)"
            content = content.replace(old_line, new_line)
            result.fixes_applied.append("Replaced fake VWAP (SMA) with ta_lib.vwap()")
    
    # Also check for comment mentioning SMA/simplicity near VWAP
    sma_vwap_comment = re.search(r'#.*(?:VWAP|vwap).*(?:SMA|sma|simplicity|equivalent)', content)
    if sma_vwap_comment:
        line_num = content[:sma_vwap_comment.start()].count("\n") + 1
        old_comment = sma_vwap_comment.group(0)
        content = content.replace(old_comment, "# VWAP (cumulative, no session reset)")
        result.fixes_applied.append(f"Updated VWAP comment (removed SMA reference)")
    
    # ─── Check 3: Missing dateBlocked check on entries ──────────────
    has_date_blocked = "dateBlocked" in content or "is_entry_blocked" in content
    has_entries = "ctx.entry(" in content
    
    if has_entries and not has_date_blocked:
        # Rebuild lines from current content (may have been modified by earlier fixes)
        lines = content.split("\n")
        
        # Find entry lines to determine where to add the check
        entry_pattern = re.compile(r'(\s+)(if\s+.+?ctx\.entry\()', re.DOTALL)
        
        # Find the first ctx.entry call and its surrounding if block
        entry_lines = [(i, line) for i, line in enumerate(lines) if "ctx.entry(" in line]
        if entry_lines:
            first_entry_line = entry_lines[0][0]
            result.issues.append(("error", "No dateBlocked check on entries — blocked dates won't work",
                                 first_entry_line + 1))
            
            if auto_fix:
                # Find the if-block that contains the first entry
                # Walk backwards to find the condition
                for check_line in range(first_entry_line - 1, max(0, first_entry_line - 10), -1):
                    stripped = lines[check_line].strip()
                    if stripped.startswith("if ") and "ctx.entry" not in stripped:
                        indent = lines[check_line][:len(lines[check_line]) - len(lines[check_line].lstrip())]
                        # Insert dateBlocked before the if block
                        date_check = f"{indent}# Blocked date check (from engine's market event filter)"
                        date_var = f"{indent}dateBlocked = getattr(ctx, 'is_entry_blocked', False)"
                        
                        # Add "and not dateBlocked" to the condition
                        # Find all entry-gating if conditions
                        new_lines = list(lines)
                        
                        # Insert the dateBlocked variable before the first entry if-block
                        new_lines.insert(check_line, date_var)
                        new_lines.insert(check_line, date_check)
                        new_lines.insert(check_line, "")
                        
                        # Now add "and not dateBlocked" to entry conditions
                        # Re-scan because line numbers shifted
                        content = "\n".join(new_lines)
                        
                        # Add dateBlocked to all entry if-conditions
                        # Pattern: if <condition> and ctx.position_size == 0:
                        content = re.sub(
                            r'(if\s+.+?)(and\s+ctx\.position_size\s*==\s*0\s*:)',
                            r'\1and not dateBlocked \2',
                            content
                        )
                        result.fixes_applied.append("Added dateBlocked check on all entry conditions")
                        break
                else:
                    result.warnings.append("Could not auto-fix dateBlocked — add manually before entry conditions")
    
    # ─── Check 4: Missing contract_value parameter ──────────────────
    # Check if this looks like a futures strategy (session times, ORB, MES/ES/NQ in name)
    is_futures = any(kw in filepath.lower() for kw in ["mes", "mnq", "es_", "nq_", "ym_", "gc_", "mgc", "orb", "futures"])
    is_futures = is_futures or any(kw in content.lower() for kw in ["orb_minutes", "eod_session", "session_str", "0830", "0930"])
    
    has_contract_value = "contract_value" in content
    if is_futures and not has_contract_value:
        result.warnings.append(
            "Strategy appears to be for futures but has no 'contract_value' parameter. "
            "Set Contract Value in sweep settings (MES=5, MNQ=2, ES=50, NQ=20, GC=100)."
        )
        # Add contract_value to params dict
        params_match = re.search(r'(params\s*=\s*\{[^}]+)', content, re.DOTALL)
        if params_match and auto_fix:
            # Find the last param entry
            last_param = params_match.group(0)
            # Detect instrument from filename
            cv = 1
            fname = os.path.basename(filepath).lower()
            if "mes" in fname or "mym" in fname or "mbt" in fname:
                cv = 5
            elif "mnq" in fname:
                cv = 2
            elif "mgc" in fname:
                cv = 10
            elif "gc" in fname:
                cv = 100
            elif "es" in fname or "ym" in fname:
                cv = 50
            elif "nq" in fname:
                cv = 20
            
            if cv > 1:
                # Add contract_value param
                insert_before = last_param.rstrip()
                new_param = f'\n        "contract_value": Param({cv}, group="Contract Settings"),'
                content = content.replace(insert_before, insert_before + new_param)
                result.fixes_applied.append(f"Added contract_value={cv} parameter (detected from filename)")
    
    # ─── Check 5: hasattr(ctx, ...) patterns ────────────────────────
    hasattr_matches = re.finditer(r'hasattr\(ctx,\s*[\'"](\w+)[\'"]\)', content)
    known_ctx_attrs = {
        'is_entry_blocked', 'position_size', 'bar_index', 'equity',
        'strategy_equity', 'initial_capital', 'bars', 'closed_trades_count',
        'last_trade_pnl', 'contract_value',
    }
    for m in hasattr_matches:
        attr = m.group(1)
        if attr not in known_ctx_attrs:
            line_num = content[:m.start()].count("\n") + 1
            result.warnings.append(
                f"Line {line_num}: hasattr(ctx, '{attr}') — this attribute may not exist on Context. "
                f"Check if backtest_engine.py supports it."
            )
    
    # ─── Check 6: _pending_qty not set before entry ─────────────────
    has_pending_qty_set = "_pending_qty" in content and "self._pending_qty =" in content
    has_dynamic_sizing = "use_fixed_contracts" in content and "dynamic" in content.lower()
    
    if has_dynamic_sizing and not has_pending_qty_set:
        result.warnings.append(
            "Strategy has dynamic sizing but _pending_qty is never set before ctx.entry(). "
            "Add 'self._pending_qty = qty' before each ctx.entry() call."
        )
        if auto_fix:
            # Add _pending_qty before each ctx.entry call
            entry_pattern = re.compile(r'(\s+)(ctx\.entry\([^)]+\))')
            def add_pending_qty(match):
                indent = match.group(1)
                entry_call = match.group(2)
                # Extract qty from the entry call
                qty_match = re.search(r'qty=(\w+)', entry_call)
                if qty_match:
                    qty_var = qty_match.group(1)
                    return f"{indent}self._pending_qty = {qty_var}\n{indent}{entry_call}"
                return match.group(0)
            
            new_content = entry_pattern.sub(add_pending_qty, content)
            if new_content != content:
                content = new_content
                result.fixes_applied.append("Added self._pending_qty = qty before each ctx.entry() call")
    
    # ─── Check 7: Entry uses qty_func=self._size_at_fill ────────────
    # If _size_at_fill is fixed, entries should still work, but warn if missing
    if "ctx.entry(" in content and "qty_func" not in content:
        result.warnings.append(
            "Entry calls don't use qty_func=self._size_at_fill. "
            "Qty will be taken from the qty= parameter directly."
        )
    
    # ─── Check 8: Verify init initializes _pending_qty ──────────────
    if "_pending_qty" in content and "self._pending_qty" not in re.findall(r'def init\(.*?\n(?:.*?\n)*?(?=\n    def )', content, re.DOTALL)[0] if re.findall(r'def init\(.*?\n(?:.*?\n)*?(?=\n    def )', content, re.DOTALL) else True:
        # Check if _pending_qty is initialized in init
        init_block = re.search(r'def init\(self.*?\).*?(?=\n    def |\Z)', content, re.DOTALL)
        if init_block and "_pending_qty" not in init_block.group(0):
            if auto_fix and "self._prev_pos" in content:
                # Add initialization near other state vars
                content = content.replace(
                    "self._prev_pos = 0.0",
                    "self._prev_pos = 0.0\n        self._pending_qty = 1.0"
                )
                result.fixes_applied.append("Initialized self._pending_qty = 1.0 in init()")
    
    # ─── Check 9: ctx.exit() with wrong keyword arguments ───────────
    # Translator sometimes generates stop_price=/limit_price= instead of stop=/limit=
    wrong_exit_kwargs = re.findall(r'ctx\.exit\([^)]*(?:stop_price|limit_price)[^)]*\)', content)
    if wrong_exit_kwargs:
        line_num = content[:content.find(wrong_exit_kwargs[0])].count("\n") + 1
        result.issues.append(("error", 
            f"ctx.exit() uses wrong kwargs (stop_price/limit_price instead of stop/limit)", 
            line_num))
        if auto_fix:
            content = content.replace("stop_price=", "stop=")
            content = content.replace("limit_price=", "limit=")
            result.fixes_applied.append("Fixed ctx.exit() kwargs: stop_price→stop, limit_price→limit")
    
    # ─── Check 10: ctx.exit() with positional 'from_entry' arg ──────
    # Translator sometimes generates ctx.exit("id", "Long", stop=...) 
    # but engine signature is ctx.exit(id, from_entry="", stop=, limit=)
    # Make sure from_entry is a keyword arg, not positional with wrong name
    exit_calls = re.findall(r'ctx\.exit\(([^)]+)\)', content)
    for call in exit_calls:
        # Check for common wrong patterns
        if 'stop_price' in call or 'limit_price' in call:
            pass  # Already handled by check 9
        elif 'stop=' not in call and 'limit=' not in call:
            # exit() call with no stop or limit — probably wrong
            result.warnings.append(
                f"ctx.exit() call without stop= or limit= keyword: ctx.exit({call[:60]}...)"
            )
    
    # ─── Apply fixes ────────────────────────────────────────────────
    if auto_fix and content != original:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
    
    return result


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: python validate_translation.py <strategy_file.py> [--no-fix]")
        print("       python validate_translation.py --all [--no-fix]")
        sys.exit(1)
    
    auto_fix = "--no-fix" not in sys.argv
    files = []
    
    if "--all" in sys.argv:
        # Validate all strategies in the strategies folder
        strategies_dir = os.path.join(
            os.environ.get("APPDATA", ""),
            "StrategyOptimizer", "strategies"
        )
        if os.path.exists(strategies_dir):
            files = [
                os.path.join(strategies_dir, f)
                for f in os.listdir(strategies_dir)
                if f.endswith(".py")
            ]
        else:
            print(f"Strategies directory not found: {strategies_dir}")
            sys.exit(1)
    else:
        filepath = sys.argv[1]
        if not os.path.exists(filepath):
            print(f"File not found: {filepath}")
            sys.exit(1)
        files = [filepath]
    
    total_fixes = 0
    total_warnings = 0
    
    for filepath in files:
        print(f"\n{'═' * 60}")
        print(f"Validating: {os.path.basename(filepath)}")
        print(f"{'═' * 60}")
        
        result = validate_and_fix(filepath, auto_fix=auto_fix)
        print(result.summary())
        
        total_fixes += len(result.fixes_applied)
        total_warnings += len(result.warnings)
    
    print(f"\n{'─' * 60}")
    print(f"Total: {len(files)} files, {total_fixes} fixes applied, {total_warnings} warnings")
    
    if total_warnings > 0:
        sys.exit(1)  # Non-zero exit = needs attention


if __name__ == "__main__":
    main()
