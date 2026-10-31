//! A native CPython AST projection for the historical semantic-shape contract.
//! Locations are metadata; canonical field order follows the CPython 3.10 AST.
use crate::parser::ParsedFile;
use ruff_python_ast::{
    self as ast, Expr, InterpolatedStringElement, Pattern, Stmt, token::TokenKind,
};
use ruff_text_size::{Ranged, TextRange, TextSize};
use std::{
    cell::RefCell,
    collections::{BTreeMap, BTreeSet},
    sync::atomic::AtomicBool,
};

#[derive(Clone, Debug)]
pub enum Value {
    Node(Node),
    List(Vec<Value>),
    String(String),
    Utf16(Vec<u16>),
    Number(String),
    Bool(bool),
    Null,
}
#[derive(Clone, Debug)]
pub struct Node {
    pub kind: String,
    pub fields: Vec<(&'static str, Value)>,
    pub span: Option<(u32, u32)>,
}
impl Node {
    pub fn get(&self, name: &str) -> Option<&Value> {
        self.fields
            .iter()
            .find(|(field, _)| *field == name)
            .map(|(_, value)| value)
    }
    pub fn named(&self) -> bool {
        matches!(
            self.kind.as_str(),
            "FunctionDef" | "AsyncFunctionDef" | "ClassDef"
        )
    }
    pub fn name(&self) -> Option<&str> {
        self.get("name").and_then(Value::string)
    }
}
impl Value {
    pub fn string(&self) -> Option<&str> {
        if let Self::String(value) = self {
            Some(value)
        } else {
            None
        }
    }
}
fn node(kind: impl Into<String>, fields: Vec<(&'static str, Value)>) -> Value {
    Value::Node(Node {
        kind: kind.into(),
        fields,
        span: None,
    })
}
fn string(value: impl ToString) -> Value {
    Value::String(value.to_string())
}
fn optional_string(value: Option<&ast::Identifier>) -> Value {
    value.map_or(Value::Null, string)
}
fn empty(kind: impl Into<String>) -> Value {
    node(kind, vec![])
}
fn context(value: ast::ExprContext) -> Value {
    empty(format!("{value:?}"))
}
fn constant(kind: &str, value: Value) -> Value {
    node(
        "Constant",
        vec![("value", Value::List(vec![string(kind), value]))],
    )
}
fn restored_units(content: &str, decoded: &str, raw: bool, braces: bool) -> Option<Vec<u16>> {
    let bytes = content.as_bytes();
    let mut cursor = 0;
    let mut index = 0;
    let mut surrogates = BTreeMap::new();
    while cursor < bytes.len() {
        if braces
            && matches!(bytes[cursor], b'{' | b'}')
            && bytes.get(cursor + 1) == Some(&bytes[cursor])
        {
            cursor += 2;
            index += 1;
            continue;
        }
        if bytes[cursor] == b'\\' && !raw && cursor + 1 < bytes.len() {
            cursor += 1;
            let escaped = bytes[cursor];
            cursor += 1;
            match escaped {
                b'u' | b'U' => {
                    let size = if escaped == b'u' { 4 } else { 8 };
                    let digits = content.get(cursor..cursor + size)?;
                    let code = u32::from_str_radix(digits, 16).ok()?;
                    if (0xd800..=0xdfff).contains(&code) {
                        surrogates.insert(index, code as u16);
                    }
                    cursor += size;
                    index += 1;
                }
                b'x' => {
                    cursor += 2;
                    index += 1;
                }
                b'N' if bytes.get(cursor) == Some(&b'{') => {
                    cursor += content[cursor..].find('}')? + 1;
                    index += 1;
                }
                b'0'..=b'7' => {
                    let mut count = 1;
                    while count < 3
                        && bytes
                            .get(cursor)
                            .is_some_and(|byte| matches!(byte, b'0'..=b'7'))
                    {
                        count += 1;
                        cursor += 1;
                    }
                    index += 1;
                }
                b'\n' => {}
                b'\r' => {
                    if bytes.get(cursor) == Some(&b'\n') {
                        cursor += 1;
                    }
                }
                b'a' | b'b' | b'f' | b'n' | b'r' | b't' | b'v' | b'\\' | b'\'' | b'"' => index += 1,
                _ => {
                    index += 2;
                    if !escaped.is_ascii() {
                        let character = content[cursor - 1..].chars().next()?;
                        cursor += character.len_utf8() - 1;
                    }
                }
            }
        } else {
            let character = content[cursor..].chars().next()?;
            cursor += character.len_utf8();
            if character == '\r' && bytes.get(cursor) == Some(&b'\n') {
                cursor += 1;
            }
            index += 1;
        }
    }
    let mut result = vec![];
    for (index, character) in decoded.chars().enumerate() {
        if let Some(surrogate) = surrogates.get(&index) {
            if character != '\u{fffd}' {
                return None;
            }
            result.push(*surrogate);
        } else {
            let mut units = [0; 2];
            result.extend_from_slice(character.encode_utf16(&mut units));
        }
    }
    if surrogates
        .keys()
        .next_back()
        .is_some_and(|index| *index >= decoded.chars().count())
    {
        return None;
    }
    Some(result)
}
fn string_units(raw_literal: &str, decoded: &str) -> Option<Vec<u16>> {
    let quote = raw_literal.find(['\'', '"'])?;
    let delimiter =
        if raw_literal[quote..].starts_with("\"\"\"") || raw_literal[quote..].starts_with("'''") {
            3
        } else {
            1
        };
    restored_units(
        &raw_literal[quote + delimiter..raw_literal.len() - delimiter],
        decoded,
        raw_literal[..quote].to_ascii_lowercase().contains('r'),
        false,
    )
}
pub fn float_repr(value: f64) -> String {
    if value.is_infinite() {
        return if value.is_sign_negative() {
            "-inf"
        } else {
            "inf"
        }
        .into();
    }
    let raw = format!("{value:?}");
    if let Some((mantissa, exponent)) = raw.split_once('e') {
        let exponent: i32 = exponent.parse().expect("native float exponent");
        format!("{mantissa}e{exponent:+03}")
    } else {
        raw
    }
}
pub struct Projector<'a> {
    pub file: &'a ParsedFile,
    comments: BTreeMap<u32, (TextSize, String)>,
    used_comments: RefCell<BTreeSet<TextSize>>,
    cancellation: Option<&'a AtomicBool>,
}
impl<'a> Projector<'a> {
    pub fn new(file: &'a ParsedFile, cancellation: Option<&'a AtomicBool>) -> Self {
        let comments = file
            .parsed
            .tokens()
            .iter()
            .filter(|token| token.kind() == TokenKind::Comment)
            .filter_map(|token| {
                let raw = &file.source.text
                    [token.range().start().to_usize()..token.range().end().to_usize()];
                let comment = raw
                    .strip_prefix('#')?
                    .trim_start()
                    .strip_prefix("type:")?
                    .trim_start();
                if comment.starts_with("ignore") {
                    return None;
                }
                Some((
                    file.source.location(token.range().start().to_usize()).0,
                    (token.range().start(), comment.to_owned()),
                ))
            })
            .collect();
        Self {
            file,
            comments,
            used_comments: RefCell::new(BTreeSet::new()),
            cancellation,
        }
    }
    pub fn type_comments_complete(&self) -> bool {
        self.used_comments.borrow().len() == self.comments.len()
    }
    fn opt(&self, value: Option<&Expr>) -> Value {
        value.map_or(Value::Null, |value| self.expr(value))
    }
    fn exprs<'b>(&self, values: impl IntoIterator<Item = &'b Expr>) -> Value {
        Value::List(values.into_iter().map(|value| self.expr(value)).collect())
    }
    fn body(&self, values: &[Stmt]) -> Value {
        Value::List(values.iter().map(|value| self.stmt(value)).collect())
    }
    fn use_comment(&self, comment: Option<&(TextSize, String)>) -> Value {
        comment.map_or(Value::Null, |(offset, value)| {
            self.used_comments.borrow_mut().insert(*offset);
            string(value)
        })
    }
    fn type_comment(&self, range: TextRange) -> Value {
        let line = self.file.source.location(range.end().to_usize()).0;
        let comment = self.comments.get(&line).filter(|(offset, _)| {
            if *offset < range.end() {
                return false;
            }
            let start = self
                .file
                .parsed
                .tokens()
                .partition_point(|token| token.range().start() < range.end());
            !self.file.parsed.tokens()[start..]
                .iter()
                .take_while(|token| token.range().start() < *offset)
                .any(|token| {
                    !matches!(
                        token.kind(),
                        TokenKind::Comment | TokenKind::NonLogicalNewline | TokenKind::Newline
                    )
                })
        });
        self.use_comment(comment)
    }
    fn header_comment(&self, start: TextSize, body: &[Stmt], function: bool) -> Value {
        let Some(first) = body.first() else {
            return Value::Null;
        };
        let finish = first.range().start();
        let startline = self.file.source.location(start.to_usize()).0;
        let endline = self.file.source.location(finish.to_usize()).0;
        let tokens = self.file.parsed.tokens();
        let from = tokens.partition_point(|token| token.range().start() < start);
        let to = tokens.partition_point(|token| token.range().start() < finish);
        let colon_line = tokens[from..to]
            .iter()
            .rev()
            .find(|token| token.kind() == TokenKind::Colon)
            .map(|token| {
                self.file
                    .source
                    .location(token.range().start().to_usize())
                    .0
            });
        let comment = self
            .comments
            .range(startline..=endline)
            .map(|(_, comment)| comment)
            .find(|(offset, _)| {
                *offset >= start
                    && *offset < finish
                    && (function
                        || colon_line == Some(self.file.source.location(offset.to_usize()).0))
            });
        self.use_comment(comment)
    }
    fn parameter(&self, value: &ast::Parameter, comment: Option<&(TextSize, String)>) -> Value {
        node(
            "arg",
            vec![
                ("arg", string(&value.name)),
                ("annotation", self.opt(value.annotation.as_deref())),
                ("type_comment", self.use_comment(comment)),
            ],
        )
    }
    fn parameters(&self, value: Option<&ast::Parameters>) -> Value {
        let Some(value) = value else {
            return node(
                "arguments",
                vec![
                    ("posonlyargs", Value::List(vec![])),
                    ("args", Value::List(vec![])),
                    ("vararg", Value::Null),
                    ("kwonlyargs", Value::List(vec![])),
                    ("kw_defaults", Value::List(vec![])),
                    ("kwarg", Value::Null),
                    ("defaults", Value::List(vec![])),
                ],
            );
        };
        let mut ordered: Vec<_> = value
            .posonlyargs
            .iter()
            .chain(&value.args)
            .chain(&value.kwonlyargs)
            .map(|arg| &arg.parameter)
            .chain(value.vararg.as_deref())
            .chain(value.kwarg.as_deref())
            .collect();
        ordered.sort_by_key(|arg| arg.range().end());
        let startline = self
            .file
            .source
            .location(value.range().start().to_usize())
            .0;
        let endline = self.file.source.location(value.range().end().to_usize()).0;
        let mut parameter_comments = BTreeMap::new();
        for (&line, comment) in self.comments.range(startline..=endline) {
            if comment.0 < value.range().start() || comment.0 >= value.range().end() {
                continue;
            }
            let before = ordered.partition_point(|arg| arg.range().end() <= comment.0);
            if let Some(index) = before.checked_sub(1) {
                let arg = ordered[index];
                if self.file.source.location(arg.range().end().to_usize()).0 == line {
                    parameter_comments.insert(arg.range().start(), comment);
                }
            }
        }
        let parameter = |arg: &ast::Parameter| {
            self.parameter(arg, parameter_comments.get(&arg.range().start()).copied())
        };
        node(
            "arguments",
            vec![
                (
                    "posonlyargs",
                    Value::List(
                        value
                            .posonlyargs
                            .iter()
                            .map(|arg| parameter(&arg.parameter))
                            .collect(),
                    ),
                ),
                (
                    "args",
                    Value::List(
                        value
                            .args
                            .iter()
                            .map(|arg| parameter(&arg.parameter))
                            .collect(),
                    ),
                ),
                (
                    "vararg",
                    value.vararg.as_deref().map_or(Value::Null, parameter),
                ),
                (
                    "kwonlyargs",
                    Value::List(
                        value
                            .kwonlyargs
                            .iter()
                            .map(|arg| parameter(&arg.parameter))
                            .collect(),
                    ),
                ),
                (
                    "kw_defaults",
                    Value::List(
                        value
                            .kwonlyargs
                            .iter()
                            .map(|arg| self.opt(arg.default.as_deref()))
                            .collect(),
                    ),
                ),
                (
                    "kwarg",
                    value.kwarg.as_deref().map_or(Value::Null, parameter),
                ),
                (
                    "defaults",
                    self.exprs(
                        value
                            .posonlyargs
                            .iter()
                            .chain(&value.args)
                            .filter_map(|arg| arg.default.as_deref()),
                    ),
                ),
            ],
        )
    }
    fn keywords(&self, value: &[ast::Keyword]) -> Value {
        Value::List(
            value
                .iter()
                .map(|keyword| {
                    node(
                        "keyword",
                        vec![
                            ("arg", optional_string(keyword.arg.as_ref())),
                            ("value", self.expr(&keyword.value)),
                        ],
                    )
                })
                .collect(),
        )
    }
    fn aliases(&self, aliases: &[ast::Alias]) -> Value {
        Value::List(
            aliases
                .iter()
                .map(|alias| {
                    node(
                        "alias",
                        vec![
                            ("name", string(&alias.name)),
                            ("asname", optional_string(alias.asname.as_ref())),
                        ],
                    )
                })
                .collect(),
        )
    }
    fn if_tail(&self, clauses: &[ast::ElifElseClause]) -> Value {
        let Some((first, rest)) = clauses.split_first() else {
            return Value::List(vec![]);
        };
        if let Some(test) = &first.test {
            Value::List(vec![node(
                "If",
                vec![
                    ("test", self.expr(test)),
                    ("body", self.body(&first.body)),
                    ("orelse", self.if_tail(rest)),
                ],
            )])
        } else {
            self.body(&first.body)
        }
    }
    pub fn module(&self) -> Node {
        let ignores = self
            .file
            .parsed
            .tokens()
            .iter()
            .filter(|token| token.kind() == TokenKind::Comment)
            .filter_map(|token| {
                let raw = &self.file.source.text
                    [token.range().start().to_usize()..token.range().end().to_usize()];
                let value = raw
                    .strip_prefix('#')?
                    .trim_start()
                    .strip_prefix("type:")?
                    .trim_start()
                    .strip_prefix("ignore")?;
                Some(node(
                    "TypeIgnore",
                    vec![
                        (
                            "lineno",
                            Value::Number(
                                self.file
                                    .source
                                    .location(token.range().start().to_usize())
                                    .0
                                    .to_string(),
                            ),
                        ),
                        ("tag", string(value)),
                    ],
                ))
            })
            .collect();
        Node {
            kind: "Module".into(),
            fields: vec![
                ("body", self.body(self.file.body())),
                ("type_ignores", Value::List(ignores)),
            ],
            span: None,
        }
    }
    pub fn stmt(&self, value: &Stmt) -> Value {
        if crate::cancellation_requested(self.cancellation) {
            return empty("UnsupportedCancelled");
        }
        let result = match value {
            Stmt::FunctionDef(value) => node(
                if value.is_async {
                    "AsyncFunctionDef"
                } else {
                    "FunctionDef"
                },
                vec![
                    ("name", string(&value.name)),
                    ("args", self.parameters(Some(&value.parameters))),
                    ("body", self.body(&value.body)),
                    (
                        "decorator_list",
                        Value::List(
                            value
                                .decorator_list
                                .iter()
                                .map(|decorator| self.expr(&decorator.expression))
                                .collect(),
                        ),
                    ),
                    ("returns", self.opt(value.returns.as_deref())),
                    (
                        "type_comment",
                        self.header_comment(value.parameters.range().end(), &value.body, true),
                    ),
                ],
            ),
            Stmt::ClassDef(value) => node(
                "ClassDef",
                vec![
                    ("name", string(&value.name)),
                    (
                        "bases",
                        value
                            .arguments
                            .as_ref()
                            .map_or_else(|| Value::List(vec![]), |args| self.exprs(&args.args)),
                    ),
                    (
                        "keywords",
                        value.arguments.as_ref().map_or_else(
                            || Value::List(vec![]),
                            |args| self.keywords(&args.keywords),
                        ),
                    ),
                    ("body", self.body(&value.body)),
                    (
                        "decorator_list",
                        Value::List(
                            value
                                .decorator_list
                                .iter()
                                .map(|decorator| self.expr(&decorator.expression))
                                .collect(),
                        ),
                    ),
                ],
            ),
            Stmt::Return(value) => {
                node("Return", vec![("value", self.opt(value.value.as_deref()))])
            }
            Stmt::Delete(value) => node("Delete", vec![("targets", self.exprs(&value.targets))]),
            Stmt::Assign(value) => node(
                "Assign",
                vec![
                    ("targets", self.exprs(&value.targets)),
                    ("value", self.expr(&value.value)),
                    ("type_comment", self.type_comment(value.range())),
                ],
            ),
            Stmt::AugAssign(value) => node(
                "AugAssign",
                vec![
                    ("target", self.expr(&value.target)),
                    ("op", empty(format!("{:?}", value.op))),
                    ("value", self.expr(&value.value)),
                ],
            ),
            Stmt::AnnAssign(value) => node(
                "AnnAssign",
                vec![
                    ("target", self.expr(&value.target)),
                    ("annotation", self.expr(&value.annotation)),
                    ("value", self.opt(value.value.as_deref())),
                    ("simple", Value::Number(u8::from(value.simple).to_string())),
                ],
            ),
            Stmt::For(value) => node(
                if value.is_async { "AsyncFor" } else { "For" },
                vec![
                    ("target", self.expr(&value.target)),
                    ("iter", self.expr(&value.iter)),
                    ("body", self.body(&value.body)),
                    ("orelse", self.body(&value.orelse)),
                    (
                        "type_comment",
                        self.header_comment(value.range().start(), &value.body, false),
                    ),
                ],
            ),
            Stmt::While(value) => node(
                "While",
                vec![
                    ("test", self.expr(&value.test)),
                    ("body", self.body(&value.body)),
                    ("orelse", self.body(&value.orelse)),
                ],
            ),
            Stmt::If(value) => node(
                "If",
                vec![
                    ("test", self.expr(&value.test)),
                    ("body", self.body(&value.body)),
                    ("orelse", self.if_tail(&value.elif_else_clauses)),
                ],
            ),
            Stmt::With(value) => node(
                if value.is_async { "AsyncWith" } else { "With" },
                vec![
                    (
                        "items",
                        Value::List(
                            value
                                .items
                                .iter()
                                .map(|item| {
                                    node(
                                        "withitem",
                                        vec![
                                            ("context_expr", self.expr(&item.context_expr)),
                                            (
                                                "optional_vars",
                                                self.opt(item.optional_vars.as_deref()),
                                            ),
                                        ],
                                    )
                                })
                                .collect(),
                        ),
                    ),
                    ("body", self.body(&value.body)),
                    (
                        "type_comment",
                        self.header_comment(value.range().start(), &value.body, false),
                    ),
                ],
            ),
            Stmt::Match(value) => node(
                "Match",
                vec![
                    ("subject", self.expr(&value.subject)),
                    (
                        "cases",
                        Value::List(
                            value
                                .cases
                                .iter()
                                .map(|case| {
                                    node(
                                        "match_case",
                                        vec![
                                            ("pattern", self.pattern(&case.pattern)),
                                            ("guard", self.opt(case.guard.as_deref())),
                                            ("body", self.body(&case.body)),
                                        ],
                                    )
                                })
                                .collect(),
                        ),
                    ),
                ],
            ),
            Stmt::Raise(value) => node(
                "Raise",
                vec![
                    ("exc", self.opt(value.exc.as_deref())),
                    ("cause", self.opt(value.cause.as_deref())),
                ],
            ),
            Stmt::Try(value) => node(
                if value.is_star { "TryStar" } else { "Try" },
                vec![
                    ("body", self.body(&value.body)),
                    (
                        "handlers",
                        Value::List(
                            value
                                .handlers
                                .iter()
                                .map(|handler| {
                                    let ast::ExceptHandler::ExceptHandler(handler) = handler;
                                    node(
                                        "ExceptHandler",
                                        vec![
                                            ("type", self.opt(handler.type_.as_deref())),
                                            ("name", optional_string(handler.name.as_ref())),
                                            ("body", self.body(&handler.body)),
                                        ],
                                    )
                                })
                                .collect(),
                        ),
                    ),
                    ("orelse", self.body(&value.orelse)),
                    ("finalbody", self.body(&value.finalbody)),
                ],
            ),
            Stmt::Assert(value) => node(
                "Assert",
                vec![
                    ("test", self.expr(&value.test)),
                    ("msg", self.opt(value.msg.as_deref())),
                ],
            ),
            Stmt::Import(value) => node("Import", vec![("names", self.aliases(&value.names))]),
            Stmt::ImportFrom(value) => node(
                "ImportFrom",
                vec![
                    ("module", optional_string(value.module.as_ref())),
                    ("names", self.aliases(&value.names)),
                    ("level", Value::Number(value.level.to_string())),
                ],
            ),
            Stmt::Global(value) => node(
                "Global",
                vec![(
                    "names",
                    Value::List(value.names.iter().map(string).collect()),
                )],
            ),
            Stmt::Nonlocal(value) => node(
                "Nonlocal",
                vec![(
                    "names",
                    Value::List(value.names.iter().map(string).collect()),
                )],
            ),
            Stmt::Expr(value) => node("Expr", vec![("value", self.expr(&value.value))]),
            Stmt::Pass(_) => empty("Pass"),
            Stmt::Break(_) => empty("Break"),
            Stmt::Continue(_) => empty("Continue"),
            Stmt::TypeAlias(_) => empty("UnsupportedTypeAlias"),
            Stmt::IpyEscapeCommand(_) => empty("UnsupportedIpyEscapeCommand"),
        };
        let Value::Node(mut result) = result else {
            unreachable!("statement projection is a node")
        };
        let span = self
            .file
            .source
            .span(crate::ast_util::statement_range(self.file, value));
        let start = match value {
            Stmt::FunctionDef(function) => function
                .decorator_list
                .iter()
                .map(|decorator| {
                    self.file
                        .source
                        .location(decorator.expression.range().start().to_usize())
                        .0
                })
                .fold(span.start_line, u32::min),
            Stmt::ClassDef(class) => class
                .decorator_list
                .iter()
                .map(|decorator| {
                    self.file
                        .source
                        .location(decorator.expression.range().start().to_usize())
                        .0
                })
                .fold(span.start_line, u32::min),
            _ => span.start_line,
        };
        result.span = Some((start, span.end_line.unwrap_or(start)));
        Value::Node(result)
    }
    fn generators(&self, values: &[ast::Comprehension]) -> Value {
        Value::List(
            values
                .iter()
                .map(|value| {
                    node(
                        "comprehension",
                        vec![
                            ("target", self.expr(&value.target)),
                            ("iter", self.expr(&value.iter)),
                            ("ifs", self.exprs(&value.ifs)),
                            (
                                "is_async",
                                Value::Number(u8::from(value.is_async).to_string()),
                            ),
                        ],
                    )
                })
                .collect(),
        )
    }
    fn interpolated(&self, elements: impl IntoIterator<Item = Value>) -> Value {
        node(
            "JoinedStr",
            vec![("values", Value::List(elements.into_iter().collect()))],
        )
    }
    fn fstring_elements<'b>(
        &self,
        elements: impl IntoIterator<Item = &'b InterpolatedStringElement>,
        raw: bool,
    ) -> Vec<Value> {
        let mut result = vec![];
        for element in elements {
            match element {
                InterpolatedStringElement::Literal(literal) => {
                    let source = &self.file.source.text
                        [literal.range().start().to_usize()..literal.range().end().to_usize()];
                    let Some(units) = restored_units(source, &literal.value, raw, true) else {
                        result.push(empty("UnsupportedStringLiteral"));
                        continue;
                    };
                    Self::push_text(
                        &mut result,
                        String::from_utf16(&units)
                            .map_or_else(|_| Value::Utf16(units), Value::String),
                    );
                }
                InterpolatedStringElement::Interpolation(value) => {
                    if let Some(debug) = &value.debug_text {
                        Self::push_literal(&mut result, debug.as_str());
                    }
                    let conversion = if value.debug_text.is_some()
                        && value.conversion == ast::ConversionFlag::None
                        && value.format_spec.is_none()
                    {
                        b'r' as i32
                    } else {
                        value.conversion as i32
                    };
                    result.push(node(
                        "FormattedValue",
                        vec![
                            ("value", self.expr(&value.expression)),
                            ("conversion", Value::Number(conversion.to_string())),
                            (
                                "format_spec",
                                value.format_spec.as_ref().map_or(Value::Null, |format| {
                                    self.interpolated(
                                        self.fstring_elements(format.elements.iter(), raw),
                                    )
                                }),
                            ),
                        ],
                    ));
                }
            }
        }
        result
    }
    fn push_literal(values: &mut Vec<Value>, text: &str) {
        Self::push_text(values, string(text));
    }
    fn push_text(values: &mut Vec<Value>, text: Value) {
        if matches!(&text,Value::String(text) if text.is_empty())
            || matches!(&text,Value::Utf16(text) if text.is_empty())
        {
            return;
        }
        if let Some(Value::Node(last)) = values.last_mut()
            && last.kind == "Constant"
            && let Some((_, Value::List(literal))) = last.fields.first_mut()
            && let Some(value) = literal.get_mut(1)
        {
            match (value, &text) {
                (Value::String(value), Value::String(text)) => value.push_str(text),
                (Value::Utf16(value), Value::String(text)) => value.extend(text.encode_utf16()),
                (Value::Utf16(value), Value::Utf16(text)) => value.extend_from_slice(text),
                (value @ Value::String(_), Value::Utf16(text)) => {
                    let Value::String(previous) = value else {
                        unreachable!()
                    };
                    let mut units: Vec<_> = previous.encode_utf16().collect();
                    units.extend_from_slice(text);
                    *value = Value::Utf16(units);
                }
                _ => unreachable!("joined string literal is text"),
            }
            return;
        }
        values.push(constant("str", text));
    }
    pub fn expr(&self, value: &Expr) -> Value {
        if crate::cancellation_requested(self.cancellation) {
            return empty("UnsupportedCancelled");
        }
        match value {
            Expr::BoolOp(value) => node(
                "BoolOp",
                vec![
                    ("op", empty(format!("{:?}", value.op))),
                    ("values", self.exprs(&value.values)),
                ],
            ),
            Expr::Named(value) => node(
                "NamedExpr",
                vec![
                    ("target", self.expr(&value.target)),
                    ("value", self.expr(&value.value)),
                ],
            ),
            Expr::BinOp(value) => node(
                "BinOp",
                vec![
                    ("left", self.expr(&value.left)),
                    ("op", empty(format!("{:?}", value.op))),
                    ("right", self.expr(&value.right)),
                ],
            ),
            Expr::UnaryOp(value) => node(
                "UnaryOp",
                vec![
                    ("op", empty(format!("{:?}", value.op))),
                    ("operand", self.expr(&value.operand)),
                ],
            ),
            Expr::Lambda(value) => node(
                "Lambda",
                vec![
                    ("args", self.parameters(value.parameters.as_deref())),
                    ("body", self.expr(&value.body)),
                ],
            ),
            Expr::If(value) => node(
                "IfExp",
                vec![
                    ("test", self.expr(&value.test)),
                    ("body", self.expr(&value.body)),
                    ("orelse", self.expr(&value.orelse)),
                ],
            ),
            Expr::Dict(value) => node(
                "Dict",
                vec![
                    (
                        "keys",
                        Value::List(
                            value
                                .items
                                .iter()
                                .map(|item| self.opt(item.key.as_ref()))
                                .collect(),
                        ),
                    ),
                    (
                        "values",
                        Value::List(
                            value
                                .items
                                .iter()
                                .map(|item| self.expr(&item.value))
                                .collect(),
                        ),
                    ),
                ],
            ),
            Expr::Set(value) => node("Set", vec![("elts", self.exprs(&value.elts))]),
            Expr::ListComp(value) => node(
                "ListComp",
                vec![
                    ("elt", self.expr(&value.elt)),
                    ("generators", self.generators(&value.generators)),
                ],
            ),
            Expr::SetComp(value) => node(
                "SetComp",
                vec![
                    ("elt", self.expr(&value.elt)),
                    ("generators", self.generators(&value.generators)),
                ],
            ),
            Expr::DictComp(value) => node(
                "DictComp",
                vec![
                    ("key", self.opt(value.key.as_deref())),
                    ("value", self.expr(&value.value)),
                    ("generators", self.generators(&value.generators)),
                ],
            ),
            Expr::Generator(value) => node(
                "GeneratorExp",
                vec![
                    ("elt", self.expr(&value.elt)),
                    ("generators", self.generators(&value.generators)),
                ],
            ),
            Expr::Await(value) => node("Await", vec![("value", self.expr(&value.value))]),
            Expr::Yield(value) => node("Yield", vec![("value", self.opt(value.value.as_deref()))]),
            Expr::YieldFrom(value) => node("YieldFrom", vec![("value", self.expr(&value.value))]),
            Expr::Compare(value) => node(
                "Compare",
                vec![
                    ("left", self.expr(&value.left)),
                    (
                        "ops",
                        Value::List(
                            value
                                .ops
                                .iter()
                                .map(|op| empty(format!("{op:?}")))
                                .collect(),
                        ),
                    ),
                    ("comparators", self.exprs(value.comparators.iter())),
                ],
            ),
            Expr::Call(value) => node(
                "Call",
                vec![
                    ("func", self.expr(&value.func)),
                    ("args", self.exprs(&value.arguments.args)),
                    ("keywords", self.keywords(&value.arguments.keywords)),
                ],
            ),
            Expr::FString(value) => {
                let mut elements = vec![];
                for part in value.value.iter() {
                    match part {
                        ast::FStringPart::Literal(literal) => {
                            let raw = &self.file.source.text[literal.range().start().to_usize()
                                ..literal.range().end().to_usize()];
                            let Some(units) = string_units(raw, &literal.value) else {
                                return empty("UnsupportedStringLiteral");
                            };
                            Self::push_text(
                                &mut elements,
                                String::from_utf16(&units)
                                    .map_or_else(|_| Value::Utf16(units), Value::String),
                            );
                        }
                        ast::FStringPart::FString(fstring) => {
                            for element in self.fstring_elements(
                                fstring.elements.iter(),
                                self.file.source.text[fstring.range().start().to_usize()
                                    ..fstring.range().end().to_usize()]
                                    .chars()
                                    .take_while(|character| character.is_ascii_alphabetic())
                                    .any(|character| matches!(character, 'r' | 'R')),
                            ) {
                                if let Value::Node(literal) = &element
                                    && literal.kind == "Constant"
                                    && let Some(Value::List(value)) = literal.get("value")
                                    && let Some(text) = value.get(1)
                                {
                                    Self::push_text(&mut elements, text.clone());
                                    continue;
                                }
                                elements.push(element);
                            }
                        }
                    }
                }
                self.interpolated(elements)
            }
            Expr::StringLiteral(value) => {
                let mut units = vec![];
                for literal in value.value.iter() {
                    let raw = &self.file.source.text
                        [literal.range().start().to_usize()..literal.range().end().to_usize()];
                    let Some(part) = string_units(raw, &literal.value) else {
                        return empty("UnsupportedStringLiteral");
                    };
                    units.extend(part);
                }
                let text =
                    String::from_utf16(&units).map_or_else(|_| Value::Utf16(units), Value::String);
                let mut result = constant("str", text);
                if self
                    .file
                    .source
                    .text
                    .as_bytes()
                    .get(value.range().start().to_usize())
                    == Some(&b'u')
                    && let Value::Node(node) = &mut result
                {
                    node.fields.push(("kind", string("u")));
                }
                result
            }
            Expr::BytesLiteral(value) => constant(
                "bytes",
                string(
                    value
                        .value
                        .bytes()
                        .map(|byte| format!("{byte:02x}"))
                        .collect::<String>(),
                ),
            ),
            Expr::NumberLiteral(value) => match &value.value {
                ast::Number::Int(value) => {
                    let raw = value.to_string().replace('_', "");
                    let (digits, radix) = if raw.starts_with("0x") || raw.starts_with("0X") {
                        (&raw[2..], 16)
                    } else if raw.starts_with("0o") || raw.starts_with("0O") {
                        (&raw[2..], 8)
                    } else if raw.starts_with("0b") || raw.starts_with("0B") {
                        (&raw[2..], 2)
                    } else {
                        (raw.as_str(), 10)
                    };
                    let decimal = if radix == 10 {
                        digits.to_owned()
                    } else {
                        num_bigint::BigUint::parse_bytes(digits.as_bytes(), radix)
                            .expect("parsed integer literal")
                            .to_str_radix(10)
                    };
                    constant("int", Value::Number(decimal))
                }
                ast::Number::Float(value) => constant("float", string(float_repr(*value))),
                ast::Number::Complex { real, imag } => constant(
                    "complex",
                    string(if *real == 0.0 {
                        format!("{}j", float_repr(*imag).trim_end_matches(".0"))
                    } else {
                        format!(
                            "({}{}{}j)",
                            float_repr(*real).trim_end_matches(".0"),
                            if imag.is_sign_negative() { "" } else { "+" },
                            float_repr(*imag).trim_end_matches(".0")
                        )
                    }),
                ),
            },
            Expr::BooleanLiteral(value) => constant("bool", Value::Bool(value.value)),
            Expr::NoneLiteral(_) => constant("none", Value::Null),
            Expr::EllipsisLiteral(_) => constant("ellipsis", string("...")),
            Expr::Attribute(value) => node(
                "Attribute",
                vec![
                    ("value", self.expr(&value.value)),
                    ("attr", string(&value.attr)),
                    ("ctx", context(value.ctx)),
                ],
            ),
            Expr::Subscript(value) => node(
                "Subscript",
                vec![
                    ("value", self.expr(&value.value)),
                    ("slice", self.expr(&value.slice)),
                    ("ctx", context(value.ctx)),
                ],
            ),
            Expr::Starred(value) => node(
                "Starred",
                vec![
                    ("value", self.expr(&value.value)),
                    ("ctx", context(value.ctx)),
                ],
            ),
            Expr::Name(value) => node(
                "Name",
                vec![("id", string(&value.id)), ("ctx", context(value.ctx))],
            ),
            Expr::List(value) => node(
                "List",
                vec![
                    ("elts", self.exprs(&value.elts)),
                    ("ctx", context(value.ctx)),
                ],
            ),
            Expr::Tuple(value) => node(
                "Tuple",
                vec![
                    ("elts", self.exprs(&value.elts)),
                    ("ctx", context(value.ctx)),
                ],
            ),
            Expr::Slice(value) => node(
                "Slice",
                vec![
                    ("lower", self.opt(value.lower.as_deref())),
                    ("upper", self.opt(value.upper.as_deref())),
                    ("step", self.opt(value.step.as_deref())),
                ],
            ),
            Expr::TString(_) => empty("UnsupportedTemplateStr"),
            Expr::IpyEscapeCommand(_) => empty("UnsupportedIpyEscapeCommand"),
        }
    }
    fn pattern(&self, value: &Pattern) -> Value {
        match value {
            Pattern::MatchValue(value) => {
                node("MatchValue", vec![("value", self.expr(&value.value))])
            }
            Pattern::MatchSingleton(value) => node(
                "MatchSingleton",
                vec![(
                    "value",
                    match value.value {
                        ast::Singleton::None => Value::Null,
                        ast::Singleton::True => Value::Bool(true),
                        ast::Singleton::False => Value::Bool(false),
                    },
                )],
            ),
            Pattern::MatchSequence(value) => node(
                "MatchSequence",
                vec![(
                    "patterns",
                    Value::List(
                        value
                            .patterns
                            .iter()
                            .map(|pattern| self.pattern(pattern))
                            .collect(),
                    ),
                )],
            ),
            Pattern::MatchMapping(value) => node(
                "MatchMapping",
                vec![
                    ("keys", self.exprs(&value.keys)),
                    (
                        "patterns",
                        Value::List(
                            value
                                .patterns
                                .iter()
                                .map(|pattern| self.pattern(pattern))
                                .collect(),
                        ),
                    ),
                    ("rest", optional_string(value.rest.as_ref())),
                ],
            ),
            Pattern::MatchClass(value) => node(
                "MatchClass",
                vec![
                    ("cls", self.expr(&value.cls)),
                    (
                        "patterns",
                        Value::List(
                            value
                                .arguments
                                .patterns
                                .iter()
                                .map(|pattern| self.pattern(pattern))
                                .collect(),
                        ),
                    ),
                    (
                        "kwd_attrs",
                        Value::List(
                            value
                                .arguments
                                .keywords
                                .iter()
                                .map(|keyword| string(&keyword.attr))
                                .collect(),
                        ),
                    ),
                    (
                        "kwd_patterns",
                        Value::List(
                            value
                                .arguments
                                .keywords
                                .iter()
                                .map(|keyword| self.pattern(&keyword.pattern))
                                .collect(),
                        ),
                    ),
                ],
            ),
            Pattern::MatchStar(value) => node(
                "MatchStar",
                vec![("name", optional_string(value.name.as_ref()))],
            ),
            Pattern::MatchAs(value) => node(
                "MatchAs",
                vec![
                    (
                        "pattern",
                        value
                            .pattern
                            .as_deref()
                            .map_or(Value::Null, |pattern| self.pattern(pattern)),
                    ),
                    ("name", optional_string(value.name.as_ref())),
                ],
            ),
            Pattern::MatchOr(value) => node(
                "MatchOr",
                vec![(
                    "patterns",
                    Value::List(
                        value
                            .patterns
                            .iter()
                            .map(|pattern| self.pattern(pattern))
                            .collect(),
                    ),
                )],
            ),
        }
    }
}
