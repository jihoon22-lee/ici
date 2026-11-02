//! Frozen LintEngine `extra` counters and `tool_evidence` comparison shared by the legacy lint
//! replays (`#[path = "support/lint_extra.rs"] mod lint_extra;`).
use serde_json::Value;

/// Clang tool records whose argv native executes exactly as frozen.
const EXACT_ARGV: [&str; 2] = ["clang-tidy", "clazy"];

/// Remove `extra` and `tool_evidence` from both captures and describe every difference:
/// each frozen `extra` key must be present and equal (native-only keys such as `native_checks`
/// are ignored). Tool evidence must list the same records in order with equal name, version,
/// returncode, error, timed_out and truncated; clang-tidy / clazy records also equal path and
/// argv. Intended difference: the g++ syntax replays and `g++ stdlib include search` probes are
/// native's own executed commands (native driver path, flag order and column options, the
/// search reads `/dev/null` instead of stdin), so their argv/path are not frozen's; each must
/// be a non-empty argv whose program is the record's path.
pub fn split(actual: &mut Value, frozen: &mut Value) -> Vec<String> {
    let mut differences = vec![];
    let take = |value: &mut Value, key: &str| {
        value
            .as_object_mut()
            .and_then(|object| object.remove(key))
            .unwrap_or(Value::Null)
    };
    let (native_extra, frozen_extra) = (take(actual, "extra"), take(frozen, "extra"));
    let (native_tools, frozen_tools) =
        (take(actual, "tool_evidence"), take(frozen, "tool_evidence"));
    if frozen_extra.is_null() {
        return differences;
    }
    for (key, value) in frozen_extra.as_object().into_iter().flatten() {
        if native_extra.get(key) != Some(value) {
            differences.push(format!(
                "extra.{key}: native {} frozen {value}",
                native_extra.get(key).unwrap_or(&Value::Null)
            ));
        }
    }
    let native_tools = native_tools.as_array().cloned().unwrap_or_default();
    let frozen_tools = frozen_tools.as_array().cloned().unwrap_or_default();
    let names =
        |tools: &[Value]| -> Vec<Value> { tools.iter().map(|tool| tool["name"].clone()).collect() };
    if names(&native_tools) != names(&frozen_tools) {
        differences.push(format!(
            "tool_evidence names: native {:?} frozen {:?}",
            names(&native_tools),
            names(&frozen_tools)
        ));
        return differences;
    }
    for (index, (native, frozen)) in native_tools.iter().zip(&frozen_tools).enumerate() {
        let exact = EXACT_ARGV.contains(&frozen["name"].as_str().unwrap_or_default());
        let mut keys = vec!["version", "returncode", "error", "timed_out", "truncated"];
        if exact {
            keys.extend(["path", "argv"]);
        } else if !frozen["argv"].as_array().is_some_and(Vec::is_empty) {
            let executed = native["argv"]
                .as_array()
                .and_then(|argv| argv.first())
                .is_some_and(|program| *program == native["path"]);
            if !executed {
                differences.push(format!("tool_evidence[{index}] argv: {native}"));
            }
        }
        for key in keys {
            if native[key] != frozen[key] {
                differences.push(format!(
                    "tool_evidence[{index}].{key}: native {} frozen {}",
                    native[key], frozen[key]
                ));
            }
        }
    }
    differences
}

#[allow(dead_code)]
/// The engine's `extra` and `tool_evidence` with each `(path, placeholder)` replaced, as the
/// frozen captures store them.
pub fn captured(engine: &Value, replacements: &[(String, &str)]) -> (Value, Value) {
    let mut text = serde_json::to_string(&(&engine["extra"], &engine["tool_evidence"])).unwrap();
    for (path, placeholder) in replacements {
        text = text.replace(path.as_str(), placeholder);
    }
    serde_json::from_str(&text).unwrap()
}

#[allow(dead_code)]
/// Replace `path` by `placeholder` in a capture's `extra` and `tool_evidence`.
pub fn relabel(capture: &mut Value, path: &str, placeholder: &str) {
    for key in ["extra", "tool_evidence"] {
        let text = capture[key].to_string().replace(path, placeholder);
        capture[key] = serde_json::from_str(&text).unwrap();
    }
}
