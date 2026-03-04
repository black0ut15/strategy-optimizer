/// Pine Script Input Extractor
/// Walks the AST to find all `input.*` calls and extracts their
/// parameter names, types, defaults, ranges, and groups for the optimizer UI.

use super::ast::*;
use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PineInput {
    pub name: String,           // variable name in the script
    pub input_type: String,     // "bool", "int", "float", "string", "source", "session", "color"
    pub default: InputValue,    // default value
    pub title: Option<String>,  // display title
    pub group: Option<String>,  // group name
    pub options: Option<Vec<String>>,  // for string inputs with options list
    pub min_val: Option<f64>,
    pub max_val: Option<f64>,
    pub step: Option<f64>,
    pub tooltip: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum InputValue {
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
}

/// Extract all input definitions from a parsed Pine program.
pub fn extract_inputs(program: &Program) -> Vec<PineInput> {
    let mut inputs = Vec::new();
    for stmt in &program.statements {
        if let Some(input) = try_extract_input(stmt) {
            inputs.push(input);
        }
    }
    inputs
}

fn try_extract_input(stmt: &Stmt) -> Option<PineInput> {
    match stmt {
        Stmt::VarDecl { name, value, .. } => {
            try_extract_from_expr(name, value)
        }
        _ => None,
    }
}

fn try_extract_from_expr(var_name: &str, expr: &Expr) -> Option<PineInput> {
    // Look for `input.bool(...)`, `input.int(...)`, etc.
    if let Expr::Call { func, args } = expr {
        if let Expr::Member { object, field } = func.as_ref() {
            if let Expr::Var(obj_name) = object.as_ref() {
                if obj_name == "input" {
                    return parse_input_call(var_name, field, args);
                }
            }
        }
    }

    // Handle `input.float(0.10, ...) / 100` — division wrapping input call
    if let Expr::BinOp { left, op: BinOpKind::Div, right } = expr {
        if let Some(mut input) = try_extract_from_expr(var_name, left) {
            // Adjust default by dividing
            if let (InputValue::Float(v), Some(divisor)) = (&input.default, expr_to_f64(right)) {
                input.default = InputValue::Float(v / divisor);
            }
            return Some(input);
        }
    }

    None
}

fn parse_input_call(var_name: &str, input_type: &str, args: &[CallArg]) -> Option<PineInput> {
    let mut input = PineInput {
        name: var_name.to_string(),
        input_type: input_type.to_string(),
        default: InputValue::Bool(false),
        title: None,
        group: None,
        options: None,
        min_val: None,
        max_val: None,
        step: None,
        tooltip: None,
    };

    // Parse positional and named arguments
    let mut positional_idx = 0;

    for arg in args {
        if let Some(name) = &arg.name {
            match name.as_str() {
                "title" => input.title = expr_to_string(&arg.value),
                "group" => input.group = expr_to_string(&arg.value),
                "tooltip" => input.tooltip = expr_to_string(&arg.value),
                "defval" => set_default(&mut input, &arg.value, input_type),
                "minval" => input.min_val = expr_to_f64(&arg.value),
                "maxval" => input.max_val = expr_to_f64(&arg.value),
                "step" => input.step = expr_to_f64(&arg.value),
                "options" => {
                    if let Expr::Array(elems) = &arg.value {
                        input.options = Some(
                            elems.iter().filter_map(|e| expr_to_string(e)).collect()
                        );
                    }
                }
                _ => {} // ignore unknown named args (inline, confirm, display, etc.)
            }
        } else {
            // Positional arguments
            match positional_idx {
                0 => set_default(&mut input, &arg.value, input_type),
                1 => {
                    // Second positional is title for most inputs, options for string
                    if input_type == "string" {
                        if let Expr::Array(elems) = &arg.value {
                            input.options = Some(
                                elems.iter().filter_map(|e| expr_to_string(e)).collect()
                            );
                        } else {
                            input.title = expr_to_string(&arg.value);
                        }
                    } else {
                        input.title = expr_to_string(&arg.value);
                    }
                }
                2 => {
                    // Third positional for string is options
                    if input_type == "string" {
                        if let Expr::Array(elems) = &arg.value {
                            input.options = Some(
                                elems.iter().filter_map(|e| expr_to_string(e)).collect()
                            );
                        }
                    }
                }
                _ => {}
            }
            positional_idx += 1;
        }
    }

    // Set title from variable name if not specified
    if input.title.is_none() {
        input.title = Some(var_name.to_string());
    }

    Some(input)
}

fn set_default(input: &mut PineInput, expr: &Expr, input_type: &str) {
    match input_type {
        "bool" => {
            if let Some(b) = expr_to_bool(expr) {
                input.default = InputValue::Bool(b);
            }
        }
        "int" => {
            if let Some(n) = expr_to_i64(expr) {
                input.default = InputValue::Int(n);
            }
        }
        "float" => {
            if let Some(n) = expr_to_f64(expr) {
                input.default = InputValue::Float(n);
            }
        }
        "string" | "session" => {
            if let Some(s) = expr_to_string(expr) {
                input.default = InputValue::Str(s);
            }
        }
        "source" => {
            if let Some(s) = expr_to_string(expr) {
                input.default = InputValue::Str(s);
            } else if let Expr::Var(name) = expr {
                input.default = InputValue::Str(name.clone());
            }
        }
        "color" => {
            // Colors aren't meaningful for backtesting, store as string
            input.default = InputValue::Str("color".to_string());
        }
        _ => {}
    }
}

fn expr_to_string(expr: &Expr) -> Option<String> {
    match expr {
        Expr::StrLit(s) => Some(s.clone()),
        Expr::Var(s) => Some(s.clone()),
        _ => None,
    }
}

fn expr_to_f64(expr: &Expr) -> Option<f64> {
    match expr {
        Expr::FloatLit(n) => Some(*n),
        Expr::IntLit(n) => Some(*n as f64),
        Expr::UnaryOp { op: UnaryOpKind::Neg, expr } => {
            expr_to_f64(expr).map(|n| -n)
        }
        _ => None,
    }
}

fn expr_to_i64(expr: &Expr) -> Option<i64> {
    match expr {
        Expr::IntLit(n) => Some(*n),
        Expr::FloatLit(n) => Some(*n as i64),
        Expr::UnaryOp { op: UnaryOpKind::Neg, expr } => {
            expr_to_i64(expr).map(|n| -n)
        }
        _ => None,
    }
}

fn expr_to_bool(expr: &Expr) -> Option<bool> {
    match expr {
        Expr::BoolLit(b) => Some(*b),
        _ => None,
    }
}
