//! CPython `ast.walk` order of function definitions, for the frozen legacy complexity view.
//!
//! `ast.walk` is breadth first, so a definition is visited after every node of a shallower AST
//! depth. Two definitions at the same depth are visited in source order (their first differing
//! ancestors are disjoint siblings or cousins, which `iter_child_nodes` yields in source order),
//! so the walk order is the sort by `(AST depth, source position)`. The depth counts CPython
//! nodes: a statement of a module body is at depth 1; a body nests one below its statement; an
//! `except` body and a `case` body nest two below theirs (the handler/`match_case` node); each
//! `elif` is an `If` nested in the previous `If.orelse`.
use ruff_python_ast::{self as ast, Stmt};
use ruff_python_parser::{Mode, ParseOptions, parse_unchecked};
use ruff_text_size::Ranged;

struct Walk<'a> {
    line_starts: Vec<usize>,
    text: &'a str,
    found: Vec<(u32, u32, usize)>,
}

impl Walk<'_> {
    fn line(&self, offset: usize) -> u32 {
        (self.line_starts.partition_point(|start| *start <= offset)) as u32
    }
    fn body(&mut self, body: &[Stmt], depth: u32) {
        for statement in body {
            self.statement(statement, depth);
        }
    }
    fn statement(&mut self, statement: &Stmt, depth: u32) {
        match statement {
            Stmt::FunctionDef(node) => {
                // CPython `lineno` is the `def` (or `async`) keyword after the decorators.
                let start = node
                    .decorator_list
                    .last()
                    .map_or(node.range().start().to_usize(), |decorator| {
                        decorator.range().end().to_usize()
                    });
                let keyword = self.text[start..]
                    .find(if node.is_async { "async" } else { "def" })
                    .map_or(start, |position| start + position);
                self.found.push((depth, self.line(keyword), keyword));
                self.body(&node.body, depth + 1);
            }
            Stmt::ClassDef(node) => self.body(&node.body, depth + 1),
            Stmt::If(node) => {
                self.body(&node.body, depth + 1);
                for (index, clause) in node.elif_else_clauses.iter().enumerate() {
                    let nested = index as u32;
                    if clause.test.is_some() {
                        self.body(&clause.body, depth + 2 + nested);
                    } else {
                        self.body(&clause.body, depth + 1 + nested);
                    }
                }
            }
            Stmt::For(ast::StmtFor { body, orelse, .. })
            | Stmt::While(ast::StmtWhile { body, orelse, .. }) => {
                self.body(body, depth + 1);
                self.body(orelse, depth + 1);
            }
            Stmt::With(node) => self.body(&node.body, depth + 1),
            Stmt::Try(node) => {
                self.body(&node.body, depth + 1);
                for handler in &node.handlers {
                    let ast::ExceptHandler::ExceptHandler(handler) = handler;
                    self.body(&handler.body, depth + 2);
                }
                self.body(&node.orelse, depth + 1);
                self.body(&node.finalbody, depth + 1);
            }
            Stmt::Match(node) => {
                for case in &node.cases {
                    self.body(&case.body, depth + 2);
                }
            }
            _ => {}
        }
    }
}

/// The `def` line of every function definition in `ast.walk` order, or `None` when the source
/// does not parse (frozen skips such a file).
pub fn python_function_walk_order(text: &str) -> Option<Vec<u32>> {
    let parsed = parse_unchecked(text, ParseOptions::from(Mode::Module));
    if !parsed.errors().is_empty() {
        return None;
    }
    let bytes = text.as_bytes();
    let mut line_starts = vec![0];
    for (index, byte) in bytes.iter().enumerate() {
        if *byte == b'\n' || (*byte == b'\r' && bytes.get(index + 1) != Some(&b'\n')) {
            line_starts.push(index + 1);
        }
    }
    let mut walk = Walk {
        line_starts,
        text,
        found: vec![],
    };
    let ast::Mod::Module(module) = parsed.syntax() else {
        return None;
    };
    walk.body(&module.body, 1);
    walk.found
        .sort_by_key(|(depth, _, position)| (*depth, *position));
    Some(walk.found.into_iter().map(|(_, line, _)| line).collect())
}

#[cfg(test)]
mod tests {
    use super::python_function_walk_order;

    #[test]
    fn breadth_first_by_cpython_depth_then_source_order() {
        let source = "import functools\n\n\n@functools.cache\ndef deco():\n    return 1\n\n\nif True:\n    def inif():\n        pass\n\ntry:\n    pass\nexcept Exception:\n    def inexc():\n        pass\n\n\nclass K:\n    @staticmethod\n    async def am():\n        return 2\n\n\ndef last():\n    def inner():\n        pass\n    return 3\n";
        // Frozen `ast.walk` (CPython 3.12): deco, last (depth 1); inif, am, inner (depth 2,
        // source order); inexc (depth 3: Try -> ExceptHandler -> body).
        assert_eq!(
            python_function_walk_order(source),
            Some(vec![5, 26, 10, 22, 27, 16])
        );
        let elif = "if a:\n    pass\nelif b:\n    def one():\n        pass\nelse:\n    def two():\n        pass\ndef top():\n    pass\n";
        // `elif` is an If in orelse: one() at depth 3, two() at depth 3 (else of that If).
        assert_eq!(python_function_walk_order(elif), Some(vec![9, 4, 7]));
        let blocks = "match x:\n    case 1:\n        def m1():\n            pass\nwith o:\n    def w():\n        pass\nfor i in r:\n    pass\nelse:\n    def fe():\n        pass\ndef z(): pass\n";
        // A `case` body is two below `match` (match_case); with/for-else bodies one below.
        assert_eq!(python_function_walk_order(blocks), Some(vec![13, 6, 11, 3]));
        assert_eq!(python_function_walk_order("def broken(:\n"), None);
    }
}
