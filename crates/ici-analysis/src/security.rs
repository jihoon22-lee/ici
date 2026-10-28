//! Redaction-safe AST security rules. String matching is confined to literal policy values.
use crate::{finding, finish_status, parser::ParsedFile};
use ici_domain::{AnalysisOutput, AnalysisRequest, InspectionTarget, Measurement};
use regex::Regex;
use ruff_python_ast::token::TokenKind;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{self as ast, Expr, Stmt};
use ruff_text_size::{Ranged, TextRange};
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, LazyLock};

static SECRET_NAME: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(?:^|_)(?:api_?key|access_?key|auth_?token|client_?secret|passw(?:or)?d|passwd|private_?key|secret|token)(?:$|_)").expect("constant secret name pattern")
});
static PRIVATE_KEY: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----").expect("constant marker pattern")
});
const PLACEHOLDERS: &[&str] = &[
    "change-me",
    "change_me",
    "changeme",
    "dummy",
    "example",
    "not-a-real",
    "placeholder",
    "replace-me",
    "test-only",
    "your_",
    "xxxx",
];
const SECRET_PREFIXES: &[&str] = &["akia", "ghp_", "github_pat_", "sk-", "xoxb-", "xoxp-"];

pub(crate) fn literal_string(expression: &Expr) -> Option<&str> {
    if let Expr::StringLiteral(value) = expression {
        Some(value.value.to_str())
    } else {
        None
    }
}
pub(crate) fn keyword_boolean(call: &ast::ExprCall, name: &str, value: bool) -> bool {
    call.arguments.keywords.iter().any(|keyword| {
        keyword.arg.as_ref().is_some_and(|arg| arg.as_str() == name)
            && matches!(&keyword.value, Expr::BooleanLiteral(item) if item.value == value)
    })
}
fn entropy(value: &str) -> f64 {
    let mut counts = BTreeMap::new();
    let length = value.chars().count() as f64;
    if length == 0.0 {
        return 0.0;
    }
    for character in value.chars() {
        *counts.entry(character).or_insert(0usize) += 1;
    }
    -counts
        .values()
        .map(|count| {
            let probability = *count as f64 / length;
            probability * probability.log2()
        })
        .sum::<f64>()
}
fn assignment_names(expression: &Expr) -> Vec<String> {
    match expression {
        Expr::Name(name) => vec![name.id.to_string()],
        Expr::Attribute(attribute) => vec![attribute.attr.to_string()],
        Expr::Tuple(tuple) => tuple.elts.iter().flat_map(assignment_names).collect(),
        Expr::List(list) => list.elts.iter().flat_map(assignment_names).collect(),
        Expr::Subscript(subscript) => {
            literal_string(&subscript.slice).map_or_else(Vec::new, |name| vec![name.to_owned()])
        }
        _ => vec![],
    }
}
struct SecurityVisitor<'a> {
    file: &'a ParsedFile,
    allowlist: BTreeSet<String>,
    suppressed: BTreeSet<u32>,
    targets: Vec<InspectionTarget>,
    calls: u64,
    literals: u64,
}
impl SecurityVisitor<'_> {
    fn suppressed(&self, range: TextRange) -> bool {
        let start = self.file.source.location(range.start().to_usize()).0;
        let end = self.file.source.location(range.end().to_usize()).0;
        self.suppressed.range(start..=end).next().is_some()
    }
    fn target(
        &self,
        range: TextRange,
        rule: &str,
        message: &str,
        snippet: String,
    ) -> InspectionTarget {
        let mut target = self.file.source.target(range, format!("Security:{rule}"));
        target.status = "WARN".into();
        target.message = message.into();
        target.snippet = snippet;
        target
    }
    fn secret(&mut self, name: &str, value: Option<&Expr>, owner: TextRange) {
        let Some(value) = value.and_then(literal_string) else {
            return;
        };
        self.literals += 1;
        let normalized_name = name.to_lowercase();
        if self.suppressed(owner) || self.allowlist.contains(&normalized_name) {
            return;
        }
        if PRIVATE_KEY.is_match(value) {
            let mut target = self.target(
                owner,
                "PrivateKey",
                "Private-key material is embedded in a source literal",
                format!("{name} = \"***REDACTED***\""),
            );
            target
                .metrics
                .insert("detector".into(), json!("private-key-marker"));
            self.targets.push(target);
            return;
        }
        let normalized = value.to_lowercase();
        let length = value.chars().count();
        if length < 6
            || PLACEHOLDERS
                .iter()
                .any(|marker| normalized.trim().contains(marker))
        {
            return;
        }
        let value_entropy = entropy(value);
        if SECRET_PREFIXES
            .iter()
            .any(|prefix| normalized.starts_with(prefix))
            || (SECRET_NAME.is_match(name) && (length >= 8 || value_entropy >= 2.5))
        {
            let mut target = self.target(
                owner,
                "HardcodedSecret",
                &format!("Secret-like literal assigned to {name}; value REDACTED"),
                format!("{name} = \"***REDACTED***\""),
            );
            target
                .metrics
                .insert("detector".into(), json!("name-context-and-entropy"));
            target.metrics.insert("length".into(), json!(length));
            target.metrics.insert(
                "entropy".into(),
                json!(
                    format!("{value_entropy:.2}")
                        .parse::<f64>()
                        .expect("finite entropy")
                ),
            );
            self.targets.push(target);
        }
    }
    fn call(&mut self, call: &ast::ExprCall) {
        self.calls += 1;
        if self.suppressed(call.range()) {
            return;
        }
        let Some(qualified) = self.file.scopes.resolve(&call.func) else {
            return;
        };
        let rule = match qualified.as_str() {
            "hashlib.md5" | "Crypto.Hash.MD5.new" | "Cryptodome.Hash.MD5.new" => {
                if qualified == "hashlib.md5" && keyword_boolean(call, "usedforsecurity", false) {
                    return;
                }
                Some((
                    "WeakCryptoMD5",
                    "MD5 is not collision resistant",
                    "md5(...)".to_owned(),
                ))
            }
            "hashlib.sha1" | "Crypto.Hash.SHA1.new" | "Cryptodome.Hash.SHA1.new" => {
                if qualified == "hashlib.sha1" && keyword_boolean(call, "usedforsecurity", false) {
                    return;
                }
                Some((
                    "WeakCryptoSHA1",
                    "SHA-1 is not collision resistant",
                    "sha1(...)".to_owned(),
                ))
            }
            "random.choice" | "random.randint" | "random.random" | "random.randbytes"
            | "random.randrange" => Some((
                "WeakRandom",
                "The random module is not suitable for security-sensitive randomness",
                format!("{qualified}(...)"),
            )),
            "eval" | "exec" | "builtins.eval" | "builtins.exec" => Some((
                "EvalExec",
                "Dynamic code execution accepts untrusted input",
                format!("{qualified}(...)"),
            )),
            "pickle.load" | "pickle.loads" | "_pickle.load" | "_pickle.loads" => Some((
                "PickleLoad",
                "Pickle deserialization can execute attacker-controlled code",
                format!("{qualified}(...)"),
            )),
            "os.system" | "os.popen" => Some((
                "CommandProcessor",
                "Command text is executed through a system shell",
                format!("{qualified}(...)"),
            )),
            _ if qualified.starts_with("subprocess.") && keyword_boolean(call, "shell", true) => {
                Some((
                    "ShellTrue",
                    "subprocess shell=True expands input through a command processor",
                    format!("{qualified}(..., shell=True)"),
                ))
            }
            _ => None,
        };
        if let Some((rule, message, snippet)) = rule {
            self.targets
                .push(self.target(call.range(), rule, message, snippet));
        }
    }
}
impl<'a> Visitor<'a> for SecurityVisitor<'_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::Assign(assignment) => {
                for target in &assignment.targets {
                    for name in assignment_names(target) {
                        self.secret(&name, Some(&assignment.value), statement.range());
                    }
                }
            }
            Stmt::AnnAssign(assignment) => {
                for name in assignment_names(&assignment.target) {
                    self.secret(&name, assignment.value.as_deref(), statement.range());
                }
            }
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }
    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Dict(dictionary) => {
                for item in &dictionary.items {
                    if let Some(name) = item.key.as_ref().and_then(literal_string) {
                        self.secret(name, Some(&item.value), expression.range());
                    }
                }
            }
            Expr::Call(call) => self.call(call),
            _ => {}
        }
        visitor::walk_expr(self, expression);
    }
}
pub fn analyze(request: &AnalysisRequest, files: &[Arc<ParsedFile>], output: &mut AnalysisOutput) {
    let allowlist: BTreeSet<String> = request
        .options
        .get("secret_name_allowlist")
        .and_then(|value| value.as_array())
        .map_or_else(BTreeSet::new, |values| {
            values
                .iter()
                .filter_map(|value| value.as_str())
                .map(str::to_lowercase)
                .collect()
        });
    let (mut calls, mut literals) = (0, 0);
    for file in files {
        let suppressed = file
            .parsed
            .tokens()
            .iter()
            .filter(|token| token.kind() == TokenKind::Comment)
            .filter(|token| {
                file.source.text[token.range().start().to_usize()..token.range().end().to_usize()]
                    .to_lowercase()
                    .contains("nosec")
            })
            .map(|token| file.source.location(token.range().start().to_usize()).0)
            .collect();
        let mut visitor = SecurityVisitor {
            file,
            allowlist: allowlist.clone(),
            suppressed,
            targets: vec![],
            calls: 0,
            literals: 0,
        };
        visitor.visit_body(file.body());
        calls += visitor.calls;
        literals += visitor.literals;
        visitor.targets.sort_by(|left, right| {
            (
                &left.start_line,
                &left.start_column,
                &left.target_name,
                &left.message,
            )
                .cmp(&(
                    &right.start_line,
                    &right.start_column,
                    &right.target_name,
                    &right.message,
                ))
        });
        for target in visitor.targets {
            finding::hygiene(output, request, &target, "security", "high", "", "high");
            output.targets.push(target);
        }
        let mut target = InspectionTarget::at(&file.source.path, 1);
        target.target_name = "Security:ASTScan".into();
        target.message = "Bounded Python AST security rules completed".into();
        output.targets.push(target);
    }
    let findings = output.observation.findings.len();
    output.observation.measurements.extend([
        Measurement {
            unit: "files".into(),
            ..Measurement::measured("files_checked", files.len() as f64)
        },
        Measurement {
            unit: "issues".into(),
            ..Measurement::measured("issues_found", findings as f64)
        },
    ]);
    output.extra = json!({"analysis_mode":"python-ast-rules-v1","files_checked":files.len(),"calls_checked":calls,"secret_literals_checked":literals,"secret_name_allowlist_count":allowlist.len(),"limitations":["Rules identify risky syntax and import aliases, not runtime taint flow","Exact secret names can be allowlisted; secret values are never retained"]});
    output.summary = if findings > 0 {
        format!("Security hygiene: {findings} finding(s)")
    } else {
        format!("Security hygiene clean across {} file(s)", files.len())
    };
    finish_status(output, request);
}
