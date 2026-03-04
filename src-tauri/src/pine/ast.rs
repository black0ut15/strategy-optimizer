/// Pine Script AST (Abstract Syntax Tree)
/// Represents the parsed structure of a Pine Script program.

use std::fmt;

/// Top-level program node
#[derive(Debug, Clone)]
pub struct Program {
    pub statements: Vec<Stmt>,
}

/// Statement nodes
#[derive(Debug, Clone)]
pub enum Stmt {
    /// Variable declaration: `x = expr` or `var x = expr` or `float x = expr`
    VarDecl {
        name: String,
        type_hint: Option<String>,
        is_var: bool,       // `var` keyword — persist across bars
        is_varip: bool,     // `varip` keyword
        value: Expr,
    },

    /// Reassignment: `x := expr` or `x += expr`
    Reassign {
        name: String,
        op: ReassignOp,
        value: Expr,
    },

    /// If statement
    If {
        condition: Expr,
        body: Vec<Stmt>,
        else_ifs: Vec<(Expr, Vec<Stmt>)>,
        else_body: Option<Vec<Stmt>>,
    },

    /// For loop: `for i = start to end`
    For {
        var_name: String,
        start: Expr,
        end: Expr,
        step: Option<Expr>,
        body: Vec<Stmt>,
    },

    /// While loop
    While {
        condition: Expr,
        body: Vec<Stmt>,
    },

    /// Switch statement
    Switch {
        value: Option<Expr>,    // switch expr or switch (no expr, uses =>)
        cases: Vec<(Expr, Vec<Stmt>)>,
        default: Option<Vec<Stmt>>,
    },

    /// Function definition: `f_name(args) => body`
    FuncDef {
        name: String,
        params: Vec<FuncParam>,
        body: Vec<Stmt>,       // multi-line body
        single_expr: Option<Expr>, // single-line `=> expr`
    },

    /// Expression statement (function calls, strategy.entry, etc.)
    Expr(Expr),

    /// Break / Continue
    Break,
    Continue,
}

#[derive(Debug, Clone)]
pub enum ReassignOp {
    Set,        // :=
    Add,        // +=
    Sub,        // -=
    Mul,        // *=
    Div,        // /=
}

#[derive(Debug, Clone)]
pub struct FuncParam {
    pub name: String,
    pub type_hint: Option<String>,
    pub default: Option<Expr>,
}

/// Expression nodes
#[derive(Debug, Clone)]
pub enum Expr {
    /// Integer literal
    IntLit(i64),

    /// Float literal
    FloatLit(f64),

    /// String literal
    StrLit(String),

    /// Boolean literal
    BoolLit(bool),

    /// `na`
    Na,

    /// Variable reference: `close`, `myVar`
    Var(String),

    /// Historical reference: `close[1]`, `high[n]`
    HistRef {
        expr: Box<Expr>,
        offset: Box<Expr>,
    },

    /// Member access: `strategy.position_size`, `ta.ema`, `math.abs`
    Member {
        object: Box<Expr>,
        field: String,
    },

    /// Function/method call: `ta.ema(close, 14)`, `math.max(a, b)`
    Call {
        func: Box<Expr>,
        args: Vec<CallArg>,
    },

    /// Binary operation: `a + b`, `x and y`, `a == b`
    BinOp {
        left: Box<Expr>,
        op: BinOpKind,
        right: Box<Expr>,
    },

    /// Unary operation: `-x`, `not y`
    UnaryOp {
        op: UnaryOpKind,
        expr: Box<Expr>,
    },

    /// Ternary: `cond ? a : b`
    Ternary {
        condition: Box<Expr>,
        then_expr: Box<Expr>,
        else_expr: Box<Expr>,
    },

    /// Inline if expression: `x = if cond\n    a\nelse\n    b`
    IfExpr {
        condition: Box<Expr>,
        then_expr: Box<Expr>,
        else_expr: Option<Box<Expr>>,
    },

    /// Array literal: `["Both", "Long only"]`
    Array(Vec<Expr>),
}

#[derive(Debug, Clone)]
pub struct CallArg {
    pub name: Option<String>,  // named arg: `stop=100.0`
    pub value: Expr,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum BinOpKind {
    Add,
    Sub,
    Mul,
    Div,
    Mod,
    Eq,
    Neq,
    Lt,
    Gt,
    Lte,
    Gte,
    And,
    Or,
}

impl fmt::Display for BinOpKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            BinOpKind::Add => write!(f, "+"),
            BinOpKind::Sub => write!(f, "-"),
            BinOpKind::Mul => write!(f, "*"),
            BinOpKind::Div => write!(f, "/"),
            BinOpKind::Mod => write!(f, "%"),
            BinOpKind::Eq => write!(f, "=="),
            BinOpKind::Neq => write!(f, "!="),
            BinOpKind::Lt => write!(f, "<"),
            BinOpKind::Gt => write!(f, ">"),
            BinOpKind::Lte => write!(f, "<="),
            BinOpKind::Gte => write!(f, ">="),
            BinOpKind::And => write!(f, "and"),
            BinOpKind::Or => write!(f, "or"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum UnaryOpKind {
    Neg,    // -x
    Not,    // not x
}
