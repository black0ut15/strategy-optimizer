/// Pine Script Virtual Machine
/// Executes compiled bytecode bar-by-bar, maintaining variable history
/// for historical references (close[1], etc.) and na propagation.

use super::compiler::{CompiledProgram, Op, BuiltinRegistry};
use super::builtins;
use super::strategy_engine::StrategyEngine;
use std::collections::HashMap;

/// Value type used on the VM stack and in variables
#[derive(Debug, Clone)]
pub enum Value {
    Float(f64),
    Int(i64),
    Bool(bool),
    Str(String),
    Na,
}

impl Value {
    pub fn to_f64(&self) -> f64 {
        match self {
            Value::Float(f) => *f,
            Value::Int(i) => *i as f64,
            Value::Bool(b) => if *b { 1.0 } else { 0.0 },
            Value::Na => f64::NAN,
            Value::Str(_) => f64::NAN,
        }
    }

    pub fn to_bool(&self) -> bool {
        match self {
            Value::Bool(b) => *b,
            Value::Float(f) => !f.is_nan() && *f != 0.0,
            Value::Int(i) => *i != 0,
            Value::Na => false,
            Value::Str(s) => !s.is_empty(),
        }
    }

    pub fn to_i64(&self) -> i64 {
        match self {
            Value::Int(i) => *i,
            Value::Float(f) => *f as i64,
            Value::Bool(b) => if *b { 1 } else { 0 },
            _ => 0,
        }
    }

    pub fn to_string_val(&self) -> String {
        match self {
            Value::Str(s) => s.clone(),
            Value::Float(f) => format!("{}", f),
            Value::Int(i) => format!("{}", i),
            Value::Bool(b) => format!("{}", b),
            Value::Na => "NaN".to_string(),
        }
    }

    pub fn is_na(&self) -> bool {
        match self {
            Value::Na => true,
            Value::Float(f) => f.is_nan(),
            _ => false,
        }
    }
}

/// Bar data passed to the VM each bar
#[derive(Debug, Clone)]
pub struct BarData {
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub timestamp: i64, // unix ms
}

/// Backtest result returned by the VM
#[derive(Debug, Clone, serde::Serialize)]
pub struct BacktestResult {
    pub profit_factor: f64,
    pub net_profit: f64,
    pub max_drawdown: f64,
    pub win_rate: f64,
    pub total_trades: u32,
    pub gross_profit: f64,
    pub gross_loss: f64,
}

impl BacktestResult {
    pub fn empty() -> Self {
        Self {
            profit_factor: 0.0,
            net_profit: 0.0,
            max_drawdown: 0.0,
            win_rate: 0.0,
            total_trades: 0,
            gross_profit: 0.0,
            gross_loss: 0.0,
        }
    }
}

/// The VM itself
pub struct Vm {
    program: CompiledProgram,
    builtins: BuiltinRegistry,

    // Variable storage: current values
    vars: Vec<Value>,

    // History ring buffer per variable: vars_history[slot][age] where age=0 is current
    vars_history: Vec<Vec<f64>>,
    history_depth: usize, // how many bars of history to keep

    // Stack
    stack: Vec<Value>,

    // Execution state
    pc: usize, // program counter
    bar_index: usize,

    // Strategy engine
    pub strategy: StrategyEngine,

    // Builtin function state (for ta.* functions that need history)
    builtin_state: builtins::BuiltinState,
}

impl Vm {
    pub fn new(program: CompiledProgram, initial_capital: f64, fee_pct: f64) -> Self {
        let var_count = program.var_count;
        let history_depth = 512; // max lookback

        Self {
            program,
            builtins: BuiltinRegistry::new(),
            vars: vec![Value::Na; var_count],
            vars_history: vec![vec![f64::NAN; history_depth]; var_count],
            history_depth,
            stack: Vec::with_capacity(64),
            pc: 0,
            bar_index: 0,
            strategy: StrategyEngine::new(initial_capital, fee_pct),
            builtin_state: builtins::BuiltinState::new(var_count),
        }
    }

    /// Set an input parameter value before running
    pub fn set_input(&mut self, name: &str, value: Value) {
        if let Some(&slot) = self.program.input_slots.get(name) {
            self.vars[slot as usize] = value;
        }
    }

    /// Set multiple input parameters from a map
    pub fn set_inputs(&mut self, inputs: &HashMap<String, Value>) {
        for (name, value) in inputs {
            self.set_input(name, value.clone());
        }
    }

    /// Run the entire backtest on a slice of bar data
    pub fn run(&mut self, bars: &[BarData]) -> BacktestResult {
        if bars.is_empty() {
            return BacktestResult::empty();
        }

        // Initialize persistent variables (run var init code on first bar)
        self.run_var_inits();

        for (i, bar) in bars.iter().enumerate() {
            self.bar_index = i;
            self.load_bar_data(bar, i);
            self.execute_bar();

            // Process strategy orders after script execution
            self.strategy.process_bar(bar, i);
        }

        self.strategy.result()
    }

    fn run_var_inits(&mut self) {
        let inits = self.program.var_init_ops.clone();
        for (_slot, ops) in &inits {
            self.stack.clear();
            for op in ops {
                self.exec_op(op.clone());
            }
            // The init code should leave a value on the stack and store it
        }
    }

    fn load_bar_data(&mut self, bar: &BarData, idx: usize) {
        // Copy slot indices out of program to avoid borrow conflict with set_var
        let s_open = self.program.var_slots.get("open").copied();
        let s_high = self.program.var_slots.get("high").copied();
        let s_low = self.program.var_slots.get("low").copied();
        let s_close = self.program.var_slots.get("close").copied();
        let s_volume = self.program.var_slots.get("volume").copied();
        let s_hl2 = self.program.var_slots.get("hl2").copied();
        let s_hlc3 = self.program.var_slots.get("hlc3").copied();
        let s_ohlc4 = self.program.var_slots.get("ohlc4").copied();
        let s_bar_index = self.program.var_slots.get("bar_index").copied();
        let s_pos_size = self.program.var_slots.get("strategy.position_size").copied();
        let s_avg_price = self.program.var_slots.get("strategy.position_avg_price").copied();
        let s_equity = self.program.var_slots.get("strategy.equity").copied();
        let s_long = self.program.var_slots.get("strategy.long").copied();
        let s_short = self.program.var_slots.get("strategy.short").copied();
        let s_pi = self.program.var_slots.get("math.pi").copied();

        // Read strategy state before mutating vars
        let pos_size = self.strategy.position_size();
        let avg_price = self.strategy.avg_entry_price();
        let equity = self.strategy.equity();

        // Bar data
        if let Some(s) = s_open { self.set_var(s, Value::Float(bar.open)); }
        if let Some(s) = s_high { self.set_var(s, Value::Float(bar.high)); }
        if let Some(s) = s_low { self.set_var(s, Value::Float(bar.low)); }
        if let Some(s) = s_close { self.set_var(s, Value::Float(bar.close)); }
        if let Some(s) = s_volume { self.set_var(s, Value::Float(bar.volume)); }
        if let Some(s) = s_hl2 {
            self.set_var(s, Value::Float((bar.high + bar.low) / 2.0));
        }
        if let Some(s) = s_hlc3 {
            self.set_var(s, Value::Float((bar.high + bar.low + bar.close) / 3.0));
        }
        if let Some(s) = s_ohlc4 {
            self.set_var(s, Value::Float((bar.open + bar.high + bar.low + bar.close) / 4.0));
        }
        if let Some(s) = s_bar_index {
            self.set_var(s, Value::Int(idx as i64));
        }

        // Strategy properties
        if let Some(s) = s_pos_size { self.set_var(s, Value::Float(pos_size)); }
        if let Some(s) = s_avg_price { self.set_var(s, Value::Float(avg_price)); }
        if let Some(s) = s_equity { self.set_var(s, Value::Float(equity)); }

        // Constants
        if let Some(s) = s_long { self.set_var(s, Value::Int(1)); }
        if let Some(s) = s_short { self.set_var(s, Value::Int(-1)); }
        if let Some(s) = s_pi { self.set_var(s, Value::Float(std::f64::consts::PI)); }
    }

    fn set_var(&mut self, slot: u16, value: Value) {
        let s = slot as usize;
        if s < self.vars.len() {
            // Push current value into history before overwriting
            let hist = &mut self.vars_history[s];
            // Shift history right
            for j in (1..self.history_depth).rev() {
                hist[j] = hist[j - 1];
            }
            hist[0] = value.to_f64();
            self.vars[s] = value;
        }
    }

    fn execute_bar(&mut self) {
        self.pc = 0;
        self.stack.clear();
        let ops = self.program.ops.clone(); // TODO: avoid clone with lifetimes

        while self.pc < ops.len() {
            let op = ops[self.pc].clone();
            match op {
                Op::Halt => break,
                _ => self.exec_op(op),
            }
            self.pc += 1;
        }

        // Update history for all non-bar variables after execution
        for slot in 0..self.vars.len() {
            if !self.program.var_is_persistent.get(slot).copied().unwrap_or(false) {
                // Non-persistent vars: history was already updated in set_var
            }
            // For persistent vars, they keep their value — history is updated in set_var
        }
    }

    fn exec_op(&mut self, op: Op) {
        match op {
            Op::PushFloat(f) => self.stack.push(Value::Float(f)),
            Op::PushInt(i) => self.stack.push(Value::Int(i)),
            Op::PushBool(b) => self.stack.push(Value::Bool(b)),
            Op::PushStr(s) => self.stack.push(Value::Str(s)),
            Op::PushNa => self.stack.push(Value::Na),

            Op::LoadVar(slot) => {
                let val = self.vars.get(slot as usize).cloned().unwrap_or(Value::Na);
                self.stack.push(val);
            }

            Op::StoreVar(slot) => {
                let val = self.stack.pop().unwrap_or(Value::Na);
                self.set_var(slot, val);
            }

            Op::LoadHistVar(slot) => {
                let offset = self.stack.pop().unwrap_or(Value::Int(0));
                let n = offset.to_i64().max(0) as usize;
                let s = slot as usize;
                if s < self.vars_history.len() && n < self.history_depth {
                    let val = self.vars_history[s][n];
                    if val.is_nan() {
                        self.stack.push(Value::Na);
                    } else {
                        self.stack.push(Value::Float(val));
                    }
                } else {
                    self.stack.push(Value::Na);
                }
            }

            // Arithmetic
            Op::Add => {
                let b = self.stack.pop().unwrap_or(Value::Na);
                let a = self.stack.pop().unwrap_or(Value::Na);
                if a.is_na() || b.is_na() {
                    // String concatenation
                    if matches!(a, Value::Str(_)) || matches!(b, Value::Str(_)) {
                        self.stack.push(Value::Str(format!("{}{}", a.to_string_val(), b.to_string_val())));
                    } else {
                        self.stack.push(Value::Na);
                    }
                } else {
                    self.stack.push(Value::Float(a.to_f64() + b.to_f64()));
                }
            }
            Op::Sub => self.binary_float_op(|a, b| a - b),
            Op::Mul => self.binary_float_op(|a, b| a * b),
            Op::Div => self.binary_float_op(|a, b| if b == 0.0 { f64::NAN } else { a / b }),
            Op::Mod => self.binary_float_op(|a, b| if b == 0.0 { f64::NAN } else { a % b }),
            Op::Neg => {
                let a = self.stack.pop().unwrap_or(Value::Na);
                if a.is_na() {
                    self.stack.push(Value::Na);
                } else {
                    self.stack.push(Value::Float(-a.to_f64()));
                }
            }

            // Comparison
            Op::Eq => {
                let b = self.stack.pop().unwrap_or(Value::Na);
                let a = self.stack.pop().unwrap_or(Value::Na);
                // String comparison
                if matches!(&a, Value::Str(_)) || matches!(&b, Value::Str(_)) {
                    self.stack.push(Value::Bool(a.to_string_val() == b.to_string_val()));
                } else if a.is_na() && b.is_na() {
                    self.stack.push(Value::Bool(true));
                } else if a.is_na() || b.is_na() {
                    self.stack.push(Value::Bool(false));
                } else {
                    self.stack.push(Value::Bool(a.to_f64() == b.to_f64()));
                }
            }
            Op::Neq => {
                let b = self.stack.pop().unwrap_or(Value::Na);
                let a = self.stack.pop().unwrap_or(Value::Na);
                if matches!(&a, Value::Str(_)) || matches!(&b, Value::Str(_)) {
                    self.stack.push(Value::Bool(a.to_string_val() != b.to_string_val()));
                } else if a.is_na() && b.is_na() {
                    self.stack.push(Value::Bool(false));
                } else if a.is_na() || b.is_na() {
                    self.stack.push(Value::Bool(true));
                } else {
                    self.stack.push(Value::Bool(a.to_f64() != b.to_f64()));
                }
            }
            Op::Lt => self.binary_cmp_op(|a, b| a < b),
            Op::Gt => self.binary_cmp_op(|a, b| a > b),
            Op::Lte => self.binary_cmp_op(|a, b| a <= b),
            Op::Gte => self.binary_cmp_op(|a, b| a >= b),

            // Logical
            Op::And => {
                let b = self.stack.pop().unwrap_or(Value::Na);
                let a = self.stack.pop().unwrap_or(Value::Na);
                self.stack.push(Value::Bool(a.to_bool() && b.to_bool()));
            }
            Op::Or => {
                let b = self.stack.pop().unwrap_or(Value::Na);
                let a = self.stack.pop().unwrap_or(Value::Na);
                self.stack.push(Value::Bool(a.to_bool() || b.to_bool()));
            }
            Op::Not => {
                let a = self.stack.pop().unwrap_or(Value::Na);
                self.stack.push(Value::Bool(!a.to_bool()));
            }

            // Control flow
            Op::Jump(offset) => {
                self.pc = (self.pc as i32 + offset - 1) as usize; // -1 because pc++ after
            }
            Op::JumpIfFalse(offset) => {
                let val = self.stack.pop().unwrap_or(Value::Na);
                if !val.to_bool() {
                    self.pc = (self.pc as i32 + offset - 1) as usize;
                }
            }
            Op::JumpIfTrue(offset) => {
                let val = self.stack.pop().unwrap_or(Value::Na);
                if val.to_bool() {
                    self.pc = (self.pc as i32 + offset - 1) as usize;
                }
            }

            // Function calls
            Op::CallBuiltin(idx, argc) => {
                let mut args = Vec::with_capacity(argc as usize);
                for _ in 0..argc {
                    args.push(self.stack.pop().unwrap_or(Value::Na));
                }
                args.reverse();
                let result = builtins::call_builtin(
                    idx,
                    &args,
                    &mut self.builtin_state,
                    self.bar_index,
                    &self.vars,
                    &self.vars_history,
                );
                self.stack.push(result);
            }

            Op::CallUser(_idx, _argc) => {
                // For now, inline execution (no separate call stack)
                // User functions are compiled inline by the compiler
                self.stack.push(Value::Na); // placeholder
            }

            // Strategy calls
            Op::StrategyEntry(argc) => {
                let mut args = Vec::with_capacity(argc as usize);
                for _ in 0..argc {
                    args.push(self.stack.pop().unwrap_or(Value::Na));
                }
                args.reverse();
                self.strategy.handle_entry(&args, self.bar_index);
            }

            Op::StrategyExit(argc) => {
                let mut args = Vec::with_capacity(argc as usize);
                for _ in 0..argc {
                    args.push(self.stack.pop().unwrap_or(Value::Na));
                }
                args.reverse();
                self.strategy.handle_exit(&args, self.bar_index);
            }

            Op::StrategyClose(argc) => {
                let mut args = Vec::with_capacity(argc as usize);
                for _ in 0..argc {
                    args.push(self.stack.pop().unwrap_or(Value::Na));
                }
                args.reverse();
                self.strategy.handle_close(&args, self.bar_index);
            }

            Op::StrategyCloseAll => {
                self.strategy.close_all(self.bar_index);
            }

            Op::Pop => { self.stack.pop(); }
            Op::Dup => {
                if let Some(val) = self.stack.last().cloned() {
                    self.stack.push(val);
                }
            }
            Op::Nop => {}
            Op::Halt => {}
        }
    }

    fn binary_float_op<F: Fn(f64, f64) -> f64>(&mut self, op: F) {
        let b = self.stack.pop().unwrap_or(Value::Na);
        let a = self.stack.pop().unwrap_or(Value::Na);
        if a.is_na() || b.is_na() {
            self.stack.push(Value::Na);
        } else {
            self.stack.push(Value::Float(op(a.to_f64(), b.to_f64())));
        }
    }

    fn binary_cmp_op<F: Fn(f64, f64) -> bool>(&mut self, op: F) {
        let b = self.stack.pop().unwrap_or(Value::Na);
        let a = self.stack.pop().unwrap_or(Value::Na);
        if a.is_na() || b.is_na() {
            self.stack.push(Value::Bool(false));
        } else {
            self.stack.push(Value::Bool(op(a.to_f64(), b.to_f64())));
        }
    }
}
