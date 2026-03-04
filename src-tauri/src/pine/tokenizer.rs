/// Pine Script Tokenizer
/// Converts raw Pine Script text into a stream of tokens.
/// Handles Pine's indentation-based blocks, operators, literals, and keywords.

use std::fmt;

#[derive(Debug, Clone, PartialEq)]
pub enum Token {
    // Literals
    Int(i64),
    Float(f64),
    Str(String),
    Bool(bool),
    Na,

    // Identifiers & keywords
    Ident(String),       // variable names, function names
    Var,                 // `var` keyword
    Varip,               // `varip` keyword
    If,
    Else,
    For,
    To,
    While,
    Break,
    Continue,
    Switch,
    Import,
    Export,
    True,
    False,
    Not,
    And,
    Or,

    // Type keywords
    TypeFloat,
    TypeInt,
    TypeBool,
    TypeString,
    TypeColor,

    // Operators
    Plus,          // +
    Minus,         // -
    Star,          // *
    Slash,         // /
    Percent,       // %
    Eq,            // ==
    Neq,           // !=
    Lt,            // <
    Gt,            // >
    Lte,           // <=
    Gte,           // >=
    Assign,        // =
    Reassign,      // :=
    PlusAssign,    // +=
    MinusAssign,   // -=
    StarAssign,    // *=
    SlashAssign,   // /=
    Arrow,         // =>
    Question,      // ?
    Colon,         // :

    // Delimiters
    LParen,        // (
    RParen,        // )
    LBracket,      // [
    RBracket,      // ]
    Comma,         // ,
    Dot,           // .

    // Structure
    Newline,
    Indent(usize), // indentation level (number of spaces)
    Eof,
}

impl fmt::Display for Token {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Token::Int(n) => write!(f, "{}", n),
            Token::Float(n) => write!(f, "{}", n),
            Token::Str(s) => write!(f, "\"{}\"", s),
            Token::Bool(b) => write!(f, "{}", b),
            Token::Na => write!(f, "na"),
            Token::Ident(s) => write!(f, "{}", s),
            Token::Var => write!(f, "var"),
            Token::Varip => write!(f, "varip"),
            Token::If => write!(f, "if"),
            Token::Else => write!(f, "else"),
            Token::For => write!(f, "for"),
            Token::To => write!(f, "to"),
            Token::While => write!(f, "while"),
            Token::Break => write!(f, "break"),
            Token::Continue => write!(f, "continue"),
            Token::Switch => write!(f, "switch"),
            Token::Import => write!(f, "import"),
            Token::Export => write!(f, "export"),
            Token::True => write!(f, "true"),
            Token::False => write!(f, "false"),
            Token::Not => write!(f, "not"),
            Token::And => write!(f, "and"),
            Token::Or => write!(f, "or"),
            _ => write!(f, "{:?}", self),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Span {
    pub line: usize,
    pub col: usize,
}

#[derive(Debug, Clone)]
pub struct SpannedToken {
    pub token: Token,
    pub span: Span,
}

pub struct Tokenizer {
    source: Vec<char>,
    pos: usize,
    line: usize,
    col: usize,
    at_line_start: bool,
}

impl Tokenizer {
    pub fn new(source: &str) -> Self {
        Self {
            source: source.chars().collect(),
            pos: 0,
            line: 1,
            col: 1,
            at_line_start: true,
        }
    }

    pub fn tokenize(&mut self) -> Result<Vec<SpannedToken>, String> {
        let mut tokens = Vec::new();

        while self.pos < self.source.len() {
            // Handle line starts — emit indent tokens
            if self.at_line_start {
                let indent = self.count_indent();
                self.at_line_start = false;

                // Skip blank lines
                if self.pos < self.source.len() && self.peek() == '\n' {
                    self.advance();
                    self.at_line_start = true;
                    continue;
                }
                // Skip comment-only lines
                if self.pos + 1 < self.source.len() && self.peek() == '/' && self.source[self.pos + 1] == '/' {
                    self.skip_line_comment();
                    self.at_line_start = true;
                    continue;
                }

                if self.pos >= self.source.len() {
                    break;
                }

                tokens.push(SpannedToken {
                    token: Token::Indent(indent),
                    span: Span { line: self.line, col: 1 },
                });
                continue;
            }

            let ch = self.peek();

            // Skip inline whitespace (not newlines)
            if ch == ' ' || ch == '\t' {
                self.advance();
                continue;
            }

            // Newline
            if ch == '\n' {
                tokens.push(SpannedToken {
                    token: Token::Newline,
                    span: self.span(),
                });
                self.advance();
                self.at_line_start = true;
                continue;
            }

            // Carriage return
            if ch == '\r' {
                self.advance();
                continue;
            }

            // Line comment
            if ch == '/' && self.peek_at(1) == Some('/') {
                self.skip_line_comment();
                self.at_line_start = true;
                continue;
            }

            // Block comment /* ... */
            if ch == '/' && self.peek_at(1) == Some('*') {
                self.skip_block_comment();
                continue;
            }

            // String literals
            if ch == '"' {
                tokens.push(self.read_string('"')?);
                continue;
            }
            if ch == '\'' {
                tokens.push(self.read_string('\'')?);
                continue;
            }

            // Numbers
            if ch.is_ascii_digit() || (ch == '.' && self.peek_at(1).map_or(false, |c| c.is_ascii_digit())) {
                tokens.push(self.read_number());
                continue;
            }

            // Identifiers and keywords
            if ch.is_ascii_alphabetic() || ch == '_' {
                tokens.push(self.read_ident());
                continue;
            }

            // Hash color literals (skip for backtesting, treat as identifier)
            if ch == '#' {
                tokens.push(self.read_color_literal());
                continue;
            }

            // Operators and punctuation
            let span = self.span();
            let tok = match ch {
                '+' => {
                    self.advance();
                    if self.pos < self.source.len() && self.peek() == '=' {
                        self.advance();
                        Token::PlusAssign
                    } else {
                        Token::Plus
                    }
                }
                '-' => {
                    self.advance();
                    if self.pos < self.source.len() && self.peek() == '=' {
                        self.advance();
                        Token::MinusAssign
                    } else {
                        Token::Minus
                    }
                }
                '*' => {
                    self.advance();
                    if self.pos < self.source.len() && self.peek() == '=' {
                        self.advance();
                        Token::StarAssign
                    } else {
                        Token::Star
                    }
                }
                '/' => {
                    self.advance();
                    if self.pos < self.source.len() && self.peek() == '=' {
                        self.advance();
                        Token::SlashAssign
                    } else {
                        Token::Slash
                    }
                }
                '%' => { self.advance(); Token::Percent }
                '=' => {
                    self.advance();
                    if self.pos < self.source.len() && self.peek() == '=' {
                        self.advance();
                        Token::Eq
                    } else if self.pos < self.source.len() && self.peek() == '>' {
                        self.advance();
                        Token::Arrow
                    } else {
                        Token::Assign
                    }
                }
                ':' => {
                    self.advance();
                    if self.pos < self.source.len() && self.peek() == '=' {
                        self.advance();
                        Token::Reassign
                    } else {
                        Token::Colon
                    }
                }
                '!' => {
                    self.advance();
                    if self.pos < self.source.len() && self.peek() == '=' {
                        self.advance();
                        Token::Neq
                    } else {
                        return Err(format!("Unexpected '!' at line {}, col {}", span.line, span.col));
                    }
                }
                '<' => {
                    self.advance();
                    if self.pos < self.source.len() && self.peek() == '=' {
                        self.advance();
                        Token::Lte
                    } else {
                        Token::Lt
                    }
                }
                '>' => {
                    self.advance();
                    if self.pos < self.source.len() && self.peek() == '=' {
                        self.advance();
                        Token::Gte
                    } else {
                        Token::Gt
                    }
                }
                '?' => { self.advance(); Token::Question }
                '(' => { self.advance(); Token::LParen }
                ')' => { self.advance(); Token::RParen }
                '[' => { self.advance(); Token::LBracket }
                ']' => { self.advance(); Token::RBracket }
                ',' => { self.advance(); Token::Comma }
                '.' => { self.advance(); Token::Dot }
                _ => {
                    self.advance();
                    continue; // skip unknown characters (emoji etc.)
                }
            };

            tokens.push(SpannedToken { token: tok, span });
        }

        tokens.push(SpannedToken {
            token: Token::Eof,
            span: self.span(),
        });

        Ok(tokens)
    }

    fn peek(&self) -> char {
        self.source[self.pos]
    }

    fn peek_at(&self, offset: usize) -> Option<char> {
        self.source.get(self.pos + offset).copied()
    }

    fn advance(&mut self) -> char {
        let ch = self.source[self.pos];
        self.pos += 1;
        if ch == '\n' {
            self.line += 1;
            self.col = 1;
        } else {
            self.col += 1;
        }
        ch
    }

    fn span(&self) -> Span {
        Span { line: self.line, col: self.col }
    }

    fn count_indent(&mut self) -> usize {
        let mut spaces = 0;
        while self.pos < self.source.len() {
            match self.peek() {
                ' ' => { spaces += 1; self.advance(); }
                '\t' => { spaces += 4; self.advance(); }
                _ => break,
            }
        }
        spaces
    }

    fn skip_line_comment(&mut self) {
        while self.pos < self.source.len() && self.peek() != '\n' {
            self.advance();
        }
        if self.pos < self.source.len() {
            self.advance(); // consume newline
        }
    }

    fn skip_block_comment(&mut self) {
        self.advance(); // skip /
        self.advance(); // skip *
        while self.pos + 1 < self.source.len() {
            if self.peek() == '*' && self.source[self.pos + 1] == '/' {
                self.advance();
                self.advance();
                return;
            }
            self.advance();
        }
    }

    fn read_string(&mut self, quote: char) -> Result<SpannedToken, String> {
        let span = self.span();
        self.advance(); // skip opening quote
        let mut s = String::new();
        while self.pos < self.source.len() && self.peek() != quote {
            if self.peek() == '\\' {
                self.advance();
                if self.pos < self.source.len() {
                    match self.advance() {
                        'n' => s.push('\n'),
                        't' => s.push('\t'),
                        '\\' => s.push('\\'),
                        '"' => s.push('"'),
                        '\'' => s.push('\''),
                        c => { s.push('\\'); s.push(c); }
                    }
                }
            } else if self.peek() == '\n' {
                break; // unterminated string at newline
            } else {
                s.push(self.advance());
            }
        }
        if self.pos < self.source.len() && self.peek() == quote {
            self.advance(); // skip closing quote
        }
        Ok(SpannedToken { token: Token::Str(s), span })
    }

    fn read_number(&mut self) -> SpannedToken {
        let span = self.span();
        let mut num_str = String::new();
        let mut has_dot = false;

        while self.pos < self.source.len() {
            let ch = self.peek();
            if ch.is_ascii_digit() {
                num_str.push(self.advance());
            } else if ch == '.' && !has_dot {
                // Check it's not a method call like `123.tostring`
                if self.peek_at(1).map_or(false, |c| c.is_ascii_digit()) {
                    has_dot = true;
                    num_str.push(self.advance());
                } else {
                    break;
                }
            } else if ch == '_' {
                // Pine allows underscores in number literals (10_000)
                // Pine allows underscores in number literals (10_000)
                self.advance(); // skip but don't add to num_str
            } else {
                break;
            }
        }

        if has_dot {
            let val: f64 = num_str.parse().unwrap_or(0.0);
            SpannedToken { token: Token::Float(val), span }
        } else {
            let val: i64 = num_str.parse().unwrap_or(0);
            SpannedToken { token: Token::Int(val), span }
        }
    }

    fn read_ident(&mut self) -> SpannedToken {
        let span = self.span();
        let mut name = String::new();
        while self.pos < self.source.len() {
            let ch = self.peek();
            if ch.is_ascii_alphanumeric() || ch == '_' {
                name.push(self.advance());
            } else {
                break;
            }
        }

        let token = match name.as_str() {
            "var" => Token::Var,
            "varip" => Token::Varip,
            "if" => Token::If,
            "else" => Token::Else,
            "for" => Token::For,
            "to" => Token::To,
            "while" => Token::While,
            "break" => Token::Break,
            "continue" => Token::Continue,
            "switch" => Token::Switch,
            "import" => Token::Import,
            "export" => Token::Export,
            "true" => Token::Bool(true),
            "false" => Token::Bool(false),
            "not" => Token::Not,
            "and" => Token::And,
            "or" => Token::Or,
            "na" => Token::Na,
            "float" => Token::TypeFloat,
            "int" => Token::TypeInt,
            "bool" => Token::TypeBool,
            "string" => Token::TypeString,
            "color" => Token::TypeColor,
            _ => Token::Ident(name),
        };

        SpannedToken { token, span }
    }

    fn read_color_literal(&mut self) -> SpannedToken {
        let span = self.span();
        self.advance(); // skip #
        let mut hex = String::from("#");
        while self.pos < self.source.len() && self.peek().is_ascii_hexdigit() {
            hex.push(self.advance());
        }
        // Treat color literals as strings for backtesting purposes
        SpannedToken { token: Token::Str(hex), span }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_tokens() {
        let mut tok = Tokenizer::new("x = 42\ny = 3.14");
        let tokens = tok.tokenize().unwrap();
        let types: Vec<&Token> = tokens.iter().map(|t| &t.token).collect();
        assert!(matches!(types[1], Token::Ident(s) if s == "x"));
        assert!(matches!(types[2], Token::Assign));
        assert!(matches!(types[3], Token::Int(42)));
    }

    #[test]
    fn test_operators() {
        let mut tok = Tokenizer::new("a := b + c == d and not e");
        let tokens = tok.tokenize().unwrap();
        let types: Vec<&Token> = tokens.iter()
            .filter(|t| !matches!(t.token, Token::Indent(_) | Token::Newline | Token::Eof))
            .map(|t| &t.token)
            .collect();
        assert!(matches!(types[1], Token::Reassign));
        assert!(matches!(types[3], Token::Plus));
        assert!(matches!(types[5], Token::Eq));
        assert!(matches!(types[7], Token::And));
        assert!(matches!(types[8], Token::Not));
    }

    #[test]
    fn test_string_literal() {
        let mut tok = Tokenizer::new(r#"s = "hello world""#);
        let tokens = tok.tokenize().unwrap();
        let strs: Vec<&Token> = tokens.iter()
            .filter(|t| matches!(t.token, Token::Str(_)))
            .map(|t| &t.token)
            .collect();
        assert_eq!(strs.len(), 1);
        assert!(matches!(strs[0], Token::Str(s) if s == "hello world"));
    }

    #[test]
    fn test_pine_number_underscores() {
        let mut tok = Tokenizer::new("x = 10_000");
        let tokens = tok.tokenize().unwrap();
        let nums: Vec<&Token> = tokens.iter()
            .filter(|t| matches!(t.token, Token::Int(_)))
            .map(|t| &t.token)
            .collect();
        assert!(matches!(nums[0], Token::Int(10000)));
    }

    #[test]
    fn test_indentation() {
        let mut tok = Tokenizer::new("if x\n    y = 1\n    z = 2\n");
        let tokens = tok.tokenize().unwrap();
        let indents: Vec<usize> = tokens.iter()
            .filter_map(|t| match &t.token {
                Token::Indent(n) => Some(*n),
                _ => None,
            })
            .collect();
        assert_eq!(indents, vec![0, 4, 4]);
    }
}
