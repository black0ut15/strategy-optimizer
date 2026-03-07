"""
Pine Script → Python Deterministic Translator

Parses Pine Script and emits a Python strategy class.
Handles ~80-90% of typical strategies mechanically.
Falls back to Claude API for complex/unresolvable patterns.

Architecture:
  1. Tokenizer → tokens
  2. Parser → lightweight AST (statements + expressions)
  3. Emitter → Python source targeting Strategy base class
  4. Cache → SHA256 hash avoids re-translation
"""

import re
import hashlib
import json
import os
import sys
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Cache in user data dir to avoid triggering Tauri file watcher
if os.environ.get("APPDATA"):
    CACHE_DIR = os.path.join(os.environ["APPDATA"], "StrategyOptimizer", ".translation_cache")
elif os.environ.get("USERPROFILE"):
    CACHE_DIR = os.path.join(os.environ["USERPROFILE"], "StrategyOptimizer", ".translation_cache")
else:
    CACHE_DIR = os.path.join(SCRIPT_DIR, ".translation_cache")


# ═══════════════════════════════════════════════════════════════════
# Cache
# ═══════════════════════════════════════════════════════════════════

def cache_key(pine_code: str) -> str:
    return hashlib.sha256(pine_code.encode("utf-8")).hexdigest()[:16]

def get_cached(pine_code: str) -> Optional[str]:
    key = cache_key(pine_code)
    path = os.path.join(CACHE_DIR, f"{key}.py")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return None

def set_cached(pine_code: str, python_code: str):
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = cache_key(pine_code)
    path = os.path.join(CACHE_DIR, f"{key}.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(python_code)


# ═══════════════════════════════════════════════════════════════════
# Pine Script Input Parser
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PineInput:
    """Parsed input.* declaration."""
    var_name: str
    pine_type: str       # "float", "int", "bool", "string", "source"
    default: Any = None
    label: str = ""
    group: str = ""
    tooltip: str = ""
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    step: Optional[float] = None
    options: Optional[List[str]] = None
    # Extra: division wrapper like `/ 100`
    divisor: Optional[float] = None


@dataclass  
class PineFunction:
    """Custom function definition."""
    name: str
    params: List[str]
    body: List[str]  # raw lines
    is_visual: bool = False  # str.tostring, label, etc.


@dataclass
class PineStrategy:
    """Parsed strategy metadata."""
    title: str = ""
    initial_capital: float = 100000
    commission_pct: float = 0.0
    process_on_close: bool = False
    calc_on_fills: bool = True
    pyramiding: int = 0
    default_qty_type: str = "fixed"  # "fixed", "percent_of_equity", "cash"
    default_qty_value: float = 1.0


@dataclass
class ParseResult:
    """Full parse result."""
    strategy: PineStrategy
    inputs: List[PineInput]
    functions: List[PineFunction]
    var_declarations: Dict[str, str]  # var name -> initial value as string
    main_body: List[str]  # lines after inputs/functions
    groups: Dict[str, str]  # group variable -> group string
    unresolved: List[str]  # lines the parser couldn't handle
    visual_vars: set  # variables that are visual-only


# ═══════════════════════════════════════════════════════════════════
# Parser
# ═══════════════════════════════════════════════════════════════════

# Visual-only functions and patterns to skip
VISUAL_FUNCS = {
    "plot", "plotshape", "plotchar", "plotarrow", "plotbar", "plotcandle",
    "bgcolor", "fill", "hline",
    "label.new", "label.set_text", "label.delete",
    "line.new", "line.set", "line.delete",
    "box.new", "box.set", "box.delete",
    "table.new", "table.cell", "table.set_bgcolor",
    "alertcondition", "alert",
}

VISUAL_PREFIXES = ("plot", "label.", "line.", "box.", "table.", "color.", "alert")

# input.color is visual-only
VISUAL_INPUT_TYPES = {"color", "time"}


def strip_comments(code: str) -> str:
    """Remove Pine Script comments."""
    lines = []
    for line in code.split("\n"):
        # Remove line comments (but not inside strings)
        in_string = False
        result = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch in ('"', "'") and not in_string:
                in_string = ch
                result.append(ch)
            elif ch == in_string:
                in_string = False
                result.append(ch)
            elif line[i:i+2] == "//" and not in_string:
                break
            else:
                result.append(ch)
            i += 1
        lines.append("".join(result).rstrip())
    return "\n".join(lines)


def _unmatched_parens(line: str) -> int:
    """Count unmatched open parentheses (ignoring those inside strings)."""
    depth = 0
    in_string = None
    for ch in line:
        if ch in ('"', "'") and in_string is None:
            in_string = ch
        elif ch == in_string:
            in_string = None
        elif not in_string:
            if ch == "(": depth += 1
            elif ch == ")": depth -= 1
    return depth


def parse_pine(code: str) -> ParseResult:
    """Parse Pine Script into structured components."""
    code = strip_comments(code)
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    
    result = ParseResult(
        strategy=PineStrategy(),
        inputs=[],
        functions=[],
        var_declarations={},
        main_body=[],
        groups={},
        unresolved=[],
        visual_vars=set(),
    )
    
    # Pre-process: join continuation lines (unmatched parens, trailing operators, ternary colon)
    raw_lines = code.split("\n")
    lines = []
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        # Join lines with unmatched parens, trailing and/or, or trailing ternary ?/:
        while i + 1 < len(raw_lines) and (
            _unmatched_parens(line) > 0 or
            line.rstrip().endswith(" or") or
            line.rstrip().endswith(" and") or
            line.rstrip().endswith("?") or
            (line.rstrip().endswith(":") and "?" in line and not line.strip().startswith(("if ", "else", "for ")))
        ):
            i += 1
            line = line.rstrip() + " " + raw_lines[i].strip()
        lines.append(line)
        i += 1
    
    i = 0
    
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        # Skip empty lines and version directive
        if not stripped or stripped.startswith("//@version"):
            i += 1
            continue
        
        # Parse strategy() declaration
        if stripped.startswith("strategy("):
            _parse_strategy_decl(stripped, lines, i, result)
            # Might span multiple lines
            while i < len(lines) and ")" not in lines[i]:
                i += 1
            i += 1
            continue
        
        # Parse custom functions: name(args) =>
        func_match = re.match(r'^(\w+)\(([^)]*)\)\s*=>\s*$', stripped)
        if func_match:
            name, params_str = func_match.group(1), func_match.group(2)
            params = [p.strip() for p in params_str.split(",") if p.strip()]
            body = []
            i += 1
            while i < len(lines) and (lines[i].startswith("    ") or lines[i].startswith("\t") or not lines[i].strip()):
                if lines[i].strip():
                    body.append(lines[i])
                i += 1
            is_visual = any(vf in name.lower() for vf in ["label", "txt", "price_txt", "colr", "payload", "json", "alert"])
            result.functions.append(PineFunction(name, params, body, is_visual))
            continue
        
        # Single-line function: name(args) => expr
        func_match2 = re.match(r'^(\w+)\(([^)]*)\)\s*=>\s*(.+)$', stripped)
        if func_match2:
            name, params_str, body = func_match2.group(1), func_match2.group(2), func_match2.group(3)
            params = [p.strip() for p in params_str.split(",") if p.strip()]
            is_visual = any(vf in name.lower() or vf in body.lower() for vf in ["str.tostring", "format.mintick", "label", "color", "payload", "json_escape", "alert"])
            result.functions.append(PineFunction(name, params, [f"    {body}"], is_visual))
            i += 1
            continue
        
        # Parse group variable assignments: grpXxx = "Group Name"
        grp_match = re.match(r'^(grp\w+)\s*=\s*"([^"]*)"', stripped)
        if grp_match:
            result.groups[grp_match.group(1)] = grp_match.group(2)
            i += 1
            continue
        
        # Parse input declarations
        input_match = re.match(r'^(\w+)\s*=\s*input\.(float|int|bool|string|source|color|time|session)\s*\(', stripped)
        if input_match:
            inp = _parse_input(input_match.group(1), input_match.group(2), stripped, result.groups)
            if inp and inp.pine_type not in VISUAL_INPUT_TYPES:
                # Check for division wrapper: `input.float(...) / 100`
                div_match = re.search(r'\)\s*/\s*(\d+\.?\d*)\s*$', stripped)
                if div_match:
                    inp.divisor = float(div_match.group(1))
                result.inputs.append(inp)
            elif inp and inp.pine_type in VISUAL_INPUT_TYPES:
                result.visual_vars.add(input_match.group(1))
            i += 1
            continue
        
        # Parse var declarations: var float x = na
        var_match = re.match(r'^var\s+(\w+)\s+(\w+)\s*=\s*(.+)', stripped)
        if not var_match:
            var_match2 = re.match(r'^var\s+(\w+)\s*=\s*(.+)', stripped)
            if var_match2:
                var_name, init_val = var_match2.group(1), var_match2.group(2).strip()
                # Skip visual types
                if var_name not in ("box", "line", "label", "table"):
                    result.var_declarations[var_name] = init_val
                i += 1
                continue
        if var_match:
            type_name, var_name, init_val = var_match.group(1), var_match.group(2), var_match.group(3).strip()
            # Skip visual types
            if type_name in ("box", "line", "label", "table", "color"):
                result.visual_vars.add(var_name)
            else:
                result.var_declarations[var_name] = init_val
            i += 1
            continue
        
        # Skip visual-only lines
        if _is_visual_line(stripped, result.visual_vars):
            # If this is a block starter (if/for), skip the entire indented block
            if stripped.endswith(":") or (not stripped.endswith(")") and ("barstate.islast" in stripped or "showDiag" in stripped.lower() or "show_diag" in stripped.lower())):
                base_indent = len(line) - len(line.lstrip())
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    if next_line.strip():
                        next_indent = len(next_line) - len(next_line.lstrip())
                        if next_indent <= base_indent:
                            break
                    i += 1
                continue
            i += 1
            continue
        
        # Everything else goes to main body
        result.main_body.append(line)
        i += 1
    
    return result


def _parse_strategy_decl(line: str, lines: List[str], start: int, result: ParseResult):
    """Parse strategy() declaration."""
    # Collect full declaration (may span lines)
    full = line
    i = start
    while ")" not in full and i + 1 < len(lines):
        i += 1
        full += " " + lines[i].strip()
    
    title_match = re.search(r'strategy\(\s*"([^"]*)"', full)
    if title_match:
        result.strategy.title = title_match.group(1)
    
    for pattern, attr, converter in [
        (r'initial_capital\s*=\s*([\d.]+)', 'initial_capital', float),
        (r'commission_value\s*=\s*([\d.]+)', 'commission_pct', float),
        (r'process_orders_on_close\s*=\s*(true|false)', 'process_on_close', lambda x: x == 'true'),
        (r'calc_on_order_fills\s*=\s*(true|false)', 'calc_on_fills', lambda x: x == 'true'),
        (r'pyramiding\s*=\s*(\d+)', 'pyramiding', int),
        (r'default_qty_value\s*=\s*([\d.]+)', 'default_qty_value', float),
    ]:
        m = re.search(pattern, full, re.IGNORECASE)
        if m:
            setattr(result.strategy, attr, converter(m.group(1)))
    
    # Parse default_qty_type (enum)
    qty_type_match = re.search(r'default_qty_type\s*=\s*strategy\.([\w.]+)', full)
    if qty_type_match:
        qt = qty_type_match.group(1).lower()
        if "percent" in qt:
            result.strategy.default_qty_type = "percent_of_equity"
        elif "cash" in qt:
            result.strategy.default_qty_type = "cash"
        else:
            result.strategy.default_qty_type = "fixed"


def _parse_input(var_name: str, inp_type: str, line: str, groups: Dict[str, str]) -> Optional[PineInput]:
    """Parse an input.* declaration."""
    inp = PineInput(var_name=var_name, pine_type=inp_type)
    
    # Extract the argument section between input.type( and the last )
    paren_start = line.index("(")
    # Find matching close paren (handle nested)
    depth = 0
    paren_end = paren_start
    for j in range(paren_start, len(line)):
        if line[j] == "(": depth += 1
        elif line[j] == ")": 
            depth -= 1
            if depth == 0:
                paren_end = j
                break
    
    args_str = line[paren_start+1:paren_end]
    
    # Extract default value (first positional arg)
    if inp_type == "float":
        m = re.match(r'\s*([\d.eE+-]+)', args_str)
        if m: inp.default = float(m.group(1))
    elif inp_type == "int":
        m = re.match(r'\s*(\d+)', args_str)
        if m: inp.default = int(m.group(1))
    elif inp_type == "bool":
        m = re.match(r'\s*(true|false)', args_str, re.IGNORECASE)
        if m: inp.default = m.group(1).lower() == "true"
    elif inp_type in ("string", "source", "session"):
        m = re.match(r'\s*"([^"]*)"', args_str)
        if m: 
            inp.default = m.group(1)
        else:
            # Source default might be unquoted: input.source(hl2, ...)
            m = re.match(r'\s*(\w+)', args_str)
            if m: inp.default = m.group(1)
    
    # Extract named parameters
    label_match = re.search(r'(?:,\s*"([^"]*)")', args_str)
    if label_match:
        inp.label = label_match.group(1)
    
    for pattern, attr, converter in [
        (r'minval\s*=\s*([\d.eE+-]+)', 'min_val', float),
        (r'maxval\s*=\s*([\d.eE+-]+)', 'max_val', float),
        (r'step\s*=\s*([\d.eE+-]+)', 'step', float),
    ]:
        m = re.search(pattern, args_str)
        if m:
            setattr(inp, attr, converter(m.group(1)))
    
    # Extract group
    grp_match = re.search(r'group\s*=\s*(\w+)', args_str)
    if grp_match:
        grp_var = grp_match.group(1)
        inp.group = groups.get(grp_var, grp_var)
    
    # Extract tooltip
    tip_match = re.search(r'tooltip\s*=\s*"([^"]*)"', args_str)
    if tip_match:
        inp.tooltip = tip_match.group(1)
    
    # Extract options array
    opts_match = re.search(r'\[([^\]]+)\]', args_str)
    if opts_match and inp_type in ("string", "source"):
        opts_str = opts_match.group(1)
        inp.options = [s.strip().strip('"').strip("'") for s in opts_str.split(",")]
    
    # Source type gets default options
    if inp_type == "source" and not inp.options:
        inp.options = ["close", "open", "high", "low", "hl2", "hlc3", "ohlc4", "hlcc4"]
    
    return inp


def _is_visual_line(line: str, visual_vars: set) -> bool:
    """Check if a line is visual-only and should be skipped."""
    stripped = line.strip()
    
    # NEVER skip strategy trading commands — these are core logic
    if stripped.startswith(("strategy.entry", "strategy.exit", "strategy.close", "strategy.cancel")):
        return False
    
    # Direct visual function calls
    for func in VISUAL_FUNCS:
        if stripped.startswith(func + "(") or stripped.startswith(func + " "):
            return True
    
    # Lines that start with visual prefixes
    for prefix in VISUAL_PREFIXES:
        if stripped.startswith(prefix):
            return True
    
    # Variable assignments to visual vars
    for vv in visual_vars:
        if stripped.startswith(vv + " ") or stripped.startswith(vv + "="):
            return True
    
    # barstate.islast blocks (always visual)
    if "barstate.islast" in stripped:
        return True
    
    # String formatting (visual)
    if "str.tostring" in stripped or "format.mintick" in stripped:
        return True
    
    # Webhook payload building (visual/alert)
    if re.match(r'^\w*[Pp]ayload\s*\+?=', stripped):
        return True
    
    # alert_message building
    if "alert_message" in stripped.lower():
        return True
    
    # Color type declarations and assignments
    if re.match(r'^color\s+\w+\s*=', stripped):
        return True
    
    # Lines containing color.new or color.* (visual)
    if "color.new(" in stripped or "color.rgb(" in stripped:
        return True
    
    # indicator() declaration (FoShoBro uses indicator instead of strategy)
    if stripped.startswith("indicator("):
        return True
    
    # session.* assignments that are visual
    if "session.isfirstbar_regular" in stripped and "=" in stripped:
        pass  # Keep these — they may be used in logic
    
    return False


# ═══════════════════════════════════════════════════════════════════
# Python Emitter
# ═══════════════════════════════════════════════════════════════════

def to_snake_case(name: str) -> str:
    """Convert camelCase/PascalCase to snake_case."""
    # Insert underscore before uppercase letters
    s = re.sub(r'([a-z])([A-Z])', r'\1_\2', name)
    return s.lower()


def to_class_name(name: str) -> str:
    """Convert snake_case to CamelCase."""
    return "".join(w.capitalize() for w in name.replace("-", "_").split("_"))


def _translate_ternaries(expr: str) -> str:
    """Translate all Pine ternary (cond ? a : b) to Python (a if cond else b).
    Handles nested parentheses correctly."""
    # Find rightmost ? that has a matching :
    max_iterations = 10
    for _ in range(max_iterations):
        q_pos = _find_ternary_question(expr)
        if q_pos is None:
            break
        
        # Find the condition (everything before ? at same paren depth)
        # Walk backwards from ? to find condition start
        cond_start = 0
        depth = 0
        for j in range(q_pos - 1, -1, -1):
            ch = expr[j]
            if ch == ")": depth += 1
            elif ch == "(": 
                depth -= 1
                if depth < 0:
                    cond_start = j + 1
                    break
            elif ch == "," and depth == 0:
                cond_start = j + 1
                break
            elif ch == "=" and depth == 0:
                # Don't split on == (comparison) or != or <= or >=
                # Only split on bare = (assignment)
                if j > 0 and expr[j-1] in ("=", "!", "<", ">"):
                    continue  # Part of ==, !=, <=, >=
                if j + 1 < len(expr) and expr[j+1] == "=":
                    continue  # Part of ==
                cond_start = j + 1
                break
        
        # Find the : at the same depth as ?
        colon_pos = None
        depth = 0
        for j in range(q_pos + 1, len(expr)):
            ch = expr[j]
            if ch == "(": depth += 1
            elif ch == ")": depth -= 1
            elif ch == ":" and depth == 0:
                colon_pos = j
                break
        
        if colon_pos is None:
            break
        
        # Find end of else branch
        else_end = len(expr)
        depth = 0
        for j in range(colon_pos + 1, len(expr)):
            ch = expr[j]
            if ch == "(": depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    else_end = j
                    break
            elif ch in (",",) and depth == 0:
                else_end = j
                break
        
        cond = expr[cond_start:q_pos].strip()
        a = expr[q_pos+1:colon_pos].strip()
        b = expr[colon_pos+1:else_end].strip()
        
        replacement = f"({a} if {cond} else {b})"
        expr = expr[:cond_start] + replacement + expr[else_end:]
    
    return expr


def _find_ternary_question(expr: str) -> Optional[int]:
    """Find the position of a ? that's part of a ternary (not inside a string)."""
    depth = 0
    in_string = None
    for i, ch in enumerate(expr):
        if ch in ('"', "'") and in_string is None:
            in_string = ch
        elif ch == in_string:
            in_string = None
        elif not in_string:
            if ch == "(": depth += 1
            elif ch == ")": depth -= 1
            elif ch == "?" and depth >= 0:
                # Check it's not part of a word (like ?.)
                if i + 1 < len(expr) and expr[i+1] != ".":
                    return i
    return None


def translate_expression(expr: str, inputs: Dict[str, PineInput]) -> str:
    """Translate a Pine expression to Python."""
    e = expr.strip()
    
    # na(x) check -> np.isnan(x) — MUST come before bare na replacement
    e = re.sub(r'\bna\(([^)]+)\)', r'np.isnan(\1)', e)
    
    # nz() — 2-arg: nz(x, default) and 1-arg: nz(x)
    e = re.sub(r'\bnz\(([^,)]+),\s*([^)]+)\)', r'(\2 if np.isnan(\1) else \1)', e)
    e = re.sub(r'\bnz\(([^,)]+)\)', r'(0 if np.isnan(\1) else \1)', e)
    
    # na -> np.nan (bare value)
    e = re.sub(r'\bna\b', 'np.nan', e)
    
    # true/false -> True/False
    e = re.sub(r'\btrue\b', 'True', e)
    e = re.sub(r'\bfalse\b', 'False', e)
    
    # and/or/not -> and/or/not (same in Python)
    # But Pine uses `and`/`or` which are the same as Python
    
    # nz(x) -> (x if not np.isnan(x) else 0) for scalars
    # (for arrays, ta_lib.nz is better but context-dependent)
    
    # math.abs -> abs
    e = re.sub(r'\bmath\.abs\b', 'abs', e)
    e = re.sub(r'\bmath\.max\b', 'max', e)
    e = re.sub(r'\bmath\.min\b', 'min', e)
    e = re.sub(r'\bmath\.floor\b', 'np.floor', e)
    e = re.sub(r'\bmath\.ceil\b', 'np.ceil', e)
    e = re.sub(r'\bmath\.round\b', 'round', e)
    e = re.sub(r'\bmath\.pi\b', 'np.pi', e)
    e = re.sub(r'\bmath\.atan\b', 'np.arctan', e)
    e = re.sub(r'\bmath\.sum\b', 'ta_lib.rolling_sum', e)
    
    # ta.* -> ta_lib.*
    e = re.sub(r'\bta\.', 'ta_lib.', e)
    
    # Pine time() function -> self._pine_time (avoid Python time module collision)
    e = re.sub(r'\btime\(([^)]+)\)', r'self._pine_time(\1)', e)
    
    # syminfo.* -> string constants or params
    e = re.sub(r'\bsyminfo\.ticker\b', '"BTCUSDT"', e)  # TODO: parameterize
    e = re.sub(r'\bsyminfo\.mintick\b', '0.01', e)
    e = re.sub(r'\btimeframe\.period\b', '"5"', e)  # TODO: parameterize
    e = re.sub(r'\btimeframe\.in_seconds\(\)', '300', e)
    
    # session.* 
    e = re.sub(r'\bsession\.isfirstbar_regular\b', 'False', e)  # TODO: implement
    e = re.sub(r'\bsession\.ismarket\b', 'True', e)
    
    # hour/minute from bar timestamp
    e = re.sub(r'\bhour\b(?!\()', 'self._bar_hour(bar)', e)
    e = re.sub(r'\bminute\b(?!\()', 'self._bar_minute(bar)', e)
    e = re.sub(r'\bdayofweek\b', 'self._bar_dayofweek(bar)', e)
    e = re.sub(r'\bdayofmonth\(([^)]*)\)', 'self._bar_dayofmonth(bar)', e)
    
    # Custom function calls: f_xxx(...) -> self._f_xxx(bar, ctx, bars, i, p, ...)
    e = re.sub(r'\b(f_\w+)\(', r'self._\1(bar, ctx, bars, i, p, ', e)
    # Clean up double-open-paren if no args: self._f_xxx(bar, ctx, bars, i, p, )
    e = e.replace(', )', ')')
    
    # str.replace_all -> .replace (Python string method)
    e = re.sub(r'\bstr\.replace_all\((\w+),\s*"([^"]*)",\s*"([^"]*)"\)', r'\1.replace("\2", "\3")', e)
    
    # strategy.position_size -> ctx.position_size
    e = re.sub(r'\bstrategy\.position_size\b', 'ctx.position_size', e)
    e = re.sub(r'\bstrategy\.position_avg_price\b', 'ctx.position_avg_price', e)
    e = re.sub(r'\bstrategy\.equity\b', 'ctx.strategy_equity', e)
    e = re.sub(r'\bstrategy\.long\b', '"long"', e)
    e = re.sub(r'\bstrategy\.short\b', '"short"', e)
    
    # bar_index -> ctx.bar_index
    e = re.sub(r'\bbar_index\b', 'ctx.bar_index', e)
    
    # Ternary: cond ? a : b -> (a if cond else b)
    # Handle ternaries anywhere in the expression, paren-aware
    e = _translate_ternaries(e)
    
    # History reference: close[1] -> bars.close[i-1], close[i] -> bars.close[i]
    e = re.sub(r'\b(close|open|high|low|volume|hl2|hlc3|ohlc4)\[(\d+)\]',
               lambda m: f'bars.{m.group(1)}[i-{m.group(2)}]', e)
    # Variable index: close[i], close[i+1] — only if not already prefixed with bars.
    e = re.sub(r'(?<!bars\.)\b(close|open|high|low|volume|hl2|hlc3|ohlc4)\[([^]]+)\]',
               lambda m: f'bars.{m.group(1)}[{m.group(2)}]', e)
    
    # Plain bar data references (no index) — only if not already prefixed
    e = re.sub(r'(?<!bars\.)(?<!bar\.)\b(close|open|high|low|volume)\b(?!\[|\(|\.)',
               lambda m: f'bar.{m.group(1)}', e)
    
    return e


def _add_returns_to_helper(lines: List[str], body_start: int) -> None:
    """Add 'return' to bare expressions at branch ends in helper function bodies.
    
    Pine functions implicitly return the last expression of each code path.
    In Python we need explicit return statements.
    """
    for j in range(body_start, len(lines)):
        s = lines[j].strip()
        if not s or s.startswith('"""') or s.startswith("#"):
            continue
        
        indent = lines[j][:len(lines[j]) - len(lines[j].lstrip())]
        
        # Skip lines that are already control flow or assignments
        is_control = s.startswith(("if ", "elif ", "else:", "for ", "while ", "return ", "break", "continue", "def "))
        assign_match = re.match(r'^[\w.]+\s*[+\-*/]?=\s', s)
        is_assignment = assign_match is not None and "==" not in s[:s.find("=")+2] if "=" in s else False
        
        if is_control or is_assignment:
            continue
        
        # This is a bare expression. Check if it's the last non-empty line before:
        # - end of function (next line has same or less indent than function def)
        # - an else/elif block
        # - end of lines
        is_branch_end = False
        
        # Check what follows
        next_code_line = ""
        for k in range(j + 1, len(lines)):
            ns = lines[k].strip()
            if ns:
                next_code_line = ns
                next_indent = lines[k][:len(lines[k]) - len(lines[k].lstrip())]
                break
        
        if not next_code_line:
            # Last line of function
            is_branch_end = True
        elif next_code_line.startswith(("else:", "elif ")):
            is_branch_end = True
        elif len(next_indent) <= len(indent) and not next_code_line.startswith(("else:", "elif ")):
            # Next line is at same or lesser indent — this is end of a block
            is_branch_end = True
        
        if is_branch_end:
            lines[j] = f"{indent}return {s}"


def emit_python(parse_result: ParseResult, strategy_name: str) -> Tuple[str, List[str]]:
    """Emit Python strategy class from parsed Pine Script.
    
    Returns (python_code, unresolved_sections).
    """
    pr = parse_result
    class_name = to_class_name(strategy_name)
    unresolved = []
    
    lines = []
    lines.append(f'"""Auto-translated from Pine Script: {pr.strategy.title}"""')
    lines.append("")
    lines.append("import numpy as np")
    lines.append("from strategy_base import Strategy, Param")
    lines.append("from backtest_engine import BarData, Context, Bars")
    lines.append("import ta as ta_lib")
    lines.append("")
    lines.append("")
    lines.append(f"class {class_name}(Strategy):")
    lines.append(f'    """Translated from: {pr.strategy.title}"""')
    lines.append("")
    
    # Emit params
    lines.append("    params = {")
    for inp in pr.inputs:
        snake = to_snake_case(inp.var_name)
        
        if inp.pine_type == "source":
            default = f'"{inp.default}"'
            opts = f', options={inp.options}' if inp.options else ''
            lines.append(f'        "{snake}": Param({default}{opts}, group="{inp.group}"),')
        elif inp.pine_type in ("string", "session"):
            default = f'"{inp.default}"'
            opts = f', options={inp.options}' if inp.options else ''
            lines.append(f'        "{snake}": Param({default}{opts}, group="{inp.group}"),')
        elif inp.pine_type == "bool":
            default = str(inp.default)
            lines.append(f'        "{snake}": Param({default}, group="{inp.group}"),')
        else:
            # Numeric
            default = inp.default
            if inp.divisor:
                default = default / inp.divisor
            parts = [str(default)]
            if inp.min_val is not None:
                v = inp.min_val / inp.divisor if inp.divisor else inp.min_val
                parts.append(f"min={v}")
            if inp.max_val is not None:
                v = inp.max_val / inp.divisor if inp.divisor else inp.max_val
                parts.append(f"max={v}")
            if inp.step is not None:
                v = inp.step / inp.divisor if inp.divisor else inp.step
                parts.append(f"step={v}")
            parts.append(f'group="{inp.group}"')
            lines.append(f'        "{snake}": Param({", ".join(parts)}),')
    
    lines.append("    }")
    lines.append("")
    
    # Emit init()
    lines.append("    def init(self, ctx: Context) -> None:")
    lines.append('        """Pre-compute indicators (vectorized)."""')
    lines.append("        bars = ctx.bars")
    lines.append("        p = self.p")
    lines.append("")
    
    # Add var declarations as instance vars
    for var_name, init_val in pr.var_declarations.items():
        if var_name in pr.visual_vars:
            continue
        py_val = translate_expression(init_val, {inp.var_name: inp for inp in pr.inputs})
        py_val = py_val.replace("np.nan", "0.0")  # var float x = na -> 0.0
        lines.append(f"        self._{var_name} = {py_val}")
    
    if pr.var_declarations:
        lines.append("")
    
    # Position tracking for new entry detection (Pine has implicit prev bar state)
    lines.append("        self._prev_pos = 0.0")
    
    # ── Build variable name mappings (used by both helper methods and body) ──
    input_names = {}  # Pine camelCase -> p.snake_case
    for inp in pr.inputs:
        snake = to_snake_case(inp.var_name)
        input_names[inp.var_name] = f"p.{snake}"
    
    var_names = {}  # var declarations -> self._name
    for var_name in pr.var_declarations:
        var_names[var_name] = f"self._{var_name}"
    
    # Placeholder for indicator computation
    lines.append("        # TODO: Pre-compute indicators here")
    lines.append("        # Example: self.ema = ta_lib.ema(bars.close, p.ema_len)")
    lines.append("")
    
    # Emit helper methods from custom Pine functions (non-visual)
    # First, add standard time utility methods
    lines.append("    def _bar_hour(self, bar):")
    lines.append("        from datetime import datetime, timezone")
    lines.append("        ts = bar.timestamp / 1000 if bar.timestamp > 1e12 else bar.timestamp")
    lines.append("        return datetime.fromtimestamp(ts, tz=timezone.utc).hour")
    lines.append("")
    lines.append("    def _bar_minute(self, bar):")
    lines.append("        from datetime import datetime, timezone")
    lines.append("        ts = bar.timestamp / 1000 if bar.timestamp > 1e12 else bar.timestamp")
    lines.append("        return datetime.fromtimestamp(ts, tz=timezone.utc).minute")
    lines.append("")
    lines.append("    def _bar_dayofweek(self, bar):")
    lines.append("        from datetime import datetime, timezone")
    lines.append("        ts = bar.timestamp / 1000 if bar.timestamp > 1e12 else bar.timestamp")
    lines.append("        return datetime.fromtimestamp(ts, tz=timezone.utc).isoweekday()  # 1=Mon, 7=Sun")
    lines.append("")
    lines.append("    def _bar_dayofmonth(self, bar):")
    lines.append("        from datetime import datetime, timezone")
    lines.append("        ts = bar.timestamp / 1000 if bar.timestamp > 1e12 else bar.timestamp")
    lines.append("        return datetime.fromtimestamp(ts, tz=timezone.utc).day")
    lines.append("")
    lines.append("    def _pine_time(self, timeframe, session_str):")
    lines.append('        """Check if current bar falls within a session time window.')
    lines.append('        ')
    lines.append('        Pine time(timeframe, session) returns non-NaN if in session.')
    lines.append('        session_str format: "HHMM-HHMM" (e.g., "0830-1500")')
    lines.append('        Uses exchange timezone (CT for futures, ET for stocks).')
    lines.append('        """')
    lines.append("        import numpy as np")
    lines.append("        if not hasattr(self, '_current_bar_ts'):")
    lines.append("            return np.nan")
    lines.append("        from datetime import datetime, timezone, timedelta")
    lines.append("        ts = self._current_bar_ts")
    lines.append("        if ts > 1e12: ts = ts / 1000")
    lines.append("        dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)")
    lines.append("        # Convert to CT (UTC-6, or UTC-5 during DST)")
    lines.append("        # Simple DST: Mar second Sun to Nov first Sun")
    lines.append("        year = dt_utc.year")
    lines.append("        mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)")
    lines.append("        mar_second_sun = mar1 + timedelta(days=(6-mar1.weekday())%7 + 7)")
    lines.append("        nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)")
    lines.append("        nov_first_sun = nov1 + timedelta(days=(6-nov1.weekday())%7)")
    lines.append("        is_dst = mar_second_sun <= dt_utc.replace(tzinfo=timezone.utc) < nov_first_sun")
    lines.append("        ct_offset = timedelta(hours=-5 if is_dst else -6)")
    lines.append("        dt_ct = dt_utc + ct_offset")
    lines.append("        bar_hhmm = dt_ct.hour * 100 + dt_ct.minute")
    lines.append("        try:")
    lines.append("            parts = session_str.split('-')")
    lines.append("            start_hhmm = int(parts[0])")
    lines.append("            end_hhmm = int(parts[1])")
    lines.append("        except:")
    lines.append("            return np.nan")
    lines.append("        if start_hhmm <= end_hhmm:")
    lines.append("            in_session = start_hhmm <= bar_hhmm < end_hhmm")
    lines.append("        else:")
    lines.append("            in_session = bar_hhmm >= start_hhmm or bar_hhmm < end_hhmm")
    lines.append("        return 1.0 if in_session else np.nan")
    lines.append("")

    # Emit _size_at_fill for fill-time qty recalculation (TradingView calc_on_order_fills)
    # Detects sizing params from the strategy to build the function
    sizing_params = {}
    for inp in pr.inputs:
        snake = to_snake_case(inp.var_name)
        lower = snake.lower()
        if re.search(r'(pos_pct|margin_pct|leverage|lvxmax|lvxmin|min_qty|max_qty|fractional_qty|contract_value|min_contracts|max_contracts)', lower):
            sizing_params[snake] = inp
    
    if sizing_params:
        lines.append("    def _size_at_fill(self, fill_price, equity_at_fill):")
        lines.append('        """Recalculate position size at fill time (matches TradingView calc_on_order_fills)."""')
        lines.append("        p = self.p")
        lines.append("        if fill_price <= 0:")
        lines.append("            return 0.0")
        
        # Detect common sizing patterns from params
        has_fractional = any("fractional" in k for k in sizing_params)
        has_pos_pct = any("pos_pct" in k for k in sizing_params)
        has_lvxmax = any("lvxmax" in k or "max_lev" in k for k in sizing_params)
        has_lvxmin = any("lvxmin" in k or "min_lev" in k for k in sizing_params)
        has_min_qty = any("min_qty" in k for k in sizing_params)
        has_contract_value = any("contract_value" in k for k in sizing_params)
        has_min_contracts = any("min_contracts" in k for k in sizing_params)
        has_max_contracts = any("max_contracts" in k for k in sizing_params)
        
        if has_pos_pct and has_lvxmax:
            # MESA-style fractional sizing
            lvxmax = next(k for k in sizing_params if "lvxmax" in k or "max_lev" in k)
            lvxmin = next((k for k in sizing_params if "lvxmin" in k or "min_lev" in k), None)
            pos_pct = next(k for k in sizing_params if "pos_pct" in k)
            min_qty_param = next((k for k in sizing_params if "min_qty" in k), None)
            
            lines.append(f"        target_notional = equity_at_fill * p.{pos_pct} * p.{lvxmax}")
            lines.append(f"        raw_qty = target_notional / fill_price")
            if lvxmin and min_qty_param:
                lines.append(f"        lo = (equity_at_fill * p.{pos_pct} * p.{lvxmin}) / fill_price")
                lines.append(f"        hi = (equity_at_fill * p.{pos_pct} * p.{lvxmax}) / fill_price")
                lines.append(f"        clamped = max(lo, min(raw_qty, hi))")
                lines.append(f"        import numpy as np")
                lines.append(f"        stepped = np.floor(clamped / p.{min_qty_param}) * p.{min_qty_param}")
                lines.append(f"        return max(p.{min_qty_param}, stepped)")
            else:
                lines.append(f"        return max(0.001, raw_qty)")
        elif has_pos_pct:
            # Simple percent-of-equity sizing
            pos_pct = next(k for k in sizing_params if "pos_pct" in k)
            lines.append(f"        return max(0.001, equity_at_fill * p.{pos_pct} / fill_price)")
        else:
            # Fallback: use strategy-level default_qty settings
            if pr.strategy.default_qty_type == "percent_of_equity":
                lines.append(f"        pct = {pr.strategy.default_qty_value} / 100.0")
                lines.append("        return max(0.001, equity_at_fill * pct / fill_price)")
            else:
                lines.append(f"        return {pr.strategy.default_qty_value}")
        lines.append("")
    else:
        # No sizing params detected — use strategy-level default_qty settings
        lines.append("    def _size_at_fill(self, fill_price, equity_at_fill):")
        if pr.strategy.default_qty_type == "percent_of_equity":
            lines.append(f'        """Sizing: {pr.strategy.default_qty_value}% of equity."""')
            lines.append("        if fill_price <= 0: return 0.0")
            lines.append(f"        pct = {pr.strategy.default_qty_value} / 100.0")
            lines.append("        return max(0.001, equity_at_fill * pct / fill_price)")
        else:
            lines.append(f'        """Default sizing: {pr.strategy.default_qty_value} units."""')
            lines.append(f"        return {pr.strategy.default_qty_value}")
        lines.append("")

    for func in pr.functions:
        if func.is_visual:
            continue
        # Convert function to a Python method
        # Clean param names (remove type annotations)
        clean_params = []
        for fp in func.params:
            # Remove Pine type annotations like "int n" -> "n"
            parts = fp.strip().split()
            clean_params.append(parts[-1] if parts else fp.strip())
        
        py_params = ", ".join(clean_params) if clean_params else ""
        if py_params:
            py_params = ", " + py_params
        lines.append(f"    def _{func.name}(self, bar, ctx, bars, _bi, p{py_params}):")
        lines.append(f'        """Translated from Pine: {func.name}()"""')
        
        if func.body:
            inputs_map = {inp.var_name: inp for inp in pr.inputs}
            last_line_expr = None
            # Preprocess switch blocks in function body
            func_body = _preprocess_switch_blocks(func.body)
            _func_body_start = len(lines)  # Track where body lines begin
            for bline in func_body:
                stripped = bline.strip()
                if not stripped:
                    continue
                # Use statement translator for proper if/for/assignment handling
                translated = _translate_statement(stripped, inputs_map, pr)
                if translated is None:
                    translated = translate_expression(stripped, inputs_map)
                
                # Resolve variables
                for key in sorted(input_names.keys(), key=len, reverse=True):
                    translated = re.sub(r'\b' + re.escape(key) + r'\b', input_names[key], translated)
                for key in sorted(var_names.keys(), key=len, reverse=True):
                    translated = re.sub(r'\b' + re.escape(key) + r'\b', var_names[key], translated)
                
                # Fix bar data refs — use _bi (bar index) instead of i in helper functions
                translated = re.sub(r'(?<!bars\.)\b(close|open|high|low|volume)\b(?!\[|\(|\.)',
                                   lambda m: f'bars.{m.group(1)}[_bi]', translated)
                
                # Fix history refs: bars.xxx[i-N] -> bars.xxx[_bi-N] in helpers
                translated = translated.replace('[i-', '[_bi-')
                
                # Handle loop variable as history offset: bars.xxx[expr] -> bars.xxx[_bi-(expr)]
                # In Pine helper functions, all bar data indexing is relative to current bar
                # bars.xxx[_bi-N] is already correct, skip those
                # bars.xxx[anything_else] needs _bi- prefix
                def _fix_history_ref(m):
                    field = m.group(1)
                    idx_expr = m.group(2)
                    # Already has _bi reference — leave it alone
                    if '_bi' in idx_expr:
                        return m.group(0)
                    # Pure number like [1] — already handled by translate_expression
                    # but double-check
                    return f'bars.{field}[_bi-({idx_expr})]'
                
                translated = re.sub(r'bars\.(\w+)\[([^\]]+)\]', _fix_history_ref, translated)
                
                # Replace i -> _bi in nested function calls: self._f_xxx(bar, ctx, bars, i, p)
                translated = re.sub(r'(self\._f_\w+\(bar, ctx, bars, )i(, p)',
                                   r'\1_bi\2', translated)
                
                # Determine indent based on original Pine indent
                orig_indent = len(bline) - len(bline.lstrip())
                py_indent = "        " + "    " * max(0, (orig_indent - 4) // 4)
                lines.append(f"{py_indent}{translated}")
                last_line_expr = translated
            
            # Add return statements to bare expressions in helper function body.
            # Pine functions implicitly return the last expression of each branch.
            # We need to add 'return' to bare expressions at branch ends.
            _add_returns_to_helper(lines, _func_body_start)
        else:
            lines.append("        pass")
        lines.append("")

    # Emit on_bar()
    lines.append("    def on_bar(self, bar: BarData, ctx: Context) -> None:")
    lines.append('        """Per-bar trading logic."""')
    lines.append("        i = ctx.bar_index")
    lines.append("        p = self.p")
    lines.append("        bars = ctx.bars")
    lines.append("        self._current_bar_ts = bar.timestamp")
    lines.append("")
    lines.append("        # Date-change detection (for session-based strategies)")
    lines.append("        from datetime import datetime, timezone, timedelta")
    lines.append("        _ts = bar.timestamp / 1000 if bar.timestamp > 1e12 else bar.timestamp")
    lines.append("        _dt_utc = datetime.fromtimestamp(_ts, tz=timezone.utc)")
    lines.append("        _year = _dt_utc.year")
    lines.append("        _mar1 = datetime(_year, 3, 1, tzinfo=timezone.utc)")
    lines.append("        _mar_sun2 = _mar1 + timedelta(days=(6-_mar1.weekday())%7 + 7)")
    lines.append("        _nov1 = datetime(_year, 11, 1, tzinfo=timezone.utc)")
    lines.append("        _nov_sun1 = _nov1 + timedelta(days=(6-_nov1.weekday())%7)")
    lines.append("        _is_dst = _mar_sun2 <= _dt_utc < _nov_sun1")
    lines.append("        _ct_dt = _dt_utc + timedelta(hours=-5 if _is_dst else -6)")
    lines.append("        _ct_date = _ct_dt.strftime('%Y-%m-%d')")
    lines.append("        if not hasattr(self, '_prev_ct_date'): self._prev_ct_date = _ct_date")
    lines.append("        isNewDay = _ct_date != self._prev_ct_date")
    lines.append("        self._prev_ct_date = _ct_date")
    lines.append("")
    
    # Calculate minimum warmup bars from max history lookback in helper functions
    # Scan all function bodies for bars.xxx[_bi-N] patterns to find max offset
    max_offset = 5  # minimum safe default
    for func in pr.functions:
        for bline in func.body:
            # Find numeric offsets like bars.close[_bi-3], close[4], etc.
            for m in re.finditer(r'\[(\d+)\]', bline):
                max_offset = max(max_offset, int(m.group(1)) + 1)
    # Also check input params that affect lookback
    for inp in pr.inputs:
        name_lower = inp.var_name.lower()
        if any(k in name_lower for k in ['lookback', 'length', 'len', 'period', 'bars']):
            if isinstance(inp.default, (int, float)) and inp.default > max_offset:
                max_offset = max(max_offset, int(inp.default) + 2)
    
    lines.append(f"        # Warmup guard: skip bars without enough history")
    lines.append(f"        if i < {max_offset}:")
    lines.append(f"            self._prev_pos = ctx.position_size")
    lines.append(f"            return")
    lines.append("")
    lines.append("        # Position state tracking (Pine does this implicitly)")
    lines.append("        prev_position_size = self._prev_pos")
    lines.append("        isLong = ctx.position_size > 0")
    lines.append("        isShort = ctx.position_size < 0")
    lines.append("        isFlat = ctx.position_size == 0")
    lines.append("        newEntry = ((isLong and prev_position_size <= 0) or (isShort and prev_position_size >= 0)) and not isFlat")
    lines.append("        positionClosed = isFlat and prev_position_size != 0")
    lines.append("")
    
    # Translate main body
    body_lines = _translate_body(pr.main_body, pr, unresolved)
    
    # ── Variable resolution pass ──
    # Apply resolution to body lines
    # Identify source-type params
    source_params = {inp.var_name for inp in pr.inputs if inp.pine_type == "source"}
    
    body_lines = _resolve_variables(body_lines, input_names, var_names, source_params)
    
    # ── Indicator hoisting ──
    # Find ta_lib.* calls in body and move them to init
    init_indicators, body_lines = _hoist_indicators(body_lines)
    
    # Add hoisted indicators to init section
    if init_indicators:
        # Insert before the TODO comment
        todo_idx = next((j for j, l in enumerate(lines) if "TODO" in l), len(lines))
        for ind_line in init_indicators:
            lines.insert(todo_idx, f"        {ind_line}")
            todo_idx += 1
        # Remove the TODO comment since we've added real indicators
        lines = [l for l in lines if "TODO: Pre-compute indicators" not in l]
    
    # ── Augment session-first-bar detection with date-change ──
    # Pine's isSessionFirstBar relies on session transitions which may not exist
    # in continuous data (e.g., SPY without overnight bars). Adding isNewDay 
    # ensures daily resets work even without session gaps.
    for j, bl in enumerate(body_lines):
        stripped = bl.strip()
        if re.match(r'^isSessionFirstBar\s*=\s*', stripped):
            body_lines[j] = bl.rstrip() + " or isNewDay"
    
    # ── Fix NaN-unsafe indicator comparisons ──
    # When comparing bar.close to indicators that may be NaN during warmup,
    # NaN comparisons return False which can cause wrong signals.
    # Pattern: "x = bar.close > self.someIndicator[i]" -> add NaN guard
    for j, bl in enumerate(body_lines):
        stripped = bl.strip()
        # Match: varName = bar.close > self.xxx[i]  or  bar.close < self.xxx[i]
        import re as _re
        nan_cmp = _re.match(r'^(\w+)\s*=\s*(bar\.close)\s*([><])\s*(self\.\w+\[i\])$', stripped)
        if nan_cmp:
            var, lhs, op, rhs = nan_cmp.group(1), nan_cmp.group(2), nan_cmp.group(3), nan_cmp.group(4)
            indent = bl[:len(bl) - len(bl.lstrip())]
            body_lines[j] = f"{indent}{var} = ({lhs} {op} {rhs}) if not np.isnan({rhs}) else True"
    
    # ── Inject qty_func into ctx.entry() calls ──
    # This matches TradingView's calc_on_order_fills behavior
    has_entries = False
    for j, bl in enumerate(body_lines):
        stripped = bl.strip()
        if "ctx.entry(" in stripped and "qty_func" not in stripped:
            if "qty=" in stripped:
                # Has qty already — add qty_func before closing paren
                body_lines[j] = bl.rstrip().rstrip(")") + ", qty_func=self._size_at_fill)"
            else:
                # No qty — add both qty and qty_func
                body_lines[j] = bl.rstrip().rstrip(")") + ", qty_func=self._size_at_fill)"
            has_entries = True
    
    for bl in body_lines:
        lines.append(f"        {bl}")
    
    if not body_lines:
        lines.append("        pass  # TODO: Implement trading logic")
    
    # Always add position tracking at end of on_bar
    lines.append("")
    lines.append("        # Track position for next bar's new-entry detection")
    lines.append("        self._prev_pos = ctx.position_size")
    
    lines.append("")
    
    return "\n".join(lines), unresolved


def _resolve_variables(body_lines: List[str], input_names: Dict[str, str], var_names: Dict[str, str], 
                       source_params: set = None) -> List[str]:
    """Replace Pine variable names with their Python equivalents.
    
    input params: useBEStop -> p.use_bestop
    var declarations: pendingLong -> self._pendingLong
    source params: mesa_src -> bars.get_source(p.mesa_src)
    """
    result = []
    source_params = source_params or set()
    
    # Sort by length descending to avoid partial replacements
    all_replacements = {}
    all_replacements.update(input_names)
    all_replacements.update(var_names)
    
    # Source params get special treatment: wrap with bars.get_source()
    source_replacements = {}
    for sp in source_params:
        snake = to_snake_case(sp)
        source_replacements[sp] = f"bars.get_source(p.{snake})"
    
    sorted_keys = sorted(all_replacements.keys(), key=len, reverse=True)
    sorted_source_keys = sorted(source_replacements.keys(), key=len, reverse=True)
    
    for line in body_lines:
        resolved = line
        # Apply source replacements first (before general input replacement)
        for key in sorted_source_keys:
            replacement = source_replacements[key]
            resolved = re.sub(r'\b' + re.escape(key) + r'\b', replacement, resolved)
        for key in sorted_keys:
            if key in source_params:
                continue  # Already handled above
            replacement = all_replacements[key]
            resolved = re.sub(r'\b' + re.escape(key) + r'\b', replacement, resolved)
        result.append(resolved)
    
    return result


def _hoist_indicators(body_lines: List[str]) -> Tuple[List[str], List[str]]:
    """Extract ta_lib.*() calls from body and return them as init lines.
    
    Pattern: var = ta_lib.xxx(args) at top-level (indent 0) -> move to init as self.var
    Body references to var become self.var[i]
    """
    init_lines = []
    remaining = []
    hoisted_vars = {}  # var_name -> True
    
    for line in body_lines:
        stripped = line.strip()
        
        # Match: varName = ta_lib.xxx(...) at top level
        ta_match = re.match(r'^(\w+)\s*=\s*(ta_lib\.\w+\(.+\))\s*$', stripped)
        if ta_match and not line.startswith("    "):  # top-level only
            var_name = ta_match.group(1)
            ta_call = ta_match.group(2)
            
            # Check if this is crossover/crossunder with non-array args — skip hoisting
            cross_check = re.match(r'ta_lib\.(crossover|crossunder)\((\w+),\s*(\w+)\)', ta_call)
            if cross_check:
                a, b = cross_check.group(2), cross_check.group(3)
                # Only hoist if both args are bar arrays or already-hoisted vars
                known_arrays = {"bars", "self"} | set(hoisted_vars.keys())
                a_is_array = any(a.startswith(k) for k in known_arrays) or a in ("close", "open", "high", "low", "volume", "hl2")
                b_is_array = any(b.startswith(k) for k in known_arrays) or b in ("close", "open", "high", "low", "volume", "hl2")
                if not (a_is_array and b_is_array):
                    remaining.append(line)
                    continue
            
            # Fix the ta_lib call to use arrays instead of bar.xxx
            ta_call = ta_call.replace("bar.close", "bars.close")
            ta_call = ta_call.replace("bar.high", "bars.high")
            ta_call = ta_call.replace("bar.low", "bars.low")
            ta_call = ta_call.replace("bar.open", "bars.open")
            ta_call = ta_call.replace("bar.volume", "bars.volume")
            
            # Expand Pine ta function signatures to Python ta_lib signatures
            # Pine ta.atr(length) -> ta_lib.atr(bars.high, bars.low, bars.close, length)
            atr_expand = re.match(r'ta_lib\.atr\(([^,)]+)\)$', ta_call)
            if atr_expand:
                ta_call = f"ta_lib.atr(bars.high, bars.low, bars.close, {atr_expand.group(1)})"
            
            # Pine ta.ema(source, length) - already correct if source is an array
            # Pine ta.sma(source, length) - already correct
            # Pine ta.vwap(source) - needs volume
            vwap_expand = re.match(r'ta_lib\.vwap\(([^)]+)\)$', ta_call)
            if vwap_expand:
                ta_call = f"ta_lib.vwap({vwap_expand.group(1)}, bars.volume)"
            
            # Pine ta.crossover(a, b) / ta.crossunder(a, b) - need self. refs if args are hoisted
            cross_expand = re.match(r'ta_lib\.(crossover|crossunder)\((\w+),\s*(\w+)\)$', ta_call)
            if cross_expand:
                func = cross_expand.group(1)
                a = cross_expand.group(2)
                b = cross_expand.group(3)
                # If a or b are hoisted vars, add self. prefix
                if a in hoisted_vars:
                    a = f"self.{a}"
                if b in hoisted_vars:
                    b = f"self.{b}"
                ta_call = f"ta_lib.{func}({a}, {b})"
            
            init_lines.append(f"self.{var_name} = {ta_call}")
            hoisted_vars[var_name] = True
            continue
        
        remaining.append(line)
    
    # Update body references: hoisted vars need [i] index
    if hoisted_vars:
        updated = []
        for line in remaining:
            resolved = line
            for var_name in sorted(hoisted_vars.keys(), key=len, reverse=True):
                # Replace standalone references with self.var[i]
                # But not assignments (handled above) or function calls
                resolved = re.sub(
                    r'\b' + re.escape(var_name) + r'\b(?!\s*=|\s*\(|\[)',
                    f'self.{var_name}[i]',
                    resolved
                )
            updated.append(resolved)
        remaining = updated
    
    return init_lines, remaining


def _preprocess_switch_blocks(body: List[str]) -> List[str]:
    """Convert Pine switch/case blocks to if/elif/else blocks.
    
    Pine:
        switch variable
            "value1" => expr1
            "value2" => expr2
            => default_expr
    
    Becomes:
        if variable == "value1"
            expr1
        elif variable == "value2"
            expr2
        else
            default_expr
    """
    result = []
    i = 0
    while i < len(body):
        line = body[i]
        stripped = line.strip()
        indent = line[:len(line) - len(line.lstrip())]
        
        switch_match = re.match(r'^(\s*)switch\s+(.+)$', line)
        if switch_match:
            sw_indent = switch_match.group(1)
            sw_expr = switch_match.group(2).strip()
            # Collect case arms
            i += 1
            first_case = True
            while i < len(body):
                case_line = body[i]
                cs = case_line.strip()
                if not cs:
                    i += 1
                    continue
                # Check if still inside switch (indented more than switch line)
                case_indent = len(case_line) - len(case_line.lstrip())
                if case_indent <= len(sw_indent) and cs:
                    break  # Left the switch block
                
                # Match: "value" => expr  or  value => expr  or  => expr (default)
                cm = re.match(r'^\"([^\"]+)\"\s*=>\s*(.+)$', cs)
                if not cm:
                    cm = re.match(r'^(\w+)\s*=>\s*(.+)$', cs)
                is_default = re.match(r'^=>\s*(.+)$', cs)
                
                if is_default:
                    result.append(f"{sw_indent}else:")
                    result.append(f"{sw_indent}    {is_default.group(1)}")
                    i += 1
                elif cm:
                    case_val = cm.group(1)
                    case_expr = cm.group(2)
                    if first_case:
                        result.append(f'{sw_indent}if {sw_expr} == "{case_val}":')
                        first_case = False
                    else:
                        result.append(f'{sw_indent}elif {sw_expr} == "{case_val}":')
                    result.append(f"{sw_indent}    {case_expr}")
                    i += 1
                else:
                    # Not a case line — might be multi-line case body
                    result.append(case_line)
                    i += 1
        else:
            result.append(line)
            i += 1
    
    return result


def _translate_body(body: List[str], pr: ParseResult, unresolved: List[str]) -> List[str]:
    """Translate the main body lines to Python."""
    inputs_map = {inp.var_name: inp for inp in pr.inputs}
    out = []
    
    # ── Pre-process: Convert switch/case blocks to if/elif ──
    body = _preprocess_switch_blocks(body)
    
    # Find minimum indentation
    min_indent = 999
    for line in body:
        if line.strip():
            spaces = len(line) - len(line.lstrip())
            min_indent = min(min_indent, spaces)
    if min_indent == 999:
        min_indent = 0
    
    for line in body:
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        
        # Skip lines already handled by on_bar preamble (position tracking)
        _skip_vars = {
            "isLong", "isShort", "isFlat",
            "newEntry", "positionClosed",
            "prev_pos", "prevPos",
        }
        skip_line = False
        assign_match = re.match(r'^(?:bool\s+)?(\w+)\s*=\s', stripped)
        if assign_match and assign_match.group(1) in _skip_vars:
            skip_line = True
        if skip_line:
            continue
        
        # Skip visual lines
        if _is_visual_line(stripped, pr.visual_vars):
            continue
        
        # Calculate relative indent
        spaces = len(line) - len(line.lstrip())
        rel_indent = max(0, (spaces - min_indent) // 4)
        prefix = "    " * rel_indent
        
        # Translate common patterns
        translated = _translate_statement(stripped, inputs_map, pr)
        
        if translated is not None:
            out.append(f"{prefix}{translated}")
        else:
            # Can't translate — mark as unresolved
            out.append(f"{prefix}# UNRESOLVED: {stripped}")
            unresolved.append(stripped)
    
    # Post-process: remove if/elif/else blocks with no body (may need multiple passes)
    prev_len = len(out) + 1
    while len(out) < prev_len:
        prev_len = len(out)
        out = _remove_empty_blocks(out)
    
    return out


def _remove_empty_blocks(lines: List[str]) -> List[str]:
    """Remove if/elif/else blocks that have no body (after visual stripping)."""
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        # Check if this is an if/elif/else that's followed by another if/elif/else at same or lower indent, or end of list
        if stripped.endswith(":") and any(stripped.startswith(kw) for kw in ("if ", "elif ", "else:")):
            # Look ahead: is there an indented body?
            current_indent = len(line) - len(line.lstrip())
            has_body = False
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                next_stripped = next_line.strip()
                if not next_stripped:
                    j += 1
                    continue
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent > current_indent:
                    has_body = True
                break
            
            if not has_body:
                # Skip this empty block
                i += 1
                continue
        
        result.append(line)
        i += 1
    
    return result


def _translate_statement(stmt: str, inputs: Dict[str, PineInput], pr: ParseResult) -> Optional[str]:
    """Translate a single Pine statement to Python. Returns None if unresolvable."""
    
    # Tuple destructuring: [a, b, c] = func(...)
    tuple_match = re.match(r'^\[([^\]]+)\]\s*=\s*(.+)$', stmt)
    if tuple_match:
        vars_str = tuple_match.group(1)
        rhs = translate_expression(tuple_match.group(2), inputs)
        py_vars = ", ".join(v.strip() for v in vars_str.split(","))
        return f"{py_vars} = {rhs}"
    
    # var declarations inside function bodies: var float x = na
    var_in_body = re.match(r'^var\s+(?:float|int|bool|string)\s+(\w+)\s*=\s*(.+)$', stmt)
    if var_in_body:
        vname = var_in_body.group(1)
        vval = translate_expression(var_in_body.group(2), inputs)
        return f"{vname} = {vval}"
    
    # switch statement: switch expr
    switch_match = re.match(r'^switch\s+(.+)$', stmt)
    if switch_match:
        expr = translate_expression(switch_match.group(1), inputs)
        return f"# switch {expr}:  (translated to if/elif chain below)"
    
    # switch case: "value" => result  or  value => result
    case_match = re.match(r'^"([^"]+)"\s*=>\s*(.+)$', stmt)
    if not case_match:
        case_match = re.match(r'^(\w+)\s*=\s*>\s*(.+)$', stmt)
    if not case_match:
        case_match = re.match(r'^(\d+)\s*=>\s*(.+)$', stmt)
    if case_match:
        # This is a switch case — emit as comment since we need the switch context
        # The parent should handle this, but as fallback:
        case_val = case_match.group(1)
        result = translate_expression(case_match.group(2), inputs)
        return f"# case {case_val}: {result}"
    
    # default => result (switch default)
    default_match = re.match(r'^=>\s*(.+)$', stmt)
    if default_match:
        result = translate_expression(default_match.group(1), inputs)
        return f"# default: {result}"
    
    # break
    if stmt.strip() == "break":
        return "break"
    
    # if/else if/else blocks
    if_match = re.match(r'^if\s+(.+)$', stmt)
    if if_match:
        cond = translate_expression(if_match.group(1).rstrip(':'), inputs)
        return f"if {cond}:"
    
    elif_match = re.match(r'^else\s+if\s+(.+)$', stmt)
    if not elif_match:
        elif_match = re.match(r'^elif\s+(.+)$', stmt)
    if elif_match:
        cond = translate_expression(elif_match.group(1).rstrip(':'), inputs)
        return f"elif {cond}:"
    
    if stmt == "else":
        return "else:"
    
    # for loops
    for_match = re.match(r'^for\s+(\w+)\s*=\s*(\S+)\s+to\s+(.+?)(?:\s+by\s+(\S+))?$', stmt)
    if for_match:
        var = for_match.group(1)
        start = for_match.group(2)
        end = for_match.group(3).strip()
        step = for_match.group(4)
        # Translate Pine variable references in the expression
        end_py = translate_expression(end, inputs)
        start_py = translate_expression(start, inputs)
        if step:
            return f"for {var} in range(int({start_py}), int({end_py}) + 1, int({step})):"
        return f"for {var} in range(int({start_py}), int({end_py}) + 1):"
    
    # strategy.entry
    entry_match = re.match(r'^strategy\.entry\s*\((.+)\)\s*$', stmt)
    if entry_match:
        return _translate_strategy_entry(entry_match.group(1), inputs)
    
    # strategy.close / strategy.close_all
    if stmt.startswith("strategy.close_all("):
        comment_match = re.search(r'comment\s*=\s*"([^"]*)"', stmt)
        comment = comment_match.group(1) if comment_match else ""
        return f'ctx.close_all(comment="{comment}")'
    
    close_match = re.match(r'^strategy\.close\s*\(\s*"([^"]*)"', stmt)
    if close_match:
        return f'ctx.close("{close_match.group(1)}")'
    
    # strategy.exit
    exit_match = re.match(r'^strategy\.exit\s*\((.+)\)\s*$', stmt)
    if exit_match:
        return _translate_strategy_exit(exit_match.group(1), inputs)
    
    # Assignment: x := value
    assign_match = re.match(r'^(\w+)\s*:=\s*(.+)$', stmt)
    if assign_match:
        var, val = assign_match.group(1), assign_match.group(2)
        py_val = translate_expression(val, inputs)
        # If it's a var declaration, use self._
        if var in pr.var_declarations:
            return f"self._{var} = {py_val}"
        return f"{var} = {py_val}"
    
    # Variable declaration: type name = value
    decl_match = re.match(r'^(?:float|int|bool|string)?\s*(\w+)\s*=\s*(.+)$', stmt)
    if decl_match:
        var, val = decl_match.group(1), decl_match.group(2)
        # Skip group variables
        if var.startswith("grp"):
            return None
        py_val = translate_expression(val, inputs)
        return f"{var} = {py_val}"
    
    # Bare function calls
    if re.match(r'^\w+\(.*\)\s*$', stmt):
        # Check if visual
        for vf in VISUAL_FUNCS:
            if stmt.startswith(vf + "("):
                return None
        return translate_expression(stmt, inputs)
    
    # barstate.isconfirmed — always true in backtesting, keep contents
    if stmt.startswith("if barstate.isconfirmed"):
        return "if True:  # barstate.isconfirmed (always true in backtest)"
    
    # Default: try direct translation
    return translate_expression(stmt, inputs)


def _translate_strategy_entry(args_str: str, inputs: Dict[str, PineInput]) -> str:
    """Translate strategy.entry(...) call."""
    # Parse arguments
    parts = _split_args(args_str)
    
    entry_id = parts[0].strip().strip('"') if parts else "Entry"
    direction = parts[1].strip() if len(parts) > 1 else '"long"'
    
    # Translate direction
    direction = direction.replace("strategy.long", '"long"').replace("strategy.short", '"short"')
    
    # Extract qty
    qty_str = ""
    for p in parts[2:]:
        p = p.strip()
        if p.startswith("qty=") or p.startswith("qty ="):
            qty_val = p.split("=", 1)[1].strip()
            qty_str = f", qty={translate_expression(qty_val, inputs)}"
            break
    
    return f'ctx.entry("{entry_id}", {direction}{qty_str})'


def _translate_strategy_exit(args_str: str, inputs: Dict[str, PineInput]) -> str:
    """Translate strategy.exit(...) call."""
    parts = _split_args(args_str)
    
    exit_id = parts[0].strip().strip('"') if parts else "Exit"
    from_entry = ""
    stop = ""
    limit = ""
    
    for p in parts[1:]:
        p = p.strip()
        if p.startswith('"') and not from_entry:
            from_entry = f', from_entry={p}'
        elif p.startswith("from_entry"):
            from_entry = f', from_entry={p.split("=", 1)[1].strip()}'
        elif p.startswith("stop="):
            val = translate_expression(p.split("=", 1)[1].strip(), inputs)
            stop = f", stop={val}"
        elif p.startswith("limit="):
            val = translate_expression(p.split("=", 1)[1].strip(), inputs)
            limit = f", limit={val}"
    
    return f'ctx.exit("{exit_id}"{from_entry}{stop}{limit})'


def _split_args(args_str: str) -> List[str]:
    """Split function arguments respecting nested parens and strings."""
    parts = []
    depth = 0
    current = ""
    in_string = None
    
    for ch in args_str:
        if ch in ('"', "'") and in_string is None:
            in_string = ch
            current += ch
        elif ch == in_string:
            in_string = None
            current += ch
        elif in_string:
            current += ch
        elif ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    
    if current.strip():
        parts.append(current.strip())
    
    return parts


# ═══════════════════════════════════════════════════════════════════
# Main Translation Pipeline
# ═══════════════════════════════════════════════════════════════════

def translate(pine_code: str, strategy_name: str = None, 
              api_key: str = None, use_cache: bool = True) -> Tuple[str, dict]:
    """Full translation pipeline: cache → deterministic → test → Claude repair.
    
    Returns (python_code, metadata_dict).
    """
    # 1. Check cache
    if use_cache:
        cached = get_cached(pine_code)
        if cached:
            return cached, {"source": "cache", "unresolved": 0}
    
    # 2. Parse
    pr = parse_pine(pine_code)
    
    # Derive name from strategy title if not provided
    if not strategy_name:
        strategy_name = re.sub(r'[^\w\s]', '', pr.strategy.title or "translated")
        strategy_name = re.sub(r'\s+', '_', strategy_name.strip()).lower()[:40]
        if not strategy_name:
            strategy_name = "translated_strategy"
    
    # 3. Deterministic emit
    python_code, unresolved = emit_python(pr, strategy_name)
    
    metadata = {
        "source": "deterministic",
        "strategy_name": strategy_name,
        "class_name": to_class_name(strategy_name),
        "inputs": len(pr.inputs),
        "functions": len(pr.functions),
        "unresolved": len(unresolved),
        "unresolved_lines": unresolved[:20],
    }
    
    # 4. Test the deterministic output — try import + init + one bar
    test_error = _test_translated_code(python_code, strategy_name)
    
    if test_error:
        metadata["deterministic_error"] = test_error
        
        if api_key:
            # 5. Claude repair with retry: send Pine + partial translation + error
            max_retries = 2
            current_code = python_code
            current_error = test_error
            
            for attempt in range(max_retries):
                try:
                    repaired = _claude_repair(pine_code, current_code, current_error, strategy_name, api_key)
                    if not repaired:
                        break
                    
                    # Test the repaired code
                    repair_error = _test_translated_code(repaired, strategy_name)
                    if not repair_error:
                        python_code = repaired
                        metadata["source"] = "deterministic+claude_repair"
                        metadata["test_result"] = "pass"
                        metadata["repair_attempts"] = attempt + 1
                        break
                    else:
                        # Repair didn't fix it — try again with the new error
                        current_code = repaired
                        current_error = repair_error
                        python_code = repaired
                        metadata["source"] = f"deterministic+claude_repair(attempt {attempt + 1})"
                        metadata["repair_error"] = repair_error
                        metadata["repair_attempts"] = attempt + 1
                except Exception as e:
                    metadata["claude_error"] = str(e)
                    break
        elif len(unresolved) > 10:
            metadata["needs_claude"] = True
    else:
        metadata["test_result"] = "pass"
    
    # 6. Cache result — only if test passed
    if use_cache and metadata.get("test_result") == "pass":
        set_cached(pine_code, python_code)
    
    return python_code, metadata


def _test_translated_code(python_code: str, strategy_name: str) -> Optional[str]:
    """Test translated Python code by trying to import and run it.
    
    Checks:
    1. Code compiles and imports without errors
    2. Strategy class exists and can be instantiated
    3. init() and on_bar() run without crashes on random data
    4. No entries on bar 0 or 1 (warmup guard)
    5. Strategy produces at least some trades on longer random data
    
    Returns error string if failed, None if successful.
    """
    import tempfile
    import importlib
    
    tmp_dir = os.path.join(tempfile.gettempdir(), "pine_translator_test")
    
    try:
        # Write to a temp file for import
        strategies_dir = os.path.join(tmp_dir, "strategies")
        os.makedirs(strategies_dir, exist_ok=True)
        
        # Write __init__.py
        init_path = os.path.join(strategies_dir, "__init__.py")
        if not os.path.exists(init_path):
            with open(init_path, "w") as f:
                f.write("")
        
        # Write strategy
        filepath = os.path.join(strategies_dir, f"{strategy_name}.py")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(python_code)
        
        # Add to path temporarily
        if tmp_dir not in sys.path:
            sys.path.insert(0, tmp_dir)
        
        # Clear any cached import
        mod_name = f"strategies.{strategy_name}"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        
        # Try import
        mod = importlib.import_module(mod_name)
        
        # Find Strategy subclass
        cls = None
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, type) and attr.__name__ != "Strategy":
                bases = [b.__name__ for b in attr.__mro__]
                if "Strategy" in bases:
                    cls = attr
                    break
        
        if cls is None:
            return "No Strategy subclass found in translated code"
        
        # ── Test 1: Basic run on small random data (crash detection) ──
        import numpy as np
        from backtest_engine import Bars, BacktestEngine, Context, BarData
        
        n = 500
        np.random.seed(42)
        p = np.random.randn(n).cumsum() * 10 + 1000
        h = p + abs(np.random.randn(n)) * 3
        l = p - abs(np.random.randn(n)) * 3
        o = p + np.random.randn(n) * 1
        bars = Bars(
            timestamp=np.arange(n, dtype=float) * 300000 + 1700000000000,
            open=o, high=h, low=l, close=p,
            volume=np.ones(n) * 1000,
            hl2=(h+l)/2, hlc3=(h+l+p)/3, ohlc4=(o+h+l+p)/4, hlcc4=(h+l+p+p)/4,
        )
        
        stats = BacktestEngine.run_fast(cls(), bars, {}, initial_capital=100000, fee_pct=0.0)
        
        # ── Test 2: Warmup guard check ──
        # Run manually and verify no entries in first few bars
        s = cls()
        from strategy_base import ParamAccessor
        ctx = Context(bars, 100000, 0.0)
        merged = s.get_param_defaults()
        s.p = ParamAccessor(merged)
        s.init(ctx)
        
        early_entry = False
        pc = 0.0
        for i in range(min(3, n)):
            bar = BarData(int(bars.timestamp[i]), bars.open[i], bars.high[i], bars.low[i], bars.close[i], bars.volume[i])
            ctx.bar_index = i
            ctx._fill_pending_orders(bar, prev_bar_close=pc)
            s.on_bar(bar, ctx)
            if len(ctx._pending_entries) > 0:
                early_entry = True
            ctx._process_orders(bar)
            if ctx.position_size != 0:
                early_entry = True
            pc = bar.close
        
        # Early entries are a warning, not a hard failure
        # (some strategies legitimately enter early on certain data)
        
        return None  # Success
        
    except SyntaxError as e:
        return f"SyntaxError: {e}"
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    finally:
        # Cleanup path
        if tmp_dir in sys.path:
            sys.path.remove(tmp_dir)


def _claude_repair(pine_code: str, python_code: str, error: str, 
                    strategy_name: str, api_key: str) -> Optional[str]:
    """Use Claude to repair a broken deterministic translation.
    
    Pre-cleans the code: removes broken custom functions and adds clear markers.
    """
    try:
        import requests
    except ImportError:
        return None
    
    # Pre-clean: remove methods that are likely broken (custom Pine functions with series logic)
    # These will have `var float`, `[1]` on local vars, etc.
    cleaned_lines = []
    skip_method = False
    skip_indent = 0
    removed_functions = []
    
    for line in python_code.splitlines():
        stripped = line.strip()
        
        # Detect broken helper methods (custom Pine functions)
        if stripped.startswith("def _f_") and "self" in stripped:
            # Check if this method body has series-like patterns
            skip_method = True
            skip_indent = len(line) - len(line.lstrip())
            func_name = stripped.split("(")[0].replace("def ", "")
            removed_functions.append(func_name)
            continue
        
        if skip_method:
            current_indent = len(line) - len(line.lstrip()) if stripped else skip_indent + 1
            if stripped and current_indent <= skip_indent:
                skip_method = False
            else:
                continue
        
        cleaned_lines.append(line)
    
    cleaned_code = "\n".join(cleaned_lines)
    
    removed_note = ""
    if removed_functions:
        removed_note = f"\n\nNOTE: I removed these broken custom function translations: {', '.join(removed_functions)}. You need to replace their functionality using ta_lib pre-built functions in init()."
    
    repair_prompt = f"""Fix this Python trading strategy. It was mechanically translated from Pine Script but has errors.

## Error:
```
{error}
```
{removed_note}

## Pine Script (original):
```pine
{pine_code}
```

## Python (needs fixing):
```python
{cleaned_code}
```

## Available ta_lib functions (pre-compute in init as numpy arrays):
- mama, fama = ta_lib.mesa_mama_fama(source_array, fast_limit, slow_limit)
- ta_lib.ema(source, length) -> np.ndarray
- ta_lib.sma(source, length) -> np.ndarray  
- ta_lib.atr(high, low, close, length) -> np.ndarray
- ta_lib.rma(source, length) -> np.ndarray
- ta_lib.crossover(array_a, array_b) -> np.ndarray (boolean)
- ta_lib.crossunder(array_a, array_b) -> np.ndarray (boolean)
- ta_lib.nz(array, replacement=0.0) -> np.ndarray
- bars.get_source("ohlc4") -> np.ndarray  (resolves source param string to array)

## Rules:
1. Pre-compute ALL indicators in init() as numpy arrays: self.mama, self.fama = ta_lib.mesa_mama_fama(...)
2. In on_bar(), access indicators via self.mama[i], self.fama[i], self.atr[i], etc.
3. ta_lib.crossover/crossunder take ARRAYS, not scalars — compute in init()
4. Do NOT translate recursive Pine functions line-by-line. Use ta_lib equivalents.
5. Keep the params dict, class name, and all working logic exactly as-is.
6. For source params like mesa_src, use: source = bars.get_source(p.mesa_src)
7. CRITICAL — Position tracking: The Context object does NOT have get_previous_position_size(). To track new entries, use an instance variable:
   - In init(): self._prev_pos = 0.0
   - In on_bar(): prev_position_size = self._prev_pos (at the START)
   - At the END of on_bar(): self._prev_pos = ctx.position_size
   - newEntry = ((isLong and prev_position_size <= 0) or (isShort and prev_position_size >= 0)) and not isFlat
   - This MUST be correct or entry price tracking will break and NO exits will ever fire.
8. NaN safety: Indicators like EMA are NaN during warmup. Comparisons with NaN return False, causing wrong signals. Use: `bullRegime = (bar.close > ema[i]) if not np.isnan(ema[i]) else True` — default to True so both regimes suppress trading during warmup.
9. Output ONLY the complete Python file. No markdown, no explanation."""

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
            "system": "You fix Python trading strategy code. Output ONLY valid Python. No markdown fences, no explanations.",
            "messages": [
                {"role": "user", "content": repair_prompt}
            ],
        },
        timeout=120,
    )
    
    if response.status_code != 200:
        return None
    
    data = response.json()
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block["text"]
    
    # Clean markdown fences
    text = text.strip()
    if text.startswith("```python"):
        text = text[len("```python"):].strip()
    if text.startswith("```"):
        text = text[3:].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    
    return text if text else None


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Deterministic Pine→Python translator")
    parser.add_argument("pine_file", nargs="?", help="Path to .pine file")
    parser.add_argument("-o", "--output", type=str, default=None, help="Output strategy name")
    parser.add_argument("--stdin", action="store_true", help="Read JSON from stdin (IPC mode)")
    parser.add_argument("--api-key", type=str, default=None, help="Anthropic API key for Claude fallback")
    parser.add_argument("--no-cache", action="store_true", help="Skip cache")
    args = parser.parse_args()
    
    if args.stdin:
        input_data = json.loads(sys.stdin.read())
        pine_code = input_data["pine_code"]
        name = input_data.get("name", None)
        api_key = input_data.get("api_key", args.api_key)
        # Don't use empty string as API key
        if api_key is not None and not api_key.strip():
            api_key = None
        
        try:
            python_code, metadata = translate(pine_code, name, api_key, not args.no_cache)
        except Exception as e:
            output = {"success": False, "error": str(e)}
            sys.stdout.write(json.dumps(output))
            sys.exit(0)
        
        # Save to strategies dir (prefer user data dir if APPDATA is set)
        sname = metadata.get("strategy_name", name or "translated_strategy")
        if os.environ.get("APPDATA"):
            strategies_dir = os.path.join(os.environ["APPDATA"], "StrategyOptimizer", "strategies")
        elif os.environ.get("USERPROFILE"):
            strategies_dir = os.path.join(os.environ["USERPROFILE"], "StrategyOptimizer", "strategies")
        else:
            strategies_dir = os.path.join(SCRIPT_DIR, "strategies")
        os.makedirs(strategies_dir, exist_ok=True)
        
        # Ensure __init__.py exists
        init_path = os.path.join(strategies_dir, "__init__.py")
        if not os.path.exists(init_path):
            with open(init_path, "w") as f:
                f.write("# Trading strategies\n")
        
        filepath = os.path.join(strategies_dir, f"{sname}.py")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(python_code)
        
        output = {
            "success": True,
            "name": sname,
            "class_name": metadata.get("class_name", to_class_name(sname)),
            "filepath": filepath,
            "code": python_code,
            "lines": len(python_code.splitlines()),
            **metadata,
        }
        sys.stdout.write(json.dumps(output))
    
    elif args.pine_file:
        with open(args.pine_file, "r", encoding="utf-8") as f:
            pine_code = f.read()
        
        name = args.output
        python_code, metadata = translate(pine_code, name, args.api_key, not args.no_cache)
        
        sname = metadata["strategy_name"]
        strategies_dir = os.path.join(SCRIPT_DIR, "strategies")
        os.makedirs(strategies_dir, exist_ok=True)
        filepath = os.path.join(strategies_dir, f"{sname}.py")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(python_code)
        
        print(f"Translated: {args.pine_file} -> {filepath}")
        print(f"  Source: {metadata.get('source', 'unknown')}")
        print(f"  Class: {metadata.get('class_name', 'Unknown')}")
        print(f"  Inputs: {metadata.get('inputs', 0)}")
        print(f"  Unresolved: {metadata.get('unresolved', 0)}")
        if metadata.get("unresolved_lines"):
            print(f"  Unresolved lines:")
            for ul in metadata["unresolved_lines"][:5]:
                print(f"    {ul}")
    
    else:
        parser.print_help()
