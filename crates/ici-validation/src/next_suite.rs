//! Frozen next C++ suite contract (`adapters/providers/cpptest.py`, `identity.py`) for the
//! per-suite steps of `application/testing.expand_cpp_tests`: the step identity, the
//! `process_fingerprint` finding identity and the parse-failure wording.
use crate::{ValidationPlan, ValidationRequest};
use std::path::{Component, Path, PathBuf};

/// The suite step of a C++ test plan: frozen's `<task>.<suite id>` when declared, else
/// native's `<task>.suite`.
pub(crate) fn step_id(request: &ValidationRequest) -> String {
    request
        .options
        .suite_step_id
        .clone()
        .unwrap_or_else(|| format!("{}.suite", request.task_id))
}

/// Frozen `process_fingerprint(provider, spec, root, *details)`: sha256 of the compact JSON
/// (`ensure_ascii`) of `[provider, cwd, argv, details]`, where the cwd and each argument naming
/// a path under the workspace are spelled `${workspace}/…` (`invocation_identity`).
pub(crate) fn process_fingerprint(
    plan: &ValidationPlan,
    provider: &str,
    details: &[&str],
) -> String {
    let step = step_id(&plan.context);
    let root = &plan.context.root;
    let (cwd, argv) = plan
        .tasks
        .iter()
        .find(|task| task.id == step)
        .map(|task| (task.cwd.clone(), task.argv.clone()))
        .unwrap_or_default();
    let payload = serde_json::json!([
        provider,
        relative(&cwd, root),
        argv.iter()
            .map(|value| argument(value, root))
            .collect::<Vec<_>>(),
        details,
    ]);
    ici_domain::sha256(ensure_ascii(&payload.to_string()))
}

/// Frozen `_argument`: `-key=value` and joined path options keep the option spelling.
fn argument(value: &str, root: &Path) -> String {
    if value.starts_with('-')
        && let Some((key, argument)) = value.split_once('=')
    {
        let quoted = argument.len() > 1
            && argument.starts_with(['"', '\''])
            && argument.as_bytes().first() == argument.as_bytes().last();
        let argument = if quoted {
            let quote = &argument[..1];
            format!(
                "{quote}{}{quote}",
                relative(&argument[1..argument.len() - 1], root)
            )
        } else {
            relative(argument, root)
        };
        return format!("{key}={argument}");
    }
    for option in [
        "-isystem",
        "-idirafter",
        "-iquote",
        "-include",
        "-imacros",
        "-isysroot",
        "-I",
        "-L",
        "-F",
        "-B",
        "@",
    ] {
        if let Some(rest) = value.strip_prefix(option) {
            return format!("{option}{}", relative(rest, root));
        }
    }
    relative(value, root)
}

/// Frozen `_relative`: an absolute path under the root (after `normpath`) becomes
/// `${workspace}` or `${workspace}/<posix relative>`; anything else is kept as written.
fn relative(value: &str, root: &Path) -> String {
    let path = Path::new(value);
    if !path.is_absolute() {
        return value.into();
    }
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                normalized.pop();
            }
            other => normalized.push(other.as_os_str()),
        }
    }
    match normalized.strip_prefix(root) {
        Ok(rest) if rest.as_os_str().is_empty() => "${workspace}".into(),
        Ok(rest) => format!("${{workspace}}/{}", rest.to_string_lossy()),
        Err(_) => value.into(),
    }
}

/// Python `json.dumps(..., ensure_ascii=True)`: every non-ASCII character is a `\uXXXX`
/// escape (UTF-16 units, lowercase hex); serde_json already escapes control characters the
/// same way.
fn ensure_ascii(text: &str) -> String {
    let mut escaped = String::with_capacity(text.len());
    for character in text.chars() {
        if character.is_ascii() {
            escaped.push(character);
        } else {
            let mut units = [0u16; 2];
            for unit in character.encode_utf16(&mut units) {
                escaped.push_str(&format!("\\u{unit:04x}"));
            }
        }
    }
    escaped
}

/// Frozen `_parse_ctest` without a verdict line.
pub(crate) fn ctest_without_verdicts(text: &str) -> &'static str {
    if text.to_ascii_lowercase().contains("no tests were found") {
        "ctest found no tests"
    } else {
        "ctest output held no test verdicts"
    }
}

/// Frozen `_parse_qtest` without a case verdict.
pub(crate) fn qtest_without_verdicts(text: &str) -> &'static str {
    let totals =
        regex::Regex::new(r"Totals:\s*(\d+)\s+passed,\s*(\d+)\s+failed").expect("constant regex");
    match totals.captures(text) {
        Some(found) if &found[1] == "0" && &found[2] == "0" => "qtest binary ran no cases",
        _ => "qtest output held no case verdicts",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Python 3.10 `json.dumps(("ctest", "${workspace}", ("${workspace}/tools/ctest",
    /// "--test-dir", "${workspace}/out", "--output-on-failure"), ("bad", "***Failed")),
    /// separators=(",", ":"))` and the same with a non-ASCII detail.
    #[test]
    fn payload_matches_python_compact_ascii_json() {
        let root = Path::new("/w/project");
        let argv: Vec<String> = [
            "/w/project/tools/ctest",
            "--test-dir",
            "/w/project/./out",
            "-I/w/project/inc",
            "--x=/w/project/a",
            "/usr/bin/ctest",
        ]
        .iter()
        .map(|value| argument(value, root))
        .collect();
        let payload = serde_json::json!([
            "ctest",
            relative("/w/project", root),
            argv,
            ["bad", "***Failed \u{e9}\u{1f600}"],
        ]);
        assert_eq!(
            ensure_ascii(&payload.to_string()),
            r#"["ctest","${workspace}",["${workspace}/tools/ctest","--test-dir","${workspace}/out","-I${workspace}/inc","--x=${workspace}/a","/usr/bin/ctest"],["bad","***Failed \u00e9\ud83d\ude00"]]"#
        );
        assert_eq!(
            qtest_without_verdicts("Totals: 0 passed, 0 failed, 0 skipped"),
            "qtest binary ran no cases"
        );
        assert_eq!(
            qtest_without_verdicts("garbage"),
            "qtest output held no case verdicts"
        );
        assert_eq!(
            ctest_without_verdicts("No tests were found!!!"),
            "ctest found no tests"
        );
        assert_eq!(
            ctest_without_verdicts(""),
            "ctest output held no test verdicts"
        );
    }
}
