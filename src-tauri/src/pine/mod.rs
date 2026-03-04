pub mod tokenizer;
pub mod ast;
pub mod parser;
pub mod compiler;
pub mod vm;
pub mod builtins;
pub mod strategy_engine;
pub mod input_extractor;

use ast::Program;
use tokenizer::Tokenizer;
use parser::Parser;

/// Parse a Pine Script source string into an AST.
pub fn parse_pine(source: &str) -> Result<Program, String> {
    let mut tokenizer = Tokenizer::new(source);
    let tokens = tokenizer.tokenize()?;
    let mut parser = Parser::new(tokens);
    parser.parse()
}

/// Extract input definitions from a Pine Script source.
pub fn extract_inputs(source: &str) -> Result<Vec<input_extractor::PineInput>, String> {
    let program = parse_pine(source)?;
    Ok(input_extractor::extract_inputs(&program))
}
