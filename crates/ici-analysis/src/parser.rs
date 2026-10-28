//! Pinned native Python parser; recovered/unsupported syntax never becomes complete evidence.
use crate::scope::ScopeGraph;
use crate::source::{SourceDocument, SourceError};
use ruff_python_ast::token::TokenKind;
use ruff_python_ast::visitor::source_order::{SourceOrderVisitor, TraversalSignal};
use ruff_python_ast::{AnyNodeRef, Mod, PythonVersion};
use ruff_python_parser::{Mode, ParseOptions, Parsed, lexer, parse_unchecked};
use ruff_text_size::Ranged;

pub const PARSER_IDENTITY: &str =
    "ruff-11c76bf48fdac06b2f240cba502eda96da4dce77:source-v1:scope-v1";
pub const MAX_AST_NODES: usize = 250_000;
pub struct ParsedFile {
    pub source: SourceDocument,
    pub parsed: Parsed<Mod>,
    pub scopes: ScopeGraph,
    pub node_count: usize,
    pub target: PythonVersion,
    pub metrics: std::sync::OnceLock<Vec<crate::metrics::FunctionMetric>>,
}
impl ParsedFile {
    pub fn parse(source: SourceDocument, target: &str) -> Result<Self, SourceError> {
        let target = match target {
            "" | "3.14" | "py314" => PythonVersion::PY314,
            "3.13" | "py313" => PythonVersion::PY313,
            "3.12" | "py312" => PythonVersion::PY312,
            "3.11" | "py311" => PythonVersion::PY311,
            "3.10" | "py310" => PythonVersion::PY310,
            _ => {
                return Err(SourceError::new(
                    "unsupported-target",
                    "Tested native Python grammar targets are 3.10 through 3.14",
                ));
            }
        };
        lexical_budget(&source.text)?;
        let parsed = parse_unchecked(
            &source.text,
            ParseOptions::from(Mode::Module).with_target_version(target),
        );
        if let Some(error) = parsed.errors().first() {
            let (line, column) = source.location(error.range().start().to_usize());
            return Err(SourceError {
                code: "invalid-syntax",
                message: "Python source contains a syntax error; recovered AST was not analyzed"
                    .into(),
                line,
                column,
            });
        }
        if let Some(error) = parsed.unsupported_syntax_errors().first() {
            let (line, column) = source.location(error.range().start().to_usize());
            return Err(SourceError {
                code: "unsupported-syntax",
                message: format!("Python source uses syntax unavailable in target {target}"),
                line,
                column,
            });
        }
        let mut budget = AstBudget::default();
        budget.visit_mod(parsed.syntax());
        if budget.exceeded {
            return Err(SourceError::new(
                "ast-limit",
                "Python AST exceeds bounded node or nesting limits",
            ));
        }
        let scopes = ScopeGraph::build(parsed.syntax());
        if let Some(error) = scopes
            .semantic_errors(parsed.syntax(), &source.text, target)
            .first()
        {
            let (line, column) = source.location(error.range().start().to_usize());
            return Err(SourceError {
                code: "semantic-syntax",
                message: format!("Python semantic syntax invalid: {error}"),
                line,
                column,
            });
        }
        Ok(Self {
            source,
            parsed,
            scopes,
            node_count: budget.nodes,
            target,
            metrics: std::sync::OnceLock::new(),
        })
    }
    pub fn body(&self) -> &[ruff_python_ast::Stmt] {
        self.parsed
            .syntax()
            .as_module()
            .map_or(&[], |module| module.body.as_slice())
    }
}
fn lexical_budget(source: &str) -> Result<(), SourceError> {
    let mut lexer = lexer::lex(source, Mode::Module);
    let mut nesting = 0usize;
    let mut indentation = 0usize;
    let mut logical_tokens = 0usize;
    for _ in 0..1_000_000usize {
        let token = lexer.next_token();
        match token {
            TokenKind::EndOfFile => return Ok(()),
            TokenKind::Lpar | TokenKind::Lsqb | TokenKind::Lbrace => nesting += 1,
            TokenKind::Rpar | TokenKind::Rsqb | TokenKind::Rbrace => {
                nesting = nesting.saturating_sub(1)
            }
            TokenKind::Indent => indentation += 1,
            TokenKind::Dedent => indentation = indentation.saturating_sub(1),
            TokenKind::Newline => logical_tokens = 0,
            _ => {}
        }
        logical_tokens += 1;
        if nesting > 200 || indentation > 100 || logical_tokens > 8192 {
            return Err(SourceError::new(
                "syntax-resource-limit",
                "Python source exceeds delimiter, indentation or logical-statement analysis bounds",
            ));
        }
    }
    Err(SourceError::new(
        "token-limit",
        "Python source exceeds one million lexical tokens",
    ))
}
#[derive(Default)]
struct AstBudget {
    depth: usize,
    nodes: usize,
    exceeded: bool,
}
impl<'a> SourceOrderVisitor<'a> for AstBudget {
    fn enter_node(&mut self, _: AnyNodeRef<'a>) -> TraversalSignal {
        self.depth += 1;
        self.nodes += 1;
        if self.depth > 512 || self.nodes > MAX_AST_NODES {
            self.exceeded = true;
            TraversalSignal::Skip
        } else {
            TraversalSignal::Traverse
        }
    }
    fn leave_node(&mut self, _: AnyNodeRef<'a>) {
        self.depth = self.depth.saturating_sub(1);
    }
}
