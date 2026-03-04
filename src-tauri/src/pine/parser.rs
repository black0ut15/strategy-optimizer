/// Pine Script Parser
/// Recursive descent parser that converts a token stream into an AST.
/// Handles Pine's indentation-based blocks, operator precedence,
/// function definitions, and all expression types.

use super::ast::*;
use super::tokenizer::{Token, SpannedToken};

pub struct Parser {
    tokens: Vec<SpannedToken>,
    pos: usize,
}

impl Parser {
    pub fn new(tokens: Vec<SpannedToken>) -> Self {
        Self { tokens, pos: 0 }
    }

    pub fn parse(&mut self) -> Result<Program, String> {
        let mut statements = Vec::new();
        while !self.at_end() {
            self.skip_newlines();
            if self.at_end() {
                break;
            }
            // Skip indent tokens at top level
            if let Token::Indent(_) = self.peek() {
                self.advance();
            }
            if self.at_end() || matches!(self.peek(), Token::Eof) {
                break;
            }
            match self.parse_statement(0) {
                Ok(stmt) => statements.push(stmt),
                Err(e) => {
                    // Try to recover by skipping to next line
                    self.skip_to_newline();
                    eprintln!("Parse warning: {}", e);
                }
            }
        }
        Ok(Program { statements })
    }

    // ── Statement Parsing ─────────────────────────────────────

    fn parse_statement(&mut self, base_indent: usize) -> Result<Stmt, String> {
        self.skip_newlines();

        match self.peek() {
            Token::If => self.parse_if(base_indent),
            Token::For => self.parse_for(base_indent),
            Token::While => self.parse_while(base_indent),
            Token::Switch => self.parse_switch(base_indent),
            Token::Break => { self.advance(); Ok(Stmt::Break) }
            Token::Continue => { self.advance(); Ok(Stmt::Continue) }
            Token::Var | Token::Varip => self.parse_var_decl(base_indent),
            Token::TypeFloat | Token::TypeInt | Token::TypeBool | Token::TypeString => {
                self.parse_typed_decl(base_indent)
            }
            Token::Ident(_) => self.parse_ident_statement(base_indent),
            _ => {
                let expr = self.parse_expr()?;
                Ok(Stmt::Expr(expr))
            }
        }
    }

    fn parse_var_decl(&mut self, _base_indent: usize) -> Result<Stmt, String> {
        let is_var = matches!(self.peek(), Token::Var);
        let is_varip = matches!(self.peek(), Token::Varip);
        self.advance(); // consume var/varip

        // Optional type hint
        let type_hint = match self.peek() {
            Token::TypeFloat => { self.advance(); Some("float".to_string()) }
            Token::TypeInt => { self.advance(); Some("int".to_string()) }
            Token::TypeBool => { self.advance(); Some("bool".to_string()) }
            Token::TypeString => { self.advance(); Some("string".to_string()) }
            _ => None,
        };

        let name = self.expect_ident()?;
        self.expect(Token::Assign)?;
        let value = self.parse_expr()?;

        Ok(Stmt::VarDecl {
            name,
            type_hint,
            is_var,
            is_varip,
            value,
        })
    }

    fn parse_typed_decl(&mut self, _base_indent: usize) -> Result<Stmt, String> {
        let type_hint = match self.peek() {
            Token::TypeFloat => "float",
            Token::TypeInt => "int",
            Token::TypeBool => "bool",
            Token::TypeString => "string",
            _ => return Err("Expected type".to_string()),
        };
        self.advance();
        let type_str = type_hint.to_string();

        let name = self.expect_ident()?;

        if matches!(self.peek(), Token::Assign) {
            self.advance();
            let value = self.parse_expr()?;
            Ok(Stmt::VarDecl {
                name,
                type_hint: Some(type_str),
                is_var: false,
                is_varip: false,
                value,
            })
        } else {
            // Type annotation without value — default to na
            Ok(Stmt::VarDecl {
                name,
                type_hint: Some(type_str),
                is_var: false,
                is_varip: false,
                value: Expr::FloatLit(0.0),
            })
        }
    }

    fn parse_ident_statement(&mut self, base_indent: usize) -> Result<Stmt, String> {
        let name = self.expect_ident()?;

        match self.peek() {
            // Assignment: `x = expr`
            Token::Assign => {
                self.advance();
                // Check if this is a function definition: `name(params) => body`
                // Actually, function defs look like: `f_name(p1, p2) =>\n    body`
                // But `name = expr` is just assignment. Let's parse the value.
                let value = self.parse_expr()?;
                Ok(Stmt::VarDecl {
                    name,
                    type_hint: None,
                    is_var: false,
                    is_varip: false,
                    value,
                })
            }

            // Reassignment: `x := expr`, `x += expr`
            Token::Reassign => {
                self.advance();
                let value = self.parse_expr()?;
                Ok(Stmt::Reassign { name, op: ReassignOp::Set, value })
            }
            Token::PlusAssign => {
                self.advance();
                let value = self.parse_expr()?;
                Ok(Stmt::Reassign { name, op: ReassignOp::Add, value })
            }
            Token::MinusAssign => {
                self.advance();
                let value = self.parse_expr()?;
                Ok(Stmt::Reassign { name, op: ReassignOp::Sub, value })
            }
            Token::StarAssign => {
                self.advance();
                let value = self.parse_expr()?;
                Ok(Stmt::Reassign { name, op: ReassignOp::Mul, value })
            }
            Token::SlashAssign => {
                self.advance();
                let value = self.parse_expr()?;
                Ok(Stmt::Reassign { name, op: ReassignOp::Div, value })
            }

            // Function definition: `f_name(p1, p2) =>`
            Token::LParen => {
                // Could be function def or function call
                // Peek ahead to see if there's => after the closing paren
                if self.is_func_def() {
                    self.parse_func_def(name, base_indent)
                } else {
                    // It's a function call expression
                    let func_expr = Expr::Var(name);
                    let call = self.parse_call(func_expr)?;
                    // Check for member access after call
                    let expr = self.parse_postfix(call)?;
                    Ok(Stmt::Expr(expr))
                }
            }

            // Member access followed by call: `strategy.entry(...)`, `ta.ema(...)`
            Token::Dot => {
                let var_expr = Expr::Var(name);
                let expr = self.parse_postfix(var_expr)?;
                // Check for assignment after member access + call
                if matches!(self.peek(), Token::Reassign) {
                    // e.g. unlikely but handle gracefully
                    self.advance();
                    let value = self.parse_expr()?;
                    // This would be something like `obj.field := value`
                    // Not standard Pine but handle it
                    Ok(Stmt::Expr(value))
                } else {
                    Ok(Stmt::Expr(expr))
                }
            }

            _ => {
                // Just a bare identifier — treat as expression
                Ok(Stmt::Expr(Expr::Var(name)))
            }
        }
    }

    fn parse_func_def(&mut self, name: String, base_indent: usize) -> Result<Stmt, String> {
        self.expect(Token::LParen)?;
        let params = self.parse_func_params()?;
        self.expect(Token::RParen)?;
        self.expect(Token::Arrow)?;

        // Check if single-line or multi-line
        self.skip_newlines();
        if let Token::Indent(n) = self.peek() {
            if n > base_indent {
                // Multi-line body
                let body = self.parse_block(n)?;
                return Ok(Stmt::FuncDef {
                    name,
                    params,
                    body,
                    single_expr: None,
                });
            }
        }

        // Single-line expression
        let expr = self.parse_expr()?;
        Ok(Stmt::FuncDef {
            name,
            params,
            body: Vec::new(),
            single_expr: Some(expr),
        })
    }

    fn parse_func_params(&mut self) -> Result<Vec<FuncParam>, String> {
        let mut params = Vec::new();
        if matches!(self.peek(), Token::RParen) {
            return Ok(params);
        }

        loop {
            // Optional type hint
            let type_hint = match self.peek() {
                Token::TypeFloat => { self.advance(); Some("float".to_string()) }
                Token::TypeInt => { self.advance(); Some("int".to_string()) }
                Token::TypeBool => { self.advance(); Some("bool".to_string()) }
                Token::TypeString => { self.advance(); Some("string".to_string()) }
                _ => None,
            };

            let name = self.expect_ident()?;

            // Optional default value
            let default = if matches!(self.peek(), Token::Assign) {
                self.advance();
                Some(self.parse_expr()?)
            } else {
                None
            };

            params.push(FuncParam { name, type_hint, default });

            if !matches!(self.peek(), Token::Comma) {
                break;
            }
            self.advance(); // consume comma
        }

        Ok(params)
    }

    fn parse_if(&mut self, base_indent: usize) -> Result<Stmt, String> {
        self.expect(Token::If)?;
        let condition = self.parse_expr()?;
        self.skip_newlines();

        // Parse body (indented block)
        let body_indent = self.get_indent().unwrap_or(base_indent + 4);
        let body = self.parse_block(body_indent)?;

        // Parse else if / else
        let mut else_ifs = Vec::new();
        let mut else_body = None;

        loop {
            self.skip_newlines();
            // Check for else at the base indent level
            let current_indent = self.get_indent().unwrap_or(0);
            if current_indent != base_indent {
                break;
            }

            if let Token::Indent(_) = self.peek() {
                self.advance();
            }

            if matches!(self.peek(), Token::Else) {
                self.advance(); // consume 'else'
                if matches!(self.peek(), Token::If) {
                    // else if
                    self.advance(); // consume 'if'
                    let cond = self.parse_expr()?;
                    self.skip_newlines();
                    let ei_indent = self.get_indent().unwrap_or(base_indent + 4);
                    let ei_body = self.parse_block(ei_indent)?;
                    else_ifs.push((cond, ei_body));
                } else {
                    // else
                    self.skip_newlines();
                    let else_indent = self.get_indent().unwrap_or(base_indent + 4);
                    else_body = Some(self.parse_block(else_indent)?);
                    break;
                }
            } else {
                break;
            }
        }

        Ok(Stmt::If {
            condition,
            body,
            else_ifs,
            else_body,
        })
    }

    fn parse_for(&mut self, base_indent: usize) -> Result<Stmt, String> {
        self.expect(Token::For)?;
        let var_name = self.expect_ident()?;
        self.expect(Token::Assign)?;
        let start = self.parse_expr()?;
        self.expect(Token::To)?;
        let end = self.parse_expr()?;

        // Optional `by step`
        // Pine uses `for i = 0 to 10 by 2` but simpler strategies don't use `by`
        // For now skip `by`

        self.skip_newlines();
        let body_indent = self.get_indent().unwrap_or(base_indent + 4);
        let body = self.parse_block(body_indent)?;

        Ok(Stmt::For {
            var_name,
            start,
            end,
            step: None,
            body,
        })
    }

    fn parse_while(&mut self, base_indent: usize) -> Result<Stmt, String> {
        self.expect(Token::While)?;
        let condition = self.parse_expr()?;
        self.skip_newlines();
        let body_indent = self.get_indent().unwrap_or(base_indent + 4);
        let body = self.parse_block(body_indent)?;

        Ok(Stmt::While { condition, body })
    }

    fn parse_switch(&mut self, base_indent: usize) -> Result<Stmt, String> {
        self.expect(Token::Switch)?;

        // Optional switch value
        let value = if !matches!(self.peek(), Token::Newline | Token::Eof) {
            Some(self.parse_expr()?)
        } else {
            None
        };

        self.skip_newlines();
        let case_indent = self.get_indent().unwrap_or(base_indent + 4);

        let mut cases = Vec::new();
        let mut default = None;

        loop {
            self.skip_newlines();
            let indent = self.get_indent();
            if indent.map_or(true, |n| n < case_indent) {
                break;
            }
            if let Token::Indent(_) = self.peek() {
                self.advance();
            }

            if matches!(self.peek(), Token::Arrow) {
                // Default case: `=> body`
                self.advance();
                let expr = self.parse_expr()?;
                default = Some(vec![Stmt::Expr(expr)]);
            } else {
                let case_expr = self.parse_expr()?;
                self.expect(Token::Arrow)?;
                let body_expr = self.parse_expr()?;
                cases.push((case_expr, vec![Stmt::Expr(body_expr)]));
            }
        }

        Ok(Stmt::Switch {
            value,
            cases,
            default,
        })
    }

    // ── Block Parsing ─────────────────────────────────────────

    fn parse_block(&mut self, expected_indent: usize) -> Result<Vec<Stmt>, String> {
        let mut stmts = Vec::new();

        loop {
            self.skip_newlines();
            let indent = self.get_indent();

            match indent {
                Some(n) if n >= expected_indent => {
                    if let Token::Indent(_) = self.peek() {
                        self.advance();
                    }
                    stmts.push(self.parse_statement(expected_indent)?);
                }
                _ => break,
            }
        }

        Ok(stmts)
    }

    // ── Expression Parsing (Pratt parser) ─────────────────────

    fn parse_expr(&mut self) -> Result<Expr, String> {
        self.parse_ternary()
    }

    fn parse_ternary(&mut self) -> Result<Expr, String> {
        let expr = self.parse_or()?;

        if matches!(self.peek(), Token::Question) {
            self.advance();
            let then_expr = self.parse_expr()?;
            self.expect(Token::Colon)?;
            let else_expr = self.parse_expr()?;
            Ok(Expr::Ternary {
                condition: Box::new(expr),
                then_expr: Box::new(then_expr),
                else_expr: Box::new(else_expr),
            })
        } else {
            Ok(expr)
        }
    }

    fn parse_or(&mut self) -> Result<Expr, String> {
        let mut left = self.parse_and()?;
        while matches!(self.peek(), Token::Or) {
            self.advance();
            let right = self.parse_and()?;
            left = Expr::BinOp {
                left: Box::new(left),
                op: BinOpKind::Or,
                right: Box::new(right),
            };
        }
        Ok(left)
    }

    fn parse_and(&mut self) -> Result<Expr, String> {
        let mut left = self.parse_comparison()?;
        while matches!(self.peek(), Token::And) {
            self.advance();
            let right = self.parse_comparison()?;
            left = Expr::BinOp {
                left: Box::new(left),
                op: BinOpKind::And,
                right: Box::new(right),
            };
        }
        Ok(left)
    }

    fn parse_comparison(&mut self) -> Result<Expr, String> {
        let mut left = self.parse_addition()?;
        loop {
            let op = match self.peek() {
                Token::Eq => BinOpKind::Eq,
                Token::Neq => BinOpKind::Neq,
                Token::Lt => BinOpKind::Lt,
                Token::Gt => BinOpKind::Gt,
                Token::Lte => BinOpKind::Lte,
                Token::Gte => BinOpKind::Gte,
                _ => break,
            };
            self.advance();
            let right = self.parse_addition()?;
            left = Expr::BinOp {
                left: Box::new(left),
                op,
                right: Box::new(right),
            };
        }
        Ok(left)
    }

    fn parse_addition(&mut self) -> Result<Expr, String> {
        let mut left = self.parse_multiplication()?;
        loop {
            let op = match self.peek() {
                Token::Plus => BinOpKind::Add,
                Token::Minus => BinOpKind::Sub,
                _ => break,
            };
            self.advance();
            let right = self.parse_multiplication()?;
            left = Expr::BinOp {
                left: Box::new(left),
                op,
                right: Box::new(right),
            };
        }
        Ok(left)
    }

    fn parse_multiplication(&mut self) -> Result<Expr, String> {
        let mut left = self.parse_unary()?;
        loop {
            let op = match self.peek() {
                Token::Star => BinOpKind::Mul,
                Token::Slash => BinOpKind::Div,
                Token::Percent => BinOpKind::Mod,
                _ => break,
            };
            self.advance();
            let right = self.parse_unary()?;
            left = Expr::BinOp {
                left: Box::new(left),
                op,
                right: Box::new(right),
            };
        }
        Ok(left)
    }

    fn parse_unary(&mut self) -> Result<Expr, String> {
        match self.peek() {
            Token::Minus => {
                self.advance();
                let expr = self.parse_unary()?;
                Ok(Expr::UnaryOp {
                    op: UnaryOpKind::Neg,
                    expr: Box::new(expr),
                })
            }
            Token::Not => {
                self.advance();
                let expr = self.parse_unary()?;
                Ok(Expr::UnaryOp {
                    op: UnaryOpKind::Not,
                    expr: Box::new(expr),
                })
            }
            _ => self.parse_postfix_expr(),
        }
    }

    fn parse_postfix_expr(&mut self) -> Result<Expr, String> {
        let expr = self.parse_primary()?;
        self.parse_postfix(expr)
    }

    fn parse_postfix(&mut self, mut expr: Expr) -> Result<Expr, String> {
        loop {
            match self.peek() {
                // Member access: expr.field
                Token::Dot => {
                    self.advance();
                    let field = self.expect_ident()?;
                    expr = Expr::Member {
                        object: Box::new(expr),
                        field,
                    };
                }
                // Call: expr(args)
                Token::LParen => {
                    expr = self.parse_call(expr)?;
                }
                // Historical reference: expr[n]
                Token::LBracket => {
                    self.advance();
                    let offset = self.parse_expr()?;
                    self.expect(Token::RBracket)?;
                    expr = Expr::HistRef {
                        expr: Box::new(expr),
                        offset: Box::new(offset),
                    };
                }
                _ => break,
            }
        }
        Ok(expr)
    }

    fn parse_call(&mut self, func: Expr) -> Result<Expr, String> {
        self.expect(Token::LParen)?;
        let args = self.parse_call_args()?;
        self.expect(Token::RParen)?;
        Ok(Expr::Call {
            func: Box::new(func),
            args,
        })
    }

    fn parse_call_args(&mut self) -> Result<Vec<CallArg>, String> {
        let mut args = Vec::new();
        if matches!(self.peek(), Token::RParen) {
            return Ok(args);
        }

        loop {
            // Check for named argument: `name=value`
            let saved_pos = self.pos;
            if let Token::Ident(name) = self.peek() {
                let arg_name = name.clone();
                self.advance();
                if matches!(self.peek(), Token::Assign) {
                    self.advance();
                    let value = self.parse_expr()?;
                    args.push(CallArg {
                        name: Some(arg_name),
                        value,
                    });
                } else {
                    // Not a named arg, restore position
                    self.pos = saved_pos;
                    let value = self.parse_expr()?;
                    args.push(CallArg { name: None, value });
                }
            } else {
                let value = self.parse_expr()?;
                args.push(CallArg { name: None, value });
            }

            if !matches!(self.peek(), Token::Comma) {
                break;
            }
            self.advance(); // consume comma

            // Allow trailing comma
            if matches!(self.peek(), Token::RParen) {
                break;
            }
        }

        Ok(args)
    }

    fn parse_primary(&mut self) -> Result<Expr, String> {
        match self.peek() {
            Token::Int(n) => {
                let n = n;
                self.advance();
                Ok(Expr::IntLit(n))
            }
            Token::Float(n) => {
                let n = n;
                self.advance();
                Ok(Expr::FloatLit(n))
            }
            Token::Str(s) => {
                let s = s.clone();
                self.advance();
                Ok(Expr::StrLit(s))
            }
            Token::Bool(b) => {
                let b = b;
                self.advance();
                Ok(Expr::BoolLit(b))
            }
            Token::Na => {
                self.advance();
                Ok(Expr::Na)
            }
            Token::Ident(name) => {
                let name = name.clone();
                self.advance();
                Ok(Expr::Var(name))
            }
            Token::LParen => {
                self.advance();
                let expr = self.parse_expr()?;
                self.expect(Token::RParen)?;
                Ok(expr)
            }
            Token::LBracket => {
                // Array literal
                self.advance();
                let mut elems = Vec::new();
                while !matches!(self.peek(), Token::RBracket | Token::Eof) {
                    elems.push(self.parse_expr()?);
                    if matches!(self.peek(), Token::Comma) {
                        self.advance();
                    }
                }
                self.expect(Token::RBracket)?;
                Ok(Expr::Array(elems))
            }
            _ => {
                let tok = self.peek();
                Err(format!("Unexpected token {:?} at line {}", tok, self.current_line()))
            }
        }
    }

    // ── Helpers ───────────────────────────────────────────────

    fn peek(&self) -> Token {
        if self.pos < self.tokens.len() {
            self.tokens[self.pos].token.clone()
        } else {
            Token::Eof
        }
    }

    fn advance(&mut self) -> Token {
        let tok = self.peek();
        if self.pos < self.tokens.len() {
            self.pos += 1;
        }
        tok
    }

    fn at_end(&self) -> bool {
        self.pos >= self.tokens.len() || matches!(self.tokens[self.pos].token, Token::Eof)
    }

    fn expect(&mut self, expected: Token) -> Result<(), String> {
        let tok = self.peek();
        if std::mem::discriminant(&tok) == std::mem::discriminant(&expected) {
            self.advance();
            Ok(())
        } else {
            Err(format!(
                "Expected {:?}, got {:?} at line {}",
                expected, tok, self.current_line()
            ))
        }
    }

    fn expect_ident(&mut self) -> Result<String, String> {
        match self.peek() {
            Token::Ident(name) => {
                self.advance();
                Ok(name)
            }
            other => Err(format!(
                "Expected identifier, got {:?} at line {}",
                other, self.current_line()
            )),
        }
    }

    fn skip_newlines(&mut self) {
        while matches!(self.peek(), Token::Newline) {
            self.advance();
        }
    }

    fn skip_to_newline(&mut self) {
        while !matches!(self.peek(), Token::Newline | Token::Eof) {
            self.advance();
        }
        if matches!(self.peek(), Token::Newline) {
            self.advance();
        }
    }

    fn get_indent(&self) -> Option<usize> {
        match self.peek() {
            Token::Indent(n) => Some(n),
            Token::Eof => None,
            _ => Some(0),
        }
    }

    fn current_line(&self) -> usize {
        if self.pos < self.tokens.len() {
            self.tokens[self.pos].span.line
        } else {
            0
        }
    }

    /// Look ahead to determine if `ident(...)` is a function def (has `=>` after `)`)
    fn is_func_def(&self) -> bool {
        let mut depth = 0;
        let mut i = self.pos;
        while i < self.tokens.len() {
            match &self.tokens[i].token {
                Token::LParen => depth += 1,
                Token::RParen => {
                    depth -= 1;
                    if depth == 0 {
                        // Check next non-newline token for =>
                        let mut j = i + 1;
                        while j < self.tokens.len() && matches!(self.tokens[j].token, Token::Newline | Token::Indent(_)) {
                            j += 1;
                        }
                        return j < self.tokens.len() && matches!(self.tokens[j].token, Token::Arrow);
                    }
                }
                Token::Newline | Token::Eof => return false,
                _ => {}
            }
            i += 1;
        }
        false
    }
}
