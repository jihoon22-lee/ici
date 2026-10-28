use ruff_python_ast::visitor::source_order::{SourceOrderVisitor, TraversalSignal};
use ruff_python_ast::{AnyNodeRef, PythonVersion};
use ruff_python_parser::{Mode, ParseOptions, parse_unchecked};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::io::{self, Read};
use std::str::FromStr;

fn line_starts(source: &str) -> Vec<usize> {
    let bytes = source.as_bytes();
    let mut starts = vec![0];
    for (index, byte) in bytes.iter().enumerate() {
        if *byte == b'\n' || (*byte == b'\r' && bytes.get(index + 1) != Some(&b'\n')) {
            starts.push(index + 1);
        }
    }
    starts
}

fn position(source: &str, starts: &[usize], offset: usize) -> Value {
    let row = starts.partition_point(|value| *value <= offset) - 1;
    let start = starts[row];
    json!({"line": row + 1, "byte_column": offset - start,
           "character_column": source[start..offset].chars().count()})
}

#[derive(Default)]
struct Nodes {
    counts: BTreeMap<String, usize>,
    details: Vec<Value>,
}
impl<'a> SourceOrderVisitor<'a> for Nodes {
    fn enter_node(&mut self, node: AnyNodeRef<'a>) -> TraversalSignal {
        let kind = format!("{:?}", node.kind());
        *self.counts.entry(kind.clone()).or_default() += 1;
        if self.details.len() < 512 {
            let mut item = json!({"kind": kind, "start": node.range().start().to_usize(), "end": node.range().end().to_usize()});
            match node {
                AnyNodeRef::Alias(alias) => {
                    item["name"] = json!(alias.name.as_str());
                    item["asname"] = json!(alias.asname.as_ref().map(|name| name.as_str()));
                }
                AnyNodeRef::ExprName(name) => {
                    item["name"] = json!(name.id.as_str());
                }
                AnyNodeRef::StmtFunctionDef(function) => {
                    item["is_async"] = json!(function.is_async);
                }
                AnyNodeRef::StmtFor(statement) => {
                    item["is_async"] = json!(statement.is_async);
                }
                _ => {}
            }
            self.details.push(item);
        }
        TraversalSignal::Traverse
    }
}

// Explicit evaluation boundary, not a complete Python codec implementation.
fn decode(bytes: &[u8]) -> Result<(String, String, usize), String> {
    let bom = bytes.starts_with(&[0xef, 0xbb, 0xbf]);
    let bytes = if bom { &bytes[3..] } else { bytes };
    let header: String = bytes.iter().map(|byte| char::from(*byte)).collect();
    let mut encoding = None;
    for line in header.lines().take(2) {
        let line = line.trim_start();
        if !line.is_empty() && !line.starts_with('#') {
            break;
        }
        if let Some((_, suffix)) = line.split_once("coding") {
            if let Some(suffix) = suffix
                .strip_prefix(':')
                .or_else(|| suffix.strip_prefix('='))
            {
                let label: String = suffix
                    .trim_start()
                    .chars()
                    .take_while(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
                    .collect();
                if !label.is_empty() {
                    encoding = Some(label.to_ascii_lowercase().replace('_', "-"));
                    break;
                }
            }
        }
    }
    let encoding = encoding.as_deref().unwrap_or("utf-8");
    if bom && !matches!(encoding, "utf-8" | "utf8" | "utf-8-sig") {
        return Err("bom-encoding-conflict".into());
    }
    let text = match encoding {
        "utf-8" | "utf8" | "utf-8-sig" => {
            String::from_utf8(bytes.to_vec()).map_err(|_| "invalid-utf8")?
        }
        "ascii" | "us-ascii" => {
            if !bytes.is_ascii() {
                return Err("invalid-ascii".into());
            }
            String::from_utf8(bytes.to_vec()).unwrap()
        }
        "latin-1" | "latin1" | "iso-8859-1" => bytes.iter().map(|byte| char::from(*byte)).collect(),
        _ => return Err("unsupported-encoding".into()),
    };
    Ok((text, encoding.into(), if bom { 3 } else { 0 }))
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let cases: Value = serde_json::from_str(&input).unwrap();
    let mut output = Vec::new();
    for case in cases["cases"].as_array().unwrap() {
        let id = case["id"].as_str().unwrap();
        let mut bytes = if let Some(hex) = case["source_hex"].as_str() {
            (0..hex.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
                .collect()
        } else {
            case["source"].as_str().unwrap().as_bytes().to_vec()
        };
        if let Some(repeat) = case["repeat"].as_u64() {
            bytes = bytes.repeat(repeat as usize);
        }
        if bytes.len() > 1024 * 1024 {
            output.push(json!({"id": id, "grammar_complete": false, "failure": "source-limit"}));
            continue;
        }
        let (source, encoding, bom_bytes) = match decode(&bytes) {
            Ok(value) => value,
            Err(error) => {
                output.push(json!({"id": id, "grammar_complete": false, "failure": error}));
                continue;
            }
        };
        let starts = line_starts(&source);
        let target = PythonVersion::from_str(case["target"].as_str().unwrap()).unwrap();
        let parsed = parse_unchecked(
            &source,
            ParseOptions::from(Mode::Module).with_target_version(target),
        );
        let tokens: Vec<_> = parsed
            .tokens()
            .iter()
            .take(512)
            .map(|token| {
                let range = token.range();
                let start = range.start().to_usize();
                let end = range.end().to_usize();
                json!({"kind": format!("{:?}", token.kind()), "start": start, "end": end,
                   "text": &source[start..end], "position": position(&source, &starts, start)})
            })
            .collect();
        let errors: Vec<_> = parsed.errors().iter().map(|error| json!({"message": error.to_string(), "start": error.range().start().to_usize(), "end": error.range().end().to_usize()})).collect();
        let unsupported: Vec<_> = parsed
            .unsupported_syntax_errors()
            .iter()
            .map(|error| format!("{error:?}"))
            .collect();
        let mut nodes = Nodes::default();
        nodes.visit_mod(parsed.syntax());
        output.push(json!({"id": id, "grammar_complete": parsed.has_no_syntax_errors(), "syntax_valid": parsed.has_valid_syntax(),
                          "encoding": encoding, "bom_bytes": bom_bytes, "errors": errors, "unsupported": unsupported,
                          "tokens": tokens, "token_count": parsed.tokens().len(), "nodes": nodes.details, "node_counts": nodes.counts}));
    }
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({"results": output})).unwrap()
    );
}
