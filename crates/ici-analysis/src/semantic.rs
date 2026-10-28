//! Bounded exact AST shapes, with explicit conservative exclusions.
use crate::{
    parser::ParsedFile,
    python_ast::{Node, Projector, Value},
};
use ruff_python_ast::PythonVersion;
use ruff_python_parser::{Mode, ParseOptions, parse_unchecked};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt::Write,
    sync::{Arc, atomic::AtomicBool},
};

pub const ALGORITHM: &str = "sha256/semantic-shape-v1";
type Occurrence<'a> = (&'a Region, usize, usize);
#[derive(Clone, Debug, Serialize)]
pub struct Region {
    pub file_path: String,
    pub name: String,
    pub kind: String,
    pub start_line: u32,
    pub end_line: u32,
    pub canonical_shape: String,
    pub fingerprint: String,
    pub node_count: usize,
    pub fingerprint_algorithm: &'static str,
    pub shape_version: &'static str,
}
#[derive(Clone, Debug, Serialize)]
pub struct Exclusion {
    pub file_path: String,
    pub reason: String,
    pub region_name: Option<String>,
    pub start_line: Option<u32>,
    pub end_line: Option<u32>,
    pub message: String,
}
#[derive(Default, Debug, Serialize)]
pub struct Outcome {
    pub regions: Vec<Region>,
    pub exclusions: Vec<Exclusion>,
    pub files: Vec<String>,
    pub status: String,
    pub node_count: usize,
    pub serialized_chars: usize,
}
#[derive(Clone, Copy)]
pub struct Limits {
    pub files: usize,
    pub regions: usize,
    pub nodes: usize,
    pub chars: usize,
}
impl Default for Limits {
    fn default() -> Self {
        Self {
            files: 256,
            regions: 20_000,
            nodes: 500_000,
            chars: 16 * 1024 * 1024,
        }
    }
}
fn children<'a>(value: &'a Value, result: &mut Vec<&'a Node>) {
    match value {
        Value::Node(node) => result.push(node),
        Value::List(values) => {
            for value in values {
                children(value, result);
            }
        }
        _ => {}
    }
}
fn node_children(node: &Node) -> Vec<&Node> {
    let mut result = vec![];
    for (_, value) in &node.fields {
        children(value, &mut result);
    }
    result
}
fn count(node: &Node) -> usize {
    1 + node_children(node).into_iter().map(count).sum::<usize>()
}
fn unsafe_reason(node: &Node) -> Option<(String, String)> {
    let mut stack = vec![node];
    while let Some(current) = stack.pop() {
        let reason = match current.kind.as_str() {
            value if value.starts_with("Unsupported") => Some((
                "unsupported-node".into(),
                format!("unsupported AST node {value}"),
            )),
            "Lambda" => Some((
                "unsupported-node".into(),
                "lambda scopes are not named semantic regions".into(),
            )),
            "ListComp" | "SetComp" | "DictComp" | "GeneratorExp" => Some((
                "comprehension-scope".into(),
                "comprehension bindings require a nested scope and are excluded conservatively"
                    .into(),
            )),
            "Global" => Some((
                "global-statement".into(),
                "global bindings are excluded conservatively".into(),
            )),
            "Nonlocal" => Some((
                "nonlocal-statement".into(),
                "nonlocal bindings are excluded conservatively".into(),
            )),
            "ImportFrom"
                if node_children(current)
                    .iter()
                    .any(|alias| alias.name() == Some("*")) =>
            {
                Some((
                    "star-import".into(),
                    "star imports do not expose a bounded API anchor".into(),
                ))
            }
            "Call" => {
                let Value::Node(function) = current.get("func")? else {
                    return None;
                };
                let name = match function.kind.as_str() {
                    "Name" => function.get("id").and_then(Value::string),
                    "Attribute" => function.get("attr").and_then(Value::string),
                    _ => None,
                };
                if let Some(name @ ("eval" | "exec")) = name {
                    Some((
                        format!("dynamic-{name}"),
                        format!("dynamic {name} call is excluded"),
                    ))
                } else if name == Some("getattr") {
                    if let Some(Value::List(arguments)) = current.get("args")
                        && let Some(Value::Node(value)) = arguments.get(1)
                        && let Some(Value::List(value)) = value.get("value")
                        && let Some(Value::String(name)) = value.get(1)
                        && matches!(name.as_str(), "eval" | "exec")
                    {
                        Some((
                            format!("dynamic-{name}"),
                            format!("dynamic {name} lookup is excluded"),
                        ))
                    } else {
                        None
                    }
                } else {
                    None
                }
            }
            _ => None,
        };
        if reason.is_some() {
            return reason;
        }
        for child in node_children(current).into_iter().rev() {
            if !child.named() {
                stack.push(child);
            }
        }
    }
    None
}
fn bindings(root: &Node) -> (BTreeMap<String, usize>, BTreeSet<String>) {
    let mut bound = BTreeMap::new();
    let mut imported = BTreeSet::new();
    let bind = |bound: &mut BTreeMap<String, usize>, name: Option<&str>| {
        if let Some(name) = name
            && !name.is_empty()
            && !bound.contains_key(name)
        {
            let index = bound.len();
            bound.insert(name.into(), index);
        }
    };
    let mut stack = vec![(root, true)];
    while let Some((current, is_root)) = stack.pop() {
        if !is_root && current.named() {
            bind(&mut bound, current.name());
            continue;
        }
        match current.kind.as_str() {
            "arg" => bind(&mut bound, current.get("arg").and_then(Value::string)),
            "Name" if matches!(current.get("ctx"),Some(Value::Node(context)) if matches!(context.kind.as_str(),"Store"|"Del")) => {
                bind(&mut bound, current.get("id").and_then(Value::string))
            }
            "ExceptHandler" | "MatchAs" | "MatchStar" => {
                bind(&mut bound, current.get("name").and_then(Value::string))
            }
            "MatchMapping" => bind(&mut bound, current.get("rest").and_then(Value::string)),
            "Import" | "ImportFrom" => {
                for alias in node_children(current) {
                    if let Some(name) = alias.name()
                        && name != "*"
                    {
                        let name =
                            alias
                                .get("asname")
                                .and_then(Value::string)
                                .unwrap_or_else(|| {
                                    if current.kind == "Import" {
                                        name.split('.').next().unwrap_or(name)
                                    } else {
                                        name
                                    }
                                });
                        imported.insert(name.to_owned());
                    }
                }
            }
            _ => {}
        }
        stack.extend(
            node_children(current)
                .into_iter()
                .rev()
                .map(|child| (child, false)),
        );
    }
    (bound, imported)
}
fn ascii_string(output: &mut String, value: &str) {
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            '\u{8}' => output.push_str("\\b"),
            '\u{c}' => output.push_str("\\f"),
            value if (' '..='~').contains(&value) => output.push(value),
            value => {
                let mut encoded = [0u16; 2];
                for unit in value.encode_utf16(&mut encoded) {
                    write!(output, "\\u{unit:04x}").expect("String write");
                }
            }
        }
    }
    output.push('"');
}
struct Canonical<'a> {
    root: &'a Node,
    bound: BTreeMap<String, usize>,
    imported: BTreeSet<String>,
    nodes: usize,
    max: usize,
    cancellation: Option<&'a AtomicBool>,
}
impl Canonical<'_> {
    fn name(&self, value: &str) -> String {
        if !self.imported.contains(value) {
            if let Some(index) = self.bound.get(value) {
                return format!("LOCAL_{index}");
            }
            if self.root.name() == Some(value) {
                return "<self-name>".into();
            }
        }
        value.into()
    }
    fn value(
        &mut self,
        value: &Value,
        parent: &Node,
        field: &str,
        alpha: bool,
        output: &mut String,
    ) -> Result<(), String> {
        match value {
            Value::Node(node) => self.node(node, alpha, output)?,
            Value::List(values) => {
                output.push('[');
                let mut first = true;
                for value in values {
                    if matches!(value,Value::Node(node) if node.named()) {
                        continue;
                    }
                    if !first {
                        output.push(',');
                    }
                    first = false;
                    self.value(value, parent, field, alpha, output)?;
                }
                output.push(']');
            }
            Value::String(value) => {
                let binding = matches!(
                    (parent.kind.as_str(), field),
                    ("Name", "id")
                        | ("arg", "arg")
                        | ("ExceptHandler", "name")
                        | ("MatchAs", "name")
                        | ("MatchStar", "name")
                        | ("MatchMapping", "rest")
                );
                ascii_string(
                    output,
                    &if alpha && binding {
                        self.name(value)
                    } else {
                        value.clone()
                    },
                );
            }
            Value::Utf16(value) => {
                output.push('"');
                for unit in value {
                    match *unit {
                        0x22 => output.push_str("\\\""),
                        0x5c => output.push_str("\\\\"),
                        0x0a => output.push_str("\\n"),
                        0x0d => output.push_str("\\r"),
                        0x09 => output.push_str("\\t"),
                        0x08 => output.push_str("\\b"),
                        0x0c => output.push_str("\\f"),
                        0x20..=0x7e => output.push(char::from_u32(*unit as u32).unwrap()),
                        _ => {
                            write!(output, "\\u{unit:04x}").expect("String write");
                        }
                    }
                }
                output.push('"');
            }
            Value::Number(value) => output.push_str(value),
            Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
            Value::Null => output.push_str("null"),
        }
        if output.len() > self.max {
            return Err("max-serialized-chars".into());
        }
        Ok(())
    }
    fn node(&mut self, node: &Node, alpha: bool, output: &mut String) -> Result<(), String> {
        if crate::cancellation_requested(self.cancellation) {
            return Err("cancelled".into());
        }
        self.nodes += 1;
        output.push('[');
        ascii_string(output, &node.kind);
        output.push_str(",[");
        let mut first = true;
        for (field, value) in &node.fields {
            let root = std::ptr::eq(node, self.root);
            if (root
                && matches!(node.kind.as_str(), "FunctionDef" | "AsyncFunctionDef")
                && *field == "type_comment"
                && matches!(value, Value::Null))
                || (*field == "type_params"
                    && matches!(value,Value::List(values) if values.is_empty()))
            {
                continue;
            }
            if !first {
                output.push(',');
            }
            first = false;
            output.push('[');
            ascii_string(output, field);
            output.push(',');
            if root && *field == "name" {
                ascii_string(
                    output,
                    if node.kind == "ClassDef" {
                        "<class-name>"
                    } else {
                        "<function-name>"
                    },
                );
            } else {
                let alpha = alpha
                    && !(root
                        && matches!(node.kind.as_str(), "FunctionDef" | "AsyncFunctionDef")
                        && matches!(*field, "decorator_list" | "returns" | "type_params"))
                    && !(root
                        && node.kind == "ClassDef"
                        && matches!(
                            *field,
                            "bases" | "decorator_list" | "keywords" | "type_params"
                        ))
                    && !(node.kind == "arguments" && matches!(*field, "defaults" | "kw_defaults"))
                    && !(node.kind == "arg" && *field == "annotation");
                self.value(value, node, field, alpha, output)?;
            }
            output.push(']');
        }
        output.push_str("]]");
        if output.len() > self.max {
            return Err("max-serialized-chars".into());
        }
        Ok(())
    }
}
fn specs<'a>(
    node: &'a Node,
    parents: &mut Vec<(String, String)>,
    result: &mut Vec<(&'a Node, String, String)>,
) {
    let mut added = false;
    if node.named() {
        let name = node.name().unwrap_or("");
        let kind = if node.kind == "ClassDef" {
            "class"
        } else if parents.last().is_some_and(|(_, kind)| kind == "class") {
            "method"
        } else {
            "function"
        };
        parents.push((name.into(), kind.into()));
        result.push((
            node,
            parents
                .iter()
                .map(|(name, _)| name.as_str())
                .collect::<Vec<_>>()
                .join("."),
            kind.into(),
        ));
        added = true;
    }
    for child in node_children(node) {
        specs(child, parents, result);
    }
    if added {
        parents.pop();
    }
}
fn exclude(
    outcome: &mut Outcome,
    path: &str,
    spec: Option<(&Node, &str)>,
    reason: &str,
    message: &str,
) {
    let span = spec.and_then(|(node, _)| node.span);
    outcome.exclusions.push(Exclusion {
        file_path: path.into(),
        reason: reason.into(),
        region_name: spec.map(|(_, name)| name.into()),
        start_line: span.map(|span| span.0),
        end_line: span.map(|span| span.1),
        message: message.into(),
    });
}
pub fn analyze(
    files: &[Arc<ParsedFile>],
    limits: Limits,
    cancellation: Option<&AtomicBool>,
) -> Outcome {
    let mut ordered: Vec<_> = files.iter().collect();
    ordered.sort_by_key(|file| &file.source.path);
    let mut outcome = Outcome {
        files: ordered
            .iter()
            .map(|file| file.source.path.clone())
            .collect(),
        status: "ok".into(),
        ..Outcome::default()
    };
    if files.len() > limits.files {
        exclude(
            &mut outcome,
            "<sources>",
            None,
            "max-files",
            &format!(
                "source set exceeds max_files={}; no semantic regions were emitted",
                limits.files
            ),
        );
        outcome.status = "excluded".into();
        return outcome;
    }
    let mut seen = 0;
    for file in ordered {
        if crate::cancellation_requested(cancellation) {
            exclude(
                &mut outcome,
                &file.source.path,
                None,
                "cancelled",
                "analysis cancelled",
            );
            break;
        }
        let floor = parse_unchecked(
            &file.source.text,
            ParseOptions::from(Mode::Module).with_target_version(PythonVersion::PY310),
        );
        if !floor.errors().is_empty() || !floor.unsupported_syntax_errors().is_empty() {
            exclude(
                &mut outcome,
                &file.source.path,
                None,
                "malformed-ast",
                "Python source could not be parsed; no partial regions were emitted",
            );
            continue;
        }
        let projector = Projector::new(file, cancellation);
        let tree = projector.module();
        if crate::cancellation_requested(cancellation) {
            exclude(
                &mut outcome,
                &file.source.path,
                None,
                "cancelled",
                "analysis cancelled",
            );
            break;
        }
        if !projector.type_comments_complete() {
            exclude(
                &mut outcome,
                &file.source.path,
                None,
                "malformed-ast",
                "Python source could not be parsed; no partial regions were emitted",
            );
            continue;
        }
        let nodes = count(&tree);
        if outcome.node_count + nodes > limits.nodes {
            exclude(
                &mut outcome,
                &file.source.path,
                None,
                "max-nodes",
                "AST node budget was exhausted; no partial source result was emitted",
            );
            outcome.regions.clear();
            break;
        }
        outcome.node_count += nodes;
        let mut regions = vec![];
        specs(&tree, &mut vec![], &mut regions);
        regions.sort_by_key(|(node, name, kind)| (node.span, name.clone(), kind.clone()));
        if seen + regions.len() > limits.regions {
            exclude(
                &mut outcome,
                &file.source.path,
                None,
                "max-regions",
                "named-region budget was exhausted; no partial source result was emitted",
            );
            outcome.regions.clear();
            break;
        }
        seen += regions.len();
        if let Some((reason, message)) = unsafe_reason(&tree) {
            exclude(&mut outcome, &file.source.path, None, &reason, &message);
        }
        let mut failed = false;
        for (node, name, kind) in regions {
            if let Some((reason, message)) = unsafe_reason(node) {
                exclude(
                    &mut outcome,
                    &file.source.path,
                    Some((node, &name)),
                    &reason,
                    &message,
                );
                continue;
            }
            let (bound, imported) = bindings(node);
            let mut canonical = Canonical {
                root: node,
                bound,
                imported,
                nodes: 0,
                max: limits.chars.saturating_sub(outcome.serialized_chars),
                cancellation,
            };
            let mut shape = String::new();
            if let Err(reason) = canonical.node(node, true, &mut shape) {
                exclude(
                    &mut outcome,
                    &file.source.path,
                    None,
                    &reason,
                    "serialized semantic-shape budget was exhausted; no partial source result was emitted",
                );
                outcome.regions.clear();
                failed = true;
                break;
            }
            outcome.serialized_chars += shape.len();
            let span = node.span.unwrap_or((1, 1));
            let fingerprint = format!(
                "{:x}",
                Sha256::digest(format!("{ALGORITHM}\0{shape}").as_bytes())
            );
            outcome.regions.push(Region {
                file_path: file.source.path.clone(),
                name,
                kind,
                start_line: span.0,
                end_line: span.1,
                canonical_shape: shape,
                fingerprint,
                node_count: canonical.nodes,
                fingerprint_algorithm: ALGORITHM,
                shape_version: "semantic-shape-v1",
            });
        }
        if failed {
            break;
        }
    }
    outcome.regions.sort_by(|left, right| {
        (&left.file_path, left.start_line, left.end_line, &left.name).cmp(&(
            &right.file_path,
            right.start_line,
            right.end_line,
            &right.name,
        ))
    });
    outcome.exclusions.sort_by(|left, right| {
        (
            &left.file_path,
            left.start_line,
            left.end_line,
            &left.region_name,
            &left.reason,
        )
            .cmp(&(
                &right.file_path,
                right.start_line,
                right.end_line,
                &right.region_name,
                &right.reason,
            ))
    });
    if !outcome.exclusions.is_empty() {
        outcome.status = "excluded".into();
    }
    outcome
}

pub(crate) fn merge(
    request: &ici_domain::AnalysisRequest,
    files: &[crate::duplicates::DuplicateFileData],
    outcome: &Outcome,
    output: &mut ici_domain::AnalysisOutput,
) {
    use ici_domain::{EvidenceLevel, Finding, InspectionTarget, SourceSpan};
    use serde_json::json;
    let indexes: BTreeMap<_, _> = files
        .iter()
        .enumerate()
        .map(|(index, file)| (file.file_path.as_str(), index))
        .collect();
    let mut duplicated = BTreeSet::new();
    let mut existing = BTreeSet::new();
    let mut groups = output.extra["clone_groups"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    for group in &groups {
        let mut occurrences = vec![];
        for (position, location) in group["occurrences"].as_array().unwrap().iter().enumerate() {
            let path = location["file_path"].as_str().unwrap();
            let start = location["start_line"].as_u64().unwrap() as u32;
            let end = location["end_line"].as_u64().unwrap() as u32;
            occurrences.push((path.to_owned(), start, end));
            if position > 0
                && let Some(index) = indexes.get(path)
            {
                duplicated.extend(
                    files[*index]
                        .records(start, end)
                        .iter()
                        .map(|(line, _)| (*index, *line)),
                );
            }
        }
        occurrences.sort();
        existing.insert(occurrences);
    }
    let mut ordered: Vec<_> = outcome.regions.iter().collect();
    ordered.sort_by(|left, right| {
        (
            &left.file_path,
            left.start_line,
            std::cmp::Reverse(left.end_line),
            &left.name,
            &left.kind,
        )
            .cmp(&(
                &right.file_path,
                right.start_line,
                std::cmp::Reverse(right.end_line),
                &right.name,
                &right.kind,
            ))
    });
    let mut stack: Vec<&Region> = vec![];
    let mut parents = BTreeSet::new();
    for region in ordered {
        while stack.last().is_some_and(|parent| {
            parent.file_path != region.file_path || region.start_line > parent.end_line
        }) {
            stack.pop();
        }
        if let Some(parent) = stack.iter().rev().find(|parent| {
            matches!(parent.kind.as_str(), "function" | "method")
                && parent.start_line <= region.start_line
                && region.end_line <= parent.end_line
        }) {
            parents.insert((
                parent.file_path.as_str(),
                parent.name.as_str(),
                parent.start_line,
                parent.end_line,
            ));
        }
        stack.push(region);
    }
    let minimum = request
        .options
        .get("window_size")
        .or_else(|| request.options.get("min_window"))
        .and_then(serde_json::Value::as_u64)
        .unwrap_or(6) as usize;
    let mut buckets: BTreeMap<(&str, &str), Vec<Occurrence<'_>>> = BTreeMap::new();
    let mut parent_excluded = 0;
    let mut trivial = 0;
    for region in &outcome.regions {
        if !matches!(region.kind.as_str(), "function" | "method") {
            continue;
        }
        if parents.contains(&(
            region.file_path.as_str(),
            region.name.as_str(),
            region.start_line,
            region.end_line,
        )) {
            parent_excluded += 1;
            continue;
        }
        let Some(index) = indexes.get(region.file_path.as_str()) else {
            continue;
        };
        let count = files[*index]
            .records(region.start_line, region.end_line)
            .len();
        if count < minimum {
            trivial += 1;
            continue;
        }
        buckets
            .entry((&region.fingerprint, &region.canonical_shape))
            .or_default()
            .push((region, *index, count));
    }
    let eligible = buckets.values().map(Vec::len).sum::<usize>();
    let mut candidates = vec![];
    let mut suppressed = 0;
    for mut occurrences in buckets.into_values() {
        if occurrences.len() < 2 {
            continue;
        }
        occurrences.sort_by_key(|(region, _, _)| {
            (
                &region.file_path,
                region.start_line,
                region.end_line,
                &region.name,
            )
        });
        let key: Vec<_> = occurrences
            .iter()
            .map(|(region, _, _)| (region.file_path.clone(), region.start_line, region.end_line))
            .collect();
        if existing.contains(&key) {
            suppressed += 1;
            continue;
        }
        candidates.push(occurrences);
    }
    let size =
        |group: &Vec<(&Region, usize, usize)>| group.iter().map(|item| item.2).max().unwrap_or(0);
    candidates.sort_by(|left, right| {
        size(right)
            .cmp(&size(left))
            .then_with(|| right.len().cmp(&left.len()))
            .then_with(|| {
                left.iter()
                    .map(|(region, _, _)| (&region.file_path, region.start_line, region.end_line))
                    .cmp(right.iter().map(|(region, _, _)| {
                        (&region.file_path, region.start_line, region.end_line)
                    }))
            })
    });
    let reported = candidates.len();
    let mut snippets = BTreeMap::new();
    for occurrences in candidates {
        let id = groups.len() + 1;
        let representative = occurrences[0].0;
        let count = occurrences.iter().map(|item| item.2).min().unwrap_or(0);
        let index = occurrences[0].1;
        let snippet = snippets
            .entry(index)
            .or_insert_with(|| crate::duplicates::raw_lines(&files[index].raw_text))
            .get(
                representative.start_line.saturating_sub(1) as usize
                    ..representative.end_line as usize,
            )
            .unwrap_or(&[])
            .concat()
            .trim_end()
            .to_owned();
        let mut locations = vec![];
        for (position, (region, index, _)) in occurrences.iter().enumerate() {
            if position > 0 {
                duplicated.extend(
                    files[*index]
                        .records(region.start_line, region.end_line)
                        .iter()
                        .map(|(line, _)| (*index, *line)),
                );
            }
            let message = format!(
                "Equivalent bounded Python AST shape shared across {} callable regions",
                occurrences.len()
            );
            let mut target = InspectionTarget::at(&region.file_path, region.start_line);
            target.end_line = Some(region.end_line);
            target.target_name = format!("CloneGroup#{id}");
            target.status = "WARN".into();
            target.message = message.clone();
            target.snippet = snippet.chars().take(300).collect();
            target.metrics = BTreeMap::from([
                ("clone_group".into(), json!(id)),
                ("duplicate_lines".into(), json!(count)),
                ("fingerprint".into(), json!(representative.fingerprint)),
                ("fingerprint_algorithm".into(), json!(ALGORITHM)),
                ("shape_policy".into(), json!("python-bounded-ast-shape-v1")),
            ]);
            output.targets.push(target);
            let mut finding = Finding::new(
                "dup.python-semantic-shape",
                message,
                SourceSpan {
                    end_line: Some(region.end_line),
                    ..SourceSpan::line(&region.file_path, region.start_line)
                },
                "ici.dup",
            );
            finding.fingerprint = format!(
                "sha256:{:x}",
                Sha256::digest(
                    format!(
                        "ici.dup\0{}\0{}\0{}\0{}",
                        region.fingerprint, region.file_path, region.start_line, region.end_line
                    )
                    .as_bytes()
                )
            );
            finding.evidence = EvidenceLevel::Estimated;
            finding.confidence = "high".into();
            finding.severity = "medium".into();
            finding.category = "maintainability".into();
            finding.component_id = Some(request.component_id.clone());
            finding.task_id = Some(request.task_id.clone());
            output.observation.findings.push(finding);
            locations.push(json!({"file_path":region.file_path,"start_line":region.start_line,"end_line":region.end_line,"loc":format!("{}:{}-{}",region.file_path,region.start_line,region.end_line),"region_name":region.name,"region_kind":region.kind}));
        }
        groups.push(json!({"id":id,"fingerprint":representative.fingerprint,"fingerprint_algorithm":ALGORITHM,"detection":"python-ast-semantic-shape","shape_policy":"python-bounded-ast-shape-v1","language":"python","lines_count":count,"occurrences_count":occurrences.len(),"occurrences":locations,"snippet":snippet}));
    }
    let total = files.iter().map(|file| file.indexed.len()).sum::<usize>();
    let count = duplicated.len();
    let percent = if total == 0 {
        0.0
    } else {
        count as f64 / total as f64 * 100.0
    };
    let warn = request
        .options
        .get("warn_pct")
        .and_then(serde_json::Value::as_f64)
        .unwrap_or(5.0);
    let fail = request
        .options
        .get("fail_pct")
        .and_then(serde_json::Value::as_f64)
        .unwrap_or(15.0);
    let represented: BTreeSet<_> = output
        .targets
        .iter()
        .filter(|target| target.target_name.starts_with("CloneGroup#"))
        .map(|target| target.file_path.clone())
        .collect();
    output.targets.retain(|target| {
        target.target_name != "DuplicateScan" || !represented.contains(&target.file_path)
    });
    for target in &mut output.targets {
        if target.target_name.starts_with("CloneGroup#") {
            target.status = if percent > warn || percent > fail {
                "WARN"
            } else {
                "PASS"
            }
            .into();
        }
    }
    for measurement in &mut output.observation.measurements {
        if measurement.name == "clone_groups" {
            measurement.value = groups.len() as f64;
        }
        if measurement.name == "duplicated_lines" {
            measurement.value = count as f64;
            measurement.numerator = Some(count as u64);
        }
    }
    output.extra["clone_groups"] = json!(groups);
    output.extra["clone_groups_count"] = json!(groups.len());
    output.extra["duplicated_lines"] = json!(count);
    output.extra["duplication_pct"] = json!(percent);
    record_metadata(
        request,
        Some(outcome),
        Stats {
            eligible,
            trivial,
            parent_excluded,
            suppressed,
            reported,
        },
        &mut output.extra,
    );
    for exclusion in &outcome.exclusions {
        output.observation.limitations.push(format!(
            "{}:{}: semantic {}: {}",
            exclusion.file_path,
            exclusion.start_line.unwrap_or(1),
            exclusion.reason,
            exclusion.message
        ));
    }
    output.score = Some(percent);
    output.summary = format!(
        "Code Duplication Rate: {percent:.1}% ({} distinct clone groups found, {reported} from Python AST shapes)",
        groups.len()
    );
    crate::finish_status(output, request);
    if percent > fail {
        output.status = if request
            .options
            .get("mode")
            .and_then(serde_json::Value::as_str)
            == Some("pass_warn")
        {
            "WARN"
        } else {
            "FAIL"
        }
        .into();
    }
}

#[derive(Default)]
pub(crate) struct Stats {
    pub eligible: usize,
    pub trivial: usize,
    pub parent_excluded: usize,
    pub suppressed: usize,
    pub reported: usize,
}
pub(crate) fn record_metadata(
    request: &ici_domain::AnalysisRequest,
    outcome: Option<&Outcome>,
    stats: Stats,
    extra: &mut serde_json::Value,
) {
    use serde_json::json;
    let empty = Outcome::default();
    let outcome = outcome.unwrap_or(&empty);
    let policy = request
        .options
        .get("python_semantic")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("auto");
    let Stats {
        eligible,
        trivial,
        parent_excluded,
        suppressed,
        reported,
    } = stats;
    let mut reasons = BTreeMap::new();
    for excluded in &outcome.exclusions {
        *reasons.entry(&excluded.reason).or_insert(0) += 1;
    }
    let mode = if policy == "off" {
        "off"
    } else if outcome.exclusions.is_empty() {
        "bounded"
    } else if outcome.regions.is_empty() {
        "unavailable"
    } else {
        "partial"
    };
    let fields = json!({"python_semantic_policy":request.options.get("python_semantic").and_then(serde_json::Value::as_str).unwrap_or("auto"),"python_semantic_mode":mode,"python_semantic_shape_policy":"python-bounded-ast-shape-v1","python_semantic_fingerprint_algorithm":ALGORITHM,"python_semantic_shape_version":"semantic-shape-v1","python_semantic_regions_observed":outcome.regions.len(),"python_semantic_regions_eligible":eligible,"python_semantic_trivial_regions_excluded":trivial,"python_semantic_parent_regions_excluded":parent_excluded,"python_semantic_exclusions":outcome.exclusions.len(),"python_semantic_exclusion_counts":reasons,"python_semantic_groups_suppressed":suppressed,"python_semantic_groups_reported":reported,"python_semantic_nodes_observed":outcome.node_count,"python_semantic_serialized_chars":outcome.serialized_chars});
    for (key, value) in fields.as_object().unwrap() {
        extra[key] = value.clone();
    }
}
