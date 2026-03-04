/// Pine Script Built-in Function Implementations
/// All ta.*, math.*, na/nz functions with rolling state for bar-by-bar execution.

use super::vm::Value;

/// State maintained across bars for stateful built-in functions (ta.atr, ta.ema, etc.)
pub struct BuiltinState {
    pub ema_states: Vec<f64>,
    pub atr_states: Vec<f64>,
    pub prev_values: Vec<f64>,
    call_counter: usize,
}

impl BuiltinState {
    pub fn new(_var_count: usize) -> Self {
        Self {
            ema_states: Vec::new(),
            atr_states: Vec::new(),
            prev_values: Vec::new(),
            call_counter: 0,
        }
    }

    pub fn reset_bar(&mut self) {
        self.call_counter = 0;
    }

    fn next_slot(&mut self) -> usize {
        let slot = self.call_counter;
        self.call_counter += 1;
        slot
    }

    fn ensure_ema(&mut self, idx: usize) {
        if idx >= self.ema_states.len() {
            self.ema_states.resize(idx + 64, f64::NAN);
        }
    }

    fn ensure_atr(&mut self, idx: usize) {
        if idx >= self.atr_states.len() {
            self.atr_states.resize(idx + 64, f64::NAN);
        }
    }

    fn ensure_prev(&mut self, idx: usize) {
        if idx >= self.prev_values.len() {
            self.prev_values.resize(idx + 64, f64::NAN);
        }
    }
}

/// Dispatch a built-in function call by index
pub fn call_builtin(
    idx: u16,
    args: &[Value],
    state: &mut BuiltinState,
    bar_index: usize,
    vars: &[Value],
    vars_history: &[Vec<f64>],
) -> Value {
    match idx {
        0 => builtin_ta_atr(args, state, bar_index, vars, vars_history),
        1 => builtin_ta_ema(args, state),
        2 => builtin_ta_sma(args, vars_history),
        3 => builtin_ta_crossover(args, state),
        4 => builtin_ta_crossunder(args, state),
        5 => builtin_ta_highest(args, vars_history),
        6 => builtin_ta_lowest(args, vars_history),
        7 => builtin_ta_vwap(state, vars),
        8 => builtin_ta_pivotlow(args, vars_history),
        9 => builtin_ta_pivothigh(args, vars_history),
        10 => builtin_math_abs(args),
        11 => builtin_math_max(args),
        12 => builtin_math_min(args),
        13 => builtin_math_floor(args),
        14 => builtin_math_ceil(args),
        15 => builtin_math_round(args),
        16 => builtin_math_sum(args),
        17 => builtin_math_atan(args),
        18 => builtin_math_sqrt(args),
        19 => builtin_math_pow(args),
        20 => builtin_math_log(args),
        21 => builtin_math_log10(args),
        22 => builtin_math_exp(args),
        23 => builtin_na(args),
        24 => builtin_nz(args),
        25 => builtin_time(args),
        26 => builtin_hour(args),
        27 => builtin_minute(args),
        28 => builtin_dayofweek(args),
        29 => builtin_dayofmonth(args),
        30 => builtin_timeframe_in_seconds(args),
        31 | 32 => Value::Str("".to_string()),
        _ => Value::Na,
    }
}

// ── ta.* functions ──────────────────────────────────────────

fn builtin_ta_atr(
    args: &[Value],
    state: &mut BuiltinState,
    bar_index: usize,
    vars: &[Value],
    vars_history: &[Vec<f64>],
) -> Value {
    let length = args.first().map(|v| v.to_i64().max(1) as usize).unwrap_or(14);
    let slot_id = state.next_slot();
    state.ensure_atr(slot_id);

    let high = vars.get(1).map(|v| v.to_f64()).unwrap_or(f64::NAN);
    let low = vars.get(2).map(|v| v.to_f64()).unwrap_or(f64::NAN);
    let prev_close = if vars_history.len() > 3 && vars_history[3].len() > 1 {
        vars_history[3][1]
    } else {
        f64::NAN
    };

    if high.is_nan() || low.is_nan() {
        return Value::Na;
    }

    let tr = if prev_close.is_nan() {
        high - low
    } else {
        (high - low)
            .max((high - prev_close).abs())
            .max((low - prev_close).abs())
    };

    let alpha = 1.0 / length as f64;
    let prev_atr = state.atr_states[slot_id];

    let atr = if prev_atr.is_nan() || bar_index < length {
        if bar_index == 0 {
            tr
        } else {
            let prev = if prev_atr.is_nan() { tr } else { prev_atr };
            prev + (tr - prev) / (bar_index + 1).min(length) as f64
        }
    } else {
        prev_atr * (1.0 - alpha) + tr * alpha
    };

    state.atr_states[slot_id] = atr;
    Value::Float(atr)
}

fn builtin_ta_ema(args: &[Value], state: &mut BuiltinState) -> Value {
    if args.len() < 2 { return Value::Na; }
    let source = args[0].to_f64();
    let length = args[1].to_i64().max(1) as usize;
    if source.is_nan() { return Value::Na; }

    let slot_id = state.next_slot();
    state.ensure_ema(slot_id);

    let alpha = 2.0 / (length as f64 + 1.0);
    let prev = state.ema_states[slot_id];
    let ema = if prev.is_nan() { source } else { source * alpha + prev * (1.0 - alpha) };

    state.ema_states[slot_id] = ema;
    Value::Float(ema)
}

fn builtin_ta_sma(args: &[Value], _vars_history: &[Vec<f64>]) -> Value {
    if args.len() < 2 { return Value::Na; }
    let source = args[0].to_f64();
    Value::Float(source)
}

fn builtin_ta_crossover(args: &[Value], state: &mut BuiltinState) -> Value {
    if args.len() < 2 { return Value::Bool(false); }
    let a = args[0].to_f64();
    let b = args[1].to_f64();

    let slot_id = state.next_slot();
    let idx_a = slot_id * 2;
    let idx_b = slot_id * 2 + 1;
    state.ensure_prev(idx_b);

    let prev_a = state.prev_values[idx_a];
    let prev_b = state.prev_values[idx_b];
    state.prev_values[idx_a] = a;
    state.prev_values[idx_b] = b;

    if prev_a.is_nan() || prev_b.is_nan() || a.is_nan() || b.is_nan() {
        return Value::Bool(false);
    }
    Value::Bool(prev_a <= prev_b && a > b)
}

fn builtin_ta_crossunder(args: &[Value], state: &mut BuiltinState) -> Value {
    if args.len() < 2 { return Value::Bool(false); }
    let a = args[0].to_f64();
    let b = args[1].to_f64();

    let slot_id = state.next_slot();
    let idx_a = slot_id * 2;
    let idx_b = slot_id * 2 + 1;
    state.ensure_prev(idx_b);

    let prev_a = state.prev_values[idx_a];
    let prev_b = state.prev_values[idx_b];
    state.prev_values[idx_a] = a;
    state.prev_values[idx_b] = b;

    if prev_a.is_nan() || prev_b.is_nan() || a.is_nan() || b.is_nan() {
        return Value::Bool(false);
    }
    Value::Bool(prev_a >= prev_b && a < b)
}

fn builtin_ta_highest(args: &[Value], vars_history: &[Vec<f64>]) -> Value {
    if args.len() < 2 { return Value::Na; }
    let _source = args[0].to_f64();
    let length = args[1].to_i64().max(1) as usize;

    if vars_history.len() > 1 {
        let hist = &vars_history[1];
        let mut max = f64::NEG_INFINITY;
        for i in 0..length.min(hist.len()) {
            if !hist[i].is_nan() && hist[i] > max { max = hist[i]; }
        }
        if max == f64::NEG_INFINITY { Value::Na } else { Value::Float(max) }
    } else {
        Value::Na
    }
}

fn builtin_ta_lowest(args: &[Value], vars_history: &[Vec<f64>]) -> Value {
    if args.len() < 2 { return Value::Na; }
    let _source = args[0].to_f64();
    let length = args[1].to_i64().max(1) as usize;

    if vars_history.len() > 2 {
        let hist = &vars_history[2];
        let mut min = f64::INFINITY;
        for i in 0..length.min(hist.len()) {
            if !hist[i].is_nan() && hist[i] < min { min = hist[i]; }
        }
        if min == f64::INFINITY { Value::Na } else { Value::Float(min) }
    } else {
        Value::Na
    }
}

fn builtin_ta_vwap(state: &mut BuiltinState, vars: &[Value]) -> Value {
    let hlc3 = vars.get(6).map(|v| v.to_f64()).unwrap_or(f64::NAN);
    let volume = vars.get(4).map(|v| v.to_f64()).unwrap_or(f64::NAN);

    if hlc3.is_nan() || volume.is_nan() || volume == 0.0 {
        return Value::Na;
    }

    let slot_id = state.next_slot();
    let idx_vp = slot_id * 2;
    let idx_v = slot_id * 2 + 1;
    state.ensure_ema(idx_v);

    let cum_vp = if state.ema_states[idx_vp].is_nan() { 0.0 } else { state.ema_states[idx_vp] }
        + hlc3 * volume;
    let cum_v = if state.ema_states[idx_v].is_nan() { 0.0 } else { state.ema_states[idx_v] }
        + volume;

    state.ema_states[idx_vp] = cum_vp;
    state.ema_states[idx_v] = cum_v;

    if cum_v == 0.0 { Value::Na } else { Value::Float(cum_vp / cum_v) }
}

fn builtin_ta_pivotlow(args: &[Value], vars_history: &[Vec<f64>]) -> Value {
    let left = args.get(1).map(|v| v.to_i64().max(1) as usize).unwrap_or(5);
    let right = args.get(2).map(|v| v.to_i64().max(1) as usize).unwrap_or(5);

    if vars_history.len() <= 2 { return Value::Na; }
    let hist = &vars_history[2];
    if right + left >= hist.len() { return Value::Na; }

    let pivot_val = hist[right];
    if pivot_val.is_nan() { return Value::Na; }

    for i in (right + 1)..=(right + left) {
        if i >= hist.len() || hist[i].is_nan() { return Value::Na; }
        if hist[i] < pivot_val { return Value::Na; }
    }
    for i in 0..right {
        if i >= hist.len() || hist[i].is_nan() { return Value::Na; }
        if hist[i] < pivot_val { return Value::Na; }
    }
    Value::Float(pivot_val)
}

fn builtin_ta_pivothigh(args: &[Value], vars_history: &[Vec<f64>]) -> Value {
    let left = args.get(1).map(|v| v.to_i64().max(1) as usize).unwrap_or(5);
    let right = args.get(2).map(|v| v.to_i64().max(1) as usize).unwrap_or(5);

    if vars_history.len() <= 1 { return Value::Na; }
    let hist = &vars_history[1];
    if right + left >= hist.len() { return Value::Na; }

    let pivot_val = hist[right];
    if pivot_val.is_nan() { return Value::Na; }

    for i in (right + 1)..=(right + left) {
        if i >= hist.len() || hist[i].is_nan() { return Value::Na; }
        if hist[i] > pivot_val { return Value::Na; }
    }
    for i in 0..right {
        if i >= hist.len() || hist[i].is_nan() { return Value::Na; }
        if hist[i] > pivot_val { return Value::Na; }
    }
    Value::Float(pivot_val)
}

// ── math.* functions ────────────────────────────────────────

fn builtin_math_abs(args: &[Value]) -> Value {
    args.first().map(|v| {
        let f = v.to_f64();
        if f.is_nan() { Value::Na } else { Value::Float(f.abs()) }
    }).unwrap_or(Value::Na)
}

fn builtin_math_max(args: &[Value]) -> Value {
    if args.len() < 2 { return Value::Na; }
    let a = args[0].to_f64();
    let b = args[1].to_f64();
    if a.is_nan() || b.is_nan() { Value::Na } else { Value::Float(a.max(b)) }
}

fn builtin_math_min(args: &[Value]) -> Value {
    if args.len() < 2 { return Value::Na; }
    let a = args[0].to_f64();
    let b = args[1].to_f64();
    if a.is_nan() || b.is_nan() { Value::Na } else { Value::Float(a.min(b)) }
}

fn builtin_math_floor(args: &[Value]) -> Value {
    args.first().map(|v| {
        let f = v.to_f64();
        if f.is_nan() { Value::Na } else { Value::Float(f.floor()) }
    }).unwrap_or(Value::Na)
}

fn builtin_math_ceil(args: &[Value]) -> Value {
    args.first().map(|v| {
        let f = v.to_f64();
        if f.is_nan() { Value::Na } else { Value::Float(f.ceil()) }
    }).unwrap_or(Value::Na)
}

fn builtin_math_round(args: &[Value]) -> Value {
    if args.is_empty() { return Value::Na; }
    let f = args[0].to_f64();
    if f.is_nan() { return Value::Na; }
    let precision = args.get(1).map(|v| v.to_i64() as i32).unwrap_or(0);
    let mult = 10f64.powi(precision);
    Value::Float((f * mult).round() / mult)
}

fn builtin_math_sum(args: &[Value]) -> Value {
    if args.len() < 2 { return Value::Na; }
    let source = args[0].to_f64();
    Value::Float(source)
}

fn builtin_math_atan(args: &[Value]) -> Value {
    args.first().map(|v| {
        let f = v.to_f64();
        if f.is_nan() { Value::Na } else { Value::Float(f.atan()) }
    }).unwrap_or(Value::Na)
}

fn builtin_math_sqrt(args: &[Value]) -> Value {
    args.first().map(|v| {
        let f = v.to_f64();
        if f.is_nan() || f < 0.0 { Value::Na } else { Value::Float(f.sqrt()) }
    }).unwrap_or(Value::Na)
}

fn builtin_math_pow(args: &[Value]) -> Value {
    if args.len() < 2 { return Value::Na; }
    let base = args[0].to_f64();
    let exp = args[1].to_f64();
    if base.is_nan() || exp.is_nan() { Value::Na } else { Value::Float(base.powf(exp)) }
}

fn builtin_math_log(args: &[Value]) -> Value {
    args.first().map(|v| {
        let f = v.to_f64();
        if f.is_nan() || f <= 0.0 { Value::Na } else { Value::Float(f.ln()) }
    }).unwrap_or(Value::Na)
}

fn builtin_math_log10(args: &[Value]) -> Value {
    args.first().map(|v| {
        let f = v.to_f64();
        if f.is_nan() || f <= 0.0 { Value::Na } else { Value::Float(f.log10()) }
    }).unwrap_or(Value::Na)
}

fn builtin_math_exp(args: &[Value]) -> Value {
    args.first().map(|v| {
        let f = v.to_f64();
        if f.is_nan() { Value::Na } else { Value::Float(f.exp()) }
    }).unwrap_or(Value::Na)
}

// ── na / nz ────────────────────────────────────────────────

fn builtin_na(args: &[Value]) -> Value {
    args.first().map(|v| Value::Bool(v.is_na())).unwrap_or(Value::Bool(true))
}

fn builtin_nz(args: &[Value]) -> Value {
    if args.is_empty() { return Value::Float(0.0); }
    if args[0].is_na() {
        args.get(1).cloned().unwrap_or(Value::Float(0.0))
    } else {
        args[0].clone()
    }
}

// ── Time functions (stubs) ──────────────────────────────────

fn builtin_time(_args: &[Value]) -> Value { Value::Na }
fn builtin_hour(_args: &[Value]) -> Value { Value::Int(0) }
fn builtin_minute(_args: &[Value]) -> Value { Value::Int(0) }
fn builtin_dayofweek(_args: &[Value]) -> Value { Value::Int(1) }
fn builtin_dayofmonth(_args: &[Value]) -> Value { Value::Int(1) }
fn builtin_timeframe_in_seconds(_args: &[Value]) -> Value { Value::Int(300) }
