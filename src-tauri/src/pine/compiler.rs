/// Pine Script Compiler
/// Converts the AST into a flat list of bytecode instructions
/// for efficient execution by the VM.

use super::ast::*;
use serde::{Serialize, Deserialize};
use std::collections::HashMap;

/// Bytecode instruction set
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum Op {
    // Stack operations
    PushFloat(f64),
    PushInt(i64),
    PushBool(bool),
    PushStr(String),
    PushNa,

    // Variable operations
    LoadVar(u16),          // push variable by slot index
    StoreVar(u16),         // pop and store to variable slot
    LoadHistVar(u16),      // push var[offset] — offset is on stack

    // Arithmetic
    Add,
    Sub,
    Mul,
    Div,
    Mod,
    Neg,

    // Comparison
    Eq,
    Neq,
    Lt,
    Gt,
    Lte,
    Gte,

    // Logical
    And,
    Or,
    Not,

    // Control flow
    Jump(i32),             // unconditional jump (relative offset)
    JumpIfFalse(i32),      // conditional jump
    JumpIfTrue(i32),       // short-circuit jump

    // Function calls
    CallBuiltin(u16, u8),  // builtin function index, arg count
    CallUser(u16, u8),     // user function index, arg count

    // Strategy calls
    StrategyEntry(u8),     // arg count (id, direction, qty on stack)
    StrategyExit(u8),      // arg count
    StrategyClose(u8),     // arg count
    StrategyCloseAll,

    // Special
    Pop,                   // discard top of stack
    Dup,                   // duplicate top of stack
    Nop,
    Halt,
}

/// Compiled bytecode program
#[derive(Debug, Clone)]
pub struct CompiledProgram {
    pub ops: Vec<Op>,
    pub var_count: usize,
    pub var_names: Vec<String>,
    pub var_slots: HashMap<String, u16>,
    pub var_is_persistent: Vec<bool>,    // `var` keyword — persist across bars
    pub var_init_ops: Vec<(u16, Vec<Op>)>, // initialization code for `var` variables
    pub user_funcs: Vec<UserFunc>,
    pub input_slots: HashMap<String, u16>, // input variable name → slot
    pub visual_only_vars: Vec<u16>,        // slots for visual-only variables (skip)
}

#[derive(Debug, Clone)]
pub struct UserFunc {
    pub name: String,
    pub params: Vec<String>,
    pub body_start: usize,  // index into ops
    pub body_end: usize,
}

/// Builtin function registry
#[derive(Debug, Clone)]
pub struct BuiltinRegistry {
    pub names: Vec<String>,
    pub map: HashMap<String, u16>,
}

impl BuiltinRegistry {
    pub fn new() -> Self {
        let builtins = vec![
            // ta.* functions
            "ta.atr", "ta.ema", "ta.sma", "ta.crossover", "ta.crossunder",
            "ta.highest", "ta.lowest", "ta.vwap", "ta.pivotlow", "ta.pivothigh",
            // math.* functions
            "math.abs", "math.max", "math.min", "math.floor", "math.ceil",
            "math.round", "math.sum", "math.atan", "math.sqrt", "math.pow",
            "math.log", "math.log10", "math.exp",
            // na/nz
            "na", "nz",
            // time functions
            "time", "hour", "minute", "dayofweek", "dayofmonth",
            "timeframe.in_seconds",
            // str functions (mostly skip for backtesting)
            "str.tostring", "str.replace_all",
        ];

        let mut map = HashMap::new();
        let names: Vec<String> = builtins.iter().map(|s| s.to_string()).collect();
        for (i, name) in names.iter().enumerate() {
            map.insert(name.clone(), i as u16);
        }

        Self { names, map }
    }

    pub fn lookup(&self, name: &str) -> Option<u16> {
        self.map.get(name).copied()
    }
}

/// Visual-only identifiers that can be completely skipped
const VISUAL_PREFIXES: &[&str] = &[
    "plot", "plotshape", "plotchar", "bgcolor", "fill",
    "label.", "line.", "box.", "table.", "hline",
    "alert", "alertcondition",
];

const VISUAL_FUNCTIONS: &[&str] = &[
    "plot", "plotshape", "plotchar", "bgcolor", "fill", "hline",
    "alertcondition", "alert",
    "label.new", "label.set_text", "label.delete",
    "line.new", "line.delete", "line.set_xy1", "line.set_xy2",
    "box.new", "box.delete", "box.set_top", "box.set_bottom", "box.set_right",
    "table.new", "table.cell",
];

pub struct Compiler {
    ops: Vec<Op>,
    var_slots: HashMap<String, u16>,
    var_names: Vec<String>,
    var_is_persistent: Vec<bool>,
    var_init_ops: Vec<(u16, Vec<Op>)>,
    next_var: u16,
    builtins: BuiltinRegistry,
    user_funcs: Vec<UserFunc>,
    user_func_map: HashMap<String, u16>,
    input_slots: HashMap<String, u16>,
    visual_only_vars: Vec<u16>,
}

impl Compiler {
    pub fn new() -> Self {
        let mut c = Self {
            ops: Vec::new(),
            var_slots: HashMap::new(),
            var_names: Vec::new(),
            var_is_persistent: Vec::new(),
            var_init_ops: Vec::new(),
            next_var: 0,
            builtins: BuiltinRegistry::new(),
            user_funcs: Vec::new(),
            user_func_map: HashMap::new(),
            input_slots: HashMap::new(),
            visual_only_vars: Vec::new(),
        };

        // Pre-allocate slots for built-in bar data
        let bar_vars = &[
            "open", "high", "low", "close", "volume",
            "hl2", "hlc3", "ohlc4",
            "bar_index",
        ];
        for name in bar_vars {
            c.alloc_var(name, false);
        }

        // Strategy built-in properties
        let strategy_vars = &[
            "strategy.position_size",
            "strategy.position_avg_price",
            "strategy.equity",
        ];
        for name in strategy_vars {
            c.alloc_var(name, false);
        }

        // Constants
        let constants = &[
            "strategy.long", "strategy.short",
            "math.pi",
        ];
        for name in constants {
            c.alloc_var(name, false);
        }

        c
    }

    pub fn compile(&mut self, program: &Program) -> Result<CompiledProgram, String> {
        // First pass: collect function definitions and input variables
        for stmt in &program.statements {
            self.pre_scan_stmt(stmt);
        }

        // Second pass: compile statements
        for stmt in &program.statements {
            self.compile_stmt(stmt)?;
        }

        self.ops.push(Op::Halt);

        Ok(CompiledProgram {
            ops: self.ops.clone(),
            var_count: self.next_var as usize,
            var_names: self.var_names.clone(),
            var_slots: self.var_slots.clone(),
            var_is_persistent: self.var_is_persistent.clone(),
            var_init_ops: self.var_init_ops.clone(),
            user_funcs: self.user_funcs.clone(),
            input_slots: self.input_slots.clone(),
            visual_only_vars: self.visual_only_vars.clone(),
        })
    }

    fn alloc_var(&mut self, name: &str, is_persistent: bool) -> u16 {
        if let Some(&slot) = self.var_slots.get(name) {
            return slot;
        }
        let slot = self.next_var;
        self.next_var += 1;
        self.var_slots.insert(name.to_string(), slot);
        self.var_names.push(name.to_string());
        self.var_is_persistent.push(is_persistent);
        slot
    }

    fn pre_scan_stmt(&mut self, stmt: &Stmt) {
        match stmt {
            Stmt::FuncDef { name, params, .. } => {
                let idx = self.user_funcs.len() as u16;
                self.user_func_map.insert(name.clone(), idx);
                self.user_funcs.push(UserFunc {
                    name: name.clone(),
                    params: params.iter().map(|p| p.name.clone()).collect(),
                    body_start: 0,
                    body_end: 0,
                });
            }
            Stmt::VarDecl { name, value, .. } => {
                // Check if this is an input variable
                if is_input_call(value) {
                    let slot = self.alloc_var(name, false);
                    self.input_slots.insert(name.clone(), slot);
                }
                // Check if visual-only
                if is_visual_call(value) {
                    let slot = self.alloc_var(name, false);
                    self.visual_only_vars.push(slot);
                }
            }
            _ => {}
        }
    }

    fn compile_stmt(&mut self, stmt: &Stmt) -> Result<(), String> {
        match stmt {
            Stmt::VarDecl { name, is_var, is_varip, value, .. } => {
                // Skip visual-only declarations
                if is_visual_call(value) {
                    return Ok(());
                }

                let slot = self.alloc_var(name, *is_var || *is_varip);

                if *is_var || *is_varip {
                    // `var` variables: compile init code separately (run once on bar 0)
                    let mut init_ops = Vec::new();
                    std::mem::swap(&mut self.ops, &mut init_ops);
                    self.compile_expr(value)?;
                    self.ops.push(Op::StoreVar(slot));
                    std::mem::swap(&mut self.ops, &mut init_ops);
                    self.var_init_ops.push((slot, init_ops));
                } else {
                    self.compile_expr(value)?;
                    self.ops.push(Op::StoreVar(slot));
                }
            }

            Stmt::Reassign { name, op, value } => {
                let slot = self.alloc_var(name, false);
                match op {
                    ReassignOp::Set => {
                        self.compile_expr(value)?;
                        self.ops.push(Op::StoreVar(slot));
                    }
                    ReassignOp::Add => {
                        self.ops.push(Op::LoadVar(slot));
                        self.compile_expr(value)?;
                        self.ops.push(Op::Add);
                        self.ops.push(Op::StoreVar(slot));
                    }
                    ReassignOp::Sub => {
                        self.ops.push(Op::LoadVar(slot));
                        self.compile_expr(value)?;
                        self.ops.push(Op::Sub);
                        self.ops.push(Op::StoreVar(slot));
                    }
                    ReassignOp::Mul => {
                        self.ops.push(Op::LoadVar(slot));
                        self.compile_expr(value)?;
                        self.ops.push(Op::Mul);
                        self.ops.push(Op::StoreVar(slot));
                    }
                    ReassignOp::Div => {
                        self.ops.push(Op::LoadVar(slot));
                        self.compile_expr(value)?;
                        self.ops.push(Op::Div);
                        self.ops.push(Op::StoreVar(slot));
                    }
                }
            }

            Stmt::If { condition, body, else_ifs, else_body } => {
                self.compile_expr(condition)?;
                let jump_false = self.ops.len();
                self.ops.push(Op::JumpIfFalse(0)); // placeholder

                for s in body {
                    self.compile_stmt(s)?;
                }

                if else_ifs.is_empty() && else_body.is_none() {
                    // Patch jump
                    let target = self.ops.len() as i32 - jump_false as i32;
                    self.ops[jump_false] = Op::JumpIfFalse(target);
                } else {
                    let mut end_jumps = Vec::new();

                    // Jump over else body
                    end_jumps.push(self.ops.len());
                    self.ops.push(Op::Jump(0)); // placeholder

                    // Patch the if-false jump to here
                    let target = self.ops.len() as i32 - jump_false as i32;
                    self.ops[jump_false] = Op::JumpIfFalse(target);

                    // Else-if branches
                    for (cond, ei_body) in else_ifs {
                        self.compile_expr(cond)?;
                        let ei_jump = self.ops.len();
                        self.ops.push(Op::JumpIfFalse(0));

                        for s in ei_body {
                            self.compile_stmt(s)?;
                        }

                        end_jumps.push(self.ops.len());
                        self.ops.push(Op::Jump(0));

                        let ei_target = self.ops.len() as i32 - ei_jump as i32;
                        self.ops[ei_jump] = Op::JumpIfFalse(ei_target);
                    }

                    // Else branch
                    if let Some(else_stmts) = else_body {
                        for s in else_stmts {
                            self.compile_stmt(s)?;
                        }
                    }

                    // Patch all end jumps
                    let end_pos = self.ops.len();
                    for j in end_jumps {
                        let target = end_pos as i32 - j as i32;
                        self.ops[j] = Op::Jump(target);
                    }
                }
            }

            Stmt::For { var_name, start, end, step, body } => {
                let slot = self.alloc_var(var_name, false);

                // Initialize loop var
                self.compile_expr(start)?;
                self.ops.push(Op::StoreVar(slot));

                // Loop condition check
                let loop_start = self.ops.len();
                self.ops.push(Op::LoadVar(slot));
                self.compile_expr(end)?;
                self.ops.push(Op::Lte);
                let exit_jump = self.ops.len();
                self.ops.push(Op::JumpIfFalse(0));

                // Loop body
                for s in body {
                    self.compile_stmt(s)?;
                }

                // Increment
                self.ops.push(Op::LoadVar(slot));
                if let Some(step_expr) = step {
                    self.compile_expr(step_expr)?;
                } else {
                    self.ops.push(Op::PushInt(1));
                }
                self.ops.push(Op::Add);
                self.ops.push(Op::StoreVar(slot));

                // Jump back to start
                let back_jump = loop_start as i32 - self.ops.len() as i32;
                self.ops.push(Op::Jump(back_jump));

                // Patch exit jump
                let exit_target = self.ops.len() as i32 - exit_jump as i32;
                self.ops[exit_jump] = Op::JumpIfFalse(exit_target);
            }

            Stmt::While { condition, body } => {
                let loop_start = self.ops.len();
                self.compile_expr(condition)?;
                let exit_jump = self.ops.len();
                self.ops.push(Op::JumpIfFalse(0));

                for s in body {
                    self.compile_stmt(s)?;
                }

                let back_jump = loop_start as i32 - self.ops.len() as i32;
                self.ops.push(Op::Jump(back_jump));

                let exit_target = self.ops.len() as i32 - exit_jump as i32;
                self.ops[exit_jump] = Op::JumpIfFalse(exit_target);
            }

            Stmt::Switch { value, cases, default } => {
                // Compile switch value
                if let Some(val) = value {
                    self.compile_expr(val)?;
                }

                let mut end_jumps = Vec::new();

                for (case_expr, case_body) in cases {
                    // Duplicate switch value for comparison
                    if value.is_some() {
                        self.ops.push(Op::Dup);
                    }
                    self.compile_expr(case_expr)?;
                    if value.is_some() {
                        self.ops.push(Op::Eq);
                    }
                    let skip_jump = self.ops.len();
                    self.ops.push(Op::JumpIfFalse(0));

                    // Pop the duplicated switch value
                    if value.is_some() {
                        self.ops.push(Op::Pop);
                    }

                    for s in case_body {
                        self.compile_stmt(s)?;
                    }

                    end_jumps.push(self.ops.len());
                    self.ops.push(Op::Jump(0));

                    let skip_target = self.ops.len() as i32 - skip_jump as i32;
                    self.ops[skip_jump] = Op::JumpIfFalse(skip_target);
                }

                // Default case
                if value.is_some() {
                    self.ops.push(Op::Pop); // pop switch value
                }
                if let Some(def_body) = default {
                    for s in def_body {
                        self.compile_stmt(s)?;
                    }
                }

                // Patch end jumps
                let end_pos = self.ops.len();
                for j in end_jumps {
                    let target = end_pos as i32 - j as i32;
                    self.ops[j] = Op::Jump(target);
                }
            }

            Stmt::FuncDef { name, params: _, body, single_expr } => {
                // Skip the function body in main execution
                let skip_jump = self.ops.len();
                self.ops.push(Op::Jump(0));

                // Record body start
                let body_start = self.ops.len();

                if let Some(expr) = single_expr {
                    self.compile_expr(expr)?;
                } else {
                    for s in body {
                        self.compile_stmt(s)?;
                    }
                }

                let body_end = self.ops.len();

                // Patch skip jump
                let skip_target = self.ops.len() as i32 - skip_jump as i32;
                self.ops[skip_jump] = Op::Jump(skip_target);

                // Update user func info
                if let Some(&idx) = self.user_func_map.get(name) {
                    if let Some(func) = self.user_funcs.get_mut(idx as usize) {
                        func.body_start = body_start;
                        func.body_end = body_end;
                    }
                }
            }

            Stmt::Expr(expr) => {
                // Skip visual-only calls
                if is_visual_expr(expr) {
                    return Ok(());
                }
                self.compile_expr(expr)?;
                self.ops.push(Op::Pop); // discard result
            }

            Stmt::Break => {
                // TODO: proper break handling with loop stack
                self.ops.push(Op::Nop);
            }

            Stmt::Continue => {
                // TODO: proper continue handling
                self.ops.push(Op::Nop);
            }
        }

        Ok(())
    }

    fn compile_expr(&mut self, expr: &Expr) -> Result<(), String> {
        match expr {
            Expr::FloatLit(n) => self.ops.push(Op::PushFloat(*n)),
            Expr::IntLit(n) => self.ops.push(Op::PushInt(*n)),
            Expr::StrLit(s) => self.ops.push(Op::PushStr(s.clone())),
            Expr::BoolLit(b) => self.ops.push(Op::PushBool(*b)),
            Expr::Na => self.ops.push(Op::PushNa),

            Expr::Var(name) => {
                let slot = self.alloc_var(name, false);
                self.ops.push(Op::LoadVar(slot));
            }

            Expr::HistRef { expr, offset } => {
                // Get variable slot
                if let Expr::Var(name) = expr.as_ref() {
                    let slot = self.alloc_var(name, false);
                    self.compile_expr(offset)?;
                    self.ops.push(Op::LoadHistVar(slot));
                } else {
                    // Complex expression with history — compute and store
                    self.compile_expr(expr)?;
                    // For complex hist refs, we'd need temp vars
                    // For now, just push the current value
                }
            }

            Expr::Member { object, field } => {
                // Resolve to a fully qualified name
                let full_name = self.resolve_member(object, field);
                let slot = self.alloc_var(&full_name, false);
                self.ops.push(Op::LoadVar(slot));
            }

            Expr::Call { func, args } => {
                let func_name = self.resolve_func_name(func);

                // Check for strategy calls
                if func_name.starts_with("strategy.") {
                    self.compile_strategy_call(&func_name, args)?;
                    return Ok(());
                }

                // Check for visual-only calls
                if VISUAL_FUNCTIONS.contains(&func_name.as_str()) {
                    self.ops.push(Op::PushNa);
                    return Ok(());
                }

                // Check for na/nz special calls
                if func_name == "na" && args.len() == 1 {
                    self.compile_expr(&args[0].value)?;
                    let idx = self.builtins.lookup("na").unwrap_or(0);
                    self.ops.push(Op::CallBuiltin(idx, 1));
                    return Ok(());
                }
                if func_name == "nz" {
                    for arg in args {
                        self.compile_expr(&arg.value)?;
                    }
                    let idx = self.builtins.lookup("nz").unwrap_or(0);
                    self.ops.push(Op::CallBuiltin(idx, args.len() as u8));
                    return Ok(());
                }

                // Check for builtin
                if let Some(idx) = self.builtins.lookup(&func_name) {
                    for arg in args {
                        self.compile_expr(&arg.value)?;
                    }
                    self.ops.push(Op::CallBuiltin(idx, args.len() as u8));
                    return Ok(());
                }

                // Check for user function
                if let Some(&idx) = self.user_func_map.get(&func_name) {
                    for arg in args {
                        self.compile_expr(&arg.value)?;
                    }
                    self.ops.push(Op::CallUser(idx, args.len() as u8));
                    return Ok(());
                }

                // Unknown function — treat as na
                self.ops.push(Op::PushNa);
            }

            Expr::BinOp { left, op, right } => {
                self.compile_expr(left)?;
                self.compile_expr(right)?;
                let inst = match op {
                    BinOpKind::Add => Op::Add,
                    BinOpKind::Sub => Op::Sub,
                    BinOpKind::Mul => Op::Mul,
                    BinOpKind::Div => Op::Div,
                    BinOpKind::Mod => Op::Mod,
                    BinOpKind::Eq => Op::Eq,
                    BinOpKind::Neq => Op::Neq,
                    BinOpKind::Lt => Op::Lt,
                    BinOpKind::Gt => Op::Gt,
                    BinOpKind::Lte => Op::Lte,
                    BinOpKind::Gte => Op::Gte,
                    BinOpKind::And => Op::And,
                    BinOpKind::Or => Op::Or,
                };
                self.ops.push(inst);
            }

            Expr::UnaryOp { op, expr } => {
                self.compile_expr(expr)?;
                match op {
                    UnaryOpKind::Neg => self.ops.push(Op::Neg),
                    UnaryOpKind::Not => self.ops.push(Op::Not),
                }
            }

            Expr::Ternary { condition, then_expr, else_expr } => {
                self.compile_expr(condition)?;
                let jump_false = self.ops.len();
                self.ops.push(Op::JumpIfFalse(0));

                self.compile_expr(then_expr)?;
                let jump_end = self.ops.len();
                self.ops.push(Op::Jump(0));

                let false_target = self.ops.len() as i32 - jump_false as i32;
                self.ops[jump_false] = Op::JumpIfFalse(false_target);

                self.compile_expr(else_expr)?;

                let end_target = self.ops.len() as i32 - jump_end as i32;
                self.ops[jump_end] = Op::Jump(end_target);
            }

            Expr::IfExpr { condition, then_expr, else_expr } => {
                self.compile_expr(condition)?;
                let jump_false = self.ops.len();
                self.ops.push(Op::JumpIfFalse(0));

                self.compile_expr(then_expr)?;
                let jump_end = self.ops.len();
                self.ops.push(Op::Jump(0));

                let false_target = self.ops.len() as i32 - jump_false as i32;
                self.ops[jump_false] = Op::JumpIfFalse(false_target);

                if let Some(else_e) = else_expr {
                    self.compile_expr(else_e)?;
                } else {
                    self.ops.push(Op::PushNa);
                }

                let end_target = self.ops.len() as i32 - jump_end as i32;
                self.ops[jump_end] = Op::Jump(end_target);
            }

            Expr::Array(elems) => {
                // For backtesting, arrays are usually just in input options
                // Push first element as the value
                if let Some(first) = elems.first() {
                    self.compile_expr(first)?;
                } else {
                    self.ops.push(Op::PushNa);
                }
            }
        }

        Ok(())
    }

    fn compile_strategy_call(&mut self, func_name: &str, args: &[CallArg]) -> Result<(), String> {
        match func_name {
            "strategy.entry" => {
                // Push args: id (string), direction, qty (optional)
                for arg in args {
                    self.compile_expr(&arg.value)?;
                }
                self.ops.push(Op::StrategyEntry(args.len() as u8));
            }
            "strategy.exit" => {
                for arg in args {
                    self.compile_expr(&arg.value)?;
                }
                self.ops.push(Op::StrategyExit(args.len() as u8));
            }
            "strategy.close" => {
                for arg in args {
                    self.compile_expr(&arg.value)?;
                }
                self.ops.push(Op::StrategyClose(args.len() as u8));
            }
            "strategy.close_all" => {
                self.ops.push(Op::StrategyCloseAll);
            }
            "strategy.order" => {
                for arg in args {
                    self.compile_expr(&arg.value)?;
                }
                self.ops.push(Op::StrategyEntry(args.len() as u8)); // treat like entry
            }
            _ => {
                // strategy.position_size etc. are variables, not calls
                self.ops.push(Op::PushNa);
            }
        }
        Ok(())
    }

    fn resolve_member(&self, object: &Expr, field: &str) -> String {
        match object {
            Expr::Var(name) => format!("{}.{}", name, field),
            Expr::Member { object: inner, field: inner_field } => {
                let base = self.resolve_member(inner, inner_field);
                format!("{}.{}", base, field)
            }
            _ => field.to_string(),
        }
    }

    fn resolve_func_name(&self, func: &Expr) -> String {
        match func {
            Expr::Var(name) => name.clone(),
            Expr::Member { object, field } => {
                let base = self.resolve_func_name(object);
                format!("{}.{}", base, field)
            }
            _ => "unknown".to_string(),
        }
    }
}

fn is_input_call(expr: &Expr) -> bool {
    if let Expr::Call { func, .. } = expr {
        if let Expr::Member { object, .. } = func.as_ref() {
            if let Expr::Var(name) = object.as_ref() {
                return name == "input";
            }
        }
    }
    // Handle `input.float(x) / 100` pattern
    if let Expr::BinOp { left, .. } = expr {
        return is_input_call(left);
    }
    false
}

fn is_visual_call(expr: &Expr) -> bool {
    is_visual_expr(expr)
}

fn is_visual_expr(expr: &Expr) -> bool {
    match expr {
        Expr::Call { func, .. } => {
            let name = match func.as_ref() {
                Expr::Var(n) => n.as_str(),
                Expr::Member { object, field } => {
                    if let Expr::Var(obj) = object.as_ref() {
                        // Check common visual prefixes
                        return VISUAL_PREFIXES.iter().any(|p| {
                            let full = format!("{}.{}", obj, field);
                            full.starts_with(p)
                        }) || VISUAL_FUNCTIONS.contains(&format!("{}.{}", obj, field).as_str());
                    }
                    return false;
                }
                _ => return false,
            };
            VISUAL_FUNCTIONS.contains(&name)
        }
        _ => false,
    }
}
