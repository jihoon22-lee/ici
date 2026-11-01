//! Legacy qmake `ici verify`/`ici test`/`ici sanitize` against the frozen CLI
//! (`tests/rust-parity/cpp/capture_qmake_verify.py`): exit code, suite counters, every engine's
//! verdict, the test/sanitize targets and findings and the verify `analysis_context`; legacy
//! Make `build.make.*test_argv` runs (`tests/rust-parity/cpp/capture_make_suites.py`).
use serde_json::{Value, json};
use std::{fs, path::Path, process::Command};

const TARGET_FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "file_path",
    "start_line",
    "metrics",
];
const FINDING_FIELDS: [&str; 8] = [
    "rule_id",
    "severity",
    "confidence",
    "category",
    "message",
    "primary_location",
    "fingerprint",
    "tool_name",
];
const COUNTERS: [&str; 9] = [
    "suite_status",
    "passed_count",
    "failed_count",
    "error_count",
    "warned_count",
    "skipped_count",
    "total_count",
    "tem_score",
    "max_tem_score",
];

fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

fn pick(value: &Value, fields: &[&str]) -> Value {
    Value::Object(
        fields
            .iter()
            .map(|key| {
                (
                    (*key).to_owned(),
                    value.get(*key).cloned().unwrap_or(Value::Null),
                )
            })
            .collect(),
    )
}

fn run(oracle: &Value, case: &Value, capture: &str) -> (Value, Value) {
    // Frozen cuts adapter messages at 200 characters of absolute-path stderr; a project root as
    // long as the capture's (`/tmp/ici-cpp-qmake-verify/project`) cuts at the same place.
    let work = tempfile::Builder::new()
        .prefix(&format!(
            "{:-<width$}",
            "ici-",
            width = capture.len() - "/tmp//project".len() - 6
        ))
        .rand_bytes(6)
        .tempdir_in("/tmp")
        .unwrap();
    let root = work.path().join("project");
    assert_eq!(root.display().to_string().len(), capture.len());
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp")
            .join(case["fixture"].as_str().unwrap()),
        &root,
    );
    fs::write(
        root.join("ici.toml"),
        format!(
            "{}{}",
            oracle["base"].as_str().unwrap(),
            case["config"].as_str().unwrap()
        ),
    )
    .unwrap();
    for args in [
        &["init", "-q", "."][..],
        &["add", "-A"],
        &["commit", "-qm", "fixture"],
    ] {
        let status = Command::new("git")
            .args([
                "-c",
                "user.email=ici@example.invalid",
                "-c",
                "user.name=ici",
            ])
            .args(args)
            .current_dir(&root)
            .status()
            .unwrap();
        assert!(status.success());
    }
    let command = case["command"].as_str().unwrap();
    // The qmake capture re-enters the ici binary (`__qmake-configure-capture`, `__qmake-record`),
    // so the real CLI runs; the frozen interpreter coerces the C locale to LC_CTYPE=C.UTF-8
    // (PEP 538) for its tools, so g++ quotes the same way in both runs.
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args([command, "--report"])
        .current_dir(&root)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", work.path())
        .env("LC_CTYPE", "C.UTF-8")
        .output()
        .unwrap();
    let text = fs::read_to_string(root.join(format!("{}_report.json", command.replace('-', "_"))))
        .unwrap();
    let report: Value =
        serde_json::from_str(&text.replace(&root.display().to_string(), "<ROOT>")).unwrap();
    let mut engines = serde_json::Map::new();
    let engines_list = if command == "verify" {
        report["results"].as_array().unwrap().clone()
    } else {
        vec![report.clone()]
    };
    for engine in &engines_list {
        let mut view = pick(engine, &["status", "evidence", "summary", "score"]);
        let name = engine["engine_name"].as_str().unwrap();
        if matches!(name, "test" | "sanitize" | "thread_sanitize") {
            view["targets"] = json!(
                engine["targets"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|target| pick(target, &TARGET_FIELDS))
                    .collect::<Vec<_>>()
            );
            view["findings"] = json!(
                engine["findings"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|finding| pick(finding, &FINDING_FIELDS))
                    .collect::<Vec<_>>()
            );
        }
        engines.insert(name.to_owned(), view);
    }
    let actual = json!({
        "exit_code": output.status.code(),
        "counters": if command == "verify" { pick(&report, &COUNTERS) } else { json!({}) },
        "engines": engines,
        "analysis_context": if command == "verify" { report["analysis_context"].clone() } else { Value::Null },
    });
    (actual, report["native_run"]["gate"].clone())
}

fn diff(frozen: &Value, actual: &Value, path: &str, out: &mut Vec<String>) {
    match (frozen, actual) {
        (Value::Object(left), Value::Object(right)) => {
            for key in left
                .keys()
                .chain(right.keys().filter(|key| !left.contains_key(*key)))
            {
                diff(
                    left.get(key).unwrap_or(&Value::Null),
                    right.get(key).unwrap_or(&Value::Null),
                    &format!("{path}/{key}"),
                    out,
                );
            }
        }
        (Value::Array(left), Value::Array(right)) if left.len() == right.len() => {
            for (index, (left, right)) in left.iter().zip(right).enumerate() {
                diff(left, right, &format!("{path}[{index}]"), out);
            }
        }
        (Value::Array(left), Value::Array(right)) => {
            for item in left.iter().filter(|item| !right.contains(item)) {
                out.push(format!("{path} only frozen: {item}"));
            }
            for item in right.iter().filter(|item| !left.contains(item)) {
                out.push(format!("{path} only native: {item}"));
            }
        }
        _ if frozen != actual => {
            out.push(format!("{path}\n  frozen: {frozen}\n  native: {actual}"))
        }
        _ => {}
    }
}

/// Recorded differences, applied to both sides:
/// - frozen `tool_evidence` has no native counterpart: adapted findings carry no `tool_name` and
///   diagnostics no `process_evidence_index` metric;
/// - native keeps met thresholds as `Threshold:` targets; frozen lists only breaches;
/// - a project without production sources has no fabricated coverage/TEM estimate;
/// - complexity: frozen's scanner drops every inline member of a class with a base clause
///   (`class Unit : public QObject`), so it reports 0 functions where native measures the four
///   QTest slots (known correction `base-clause-members`); status and evidence still match;
/// - analysis_context: native shadow paths, digests, sizes and the short-commit version; the
///   qmake capture keeps the compiler invocation alias (`g++`) where frozen resolves it to its
///   realpath, and a shadow inside `.ici/rust-v1/build` makes qmake spell project paths
///   absolutely (frozen `../../../tests`), so unit argv paths are compared project-relative.
fn comparable(mut view: Value, frozen: bool, case: &str) -> Value {
    for engine in view["engines"].as_object_mut().unwrap().values_mut() {
        for list in ["targets", "findings"] {
            if let Some(items) = engine.get_mut(list).and_then(Value::as_array_mut) {
                for item in items {
                    if let Some(item) = item.as_object_mut() {
                        item.remove("tool_name");
                        if let Some(metrics) =
                            item.get_mut("metrics").and_then(Value::as_object_mut)
                        {
                            metrics.remove("process_evidence_index");
                        }
                    }
                }
            }
        }
    }
    if let Some(complexity) = view["engines"].get_mut("complexity") {
        for key in ["summary", "score"] {
            complexity[key] = Value::Null;
        }
    }
    if !frozen && let Some(test) = view["engines"].get_mut("test") {
        let dropped: Vec<Value> = test["targets"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|target| {
                target["target_name"]
                    .as_str()
                    .is_some_and(|name| name.starts_with("Threshold: "))
                    && target["status"] != "WARN"
            })
            .map(|target| target["message"].clone())
            .collect();
        for list in ["targets", "findings"] {
            if let Some(items) = test.get_mut(list).and_then(Value::as_array_mut) {
                items.retain(|item| !dropped.contains(&item["message"]));
            }
        }
    }
    if case == "verify-missing-src" {
        // Root-accepted: no fabricated coverage/TEM estimate (frozen 'Line: 100.0% (est)') when
        // no production source is in scope; the verdict, targets and ESTIMATED evidence match.
        view["engines"]["test"]["summary"] = Value::Null;
        view["engines"]["test"]["score"] = Value::Null;
        view["counters"]["tem_score"] = Value::Null;
    }
    if view["analysis_context"].is_object() {
        view["analysis_context"] = context(view["analysis_context"].take(), frozen);
    }
    view
}

fn digest(value: &Value) -> bool {
    value.as_str().is_some_and(|text| {
        text.strip_prefix("sha256:")
            .is_some_and(|hex| hex.len() == 64 && hex.bytes().all(|b| b.is_ascii_hexdigit()))
    })
}

/// A unit argv path relative to the unit directory (`../../../tests/x.cpp` from
/// `build/ici-qmake-build/tests`) or project-relative already (`tests/x.cpp`).
fn rooted(value: &str, directory: &str, frozen: bool) -> String {
    if !frozen || !value.starts_with("../") {
        return value.to_owned();
    }
    let mut parts: Vec<&str> = directory
        .split('/')
        .filter(|part| !part.is_empty())
        .collect();
    let mut rest = value;
    while let Some(tail) = rest.strip_prefix("../") {
        parts.pop();
        rest = tail;
    }
    parts.push(rest);
    parts.join("/")
}

fn context(mut context: Value, frozen: bool) -> Value {
    let commit = context["identity"]["source_commit"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_eq!(context["project"]["version"], format!("v{}", &commit[..7]));
    context["project"]["version"] = Value::Null;
    for key in ["config_digest", "toolchain_digest"] {
        assert!(digest(&context["identity"][key]), "{key}");
    }
    context["identity"] = Value::Null;
    let compilation = &mut context["compilation"];
    if compilation["database_path"].is_string() {
        assert!(digest(&compilation["database_digest"]));
        compilation["database_path"] = json!("<shadow>");
        compilation["database_digest"] = Value::Null;
    }
    for unit in compilation["units"].as_array_mut().unwrap() {
        assert!(digest(&unit["configuration"]));
        let directory = unit["directory"].as_str().unwrap().to_owned();
        let argv: Vec<Value> = unit["argv"]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| {
                let text = value.as_str().unwrap();
                if text.starts_with("-DQT_TESTCASE_BUILDDIR=") {
                    return json!("-DQT_TESTCASE_BUILDDIR=<shadow>");
                }
                json!(match text.strip_prefix("-I") {
                    Some(path) => format!("-I{}", rooted(path, &directory, frozen)),
                    None => rooted(text, &directory, frozen),
                })
            })
            .collect();
        unit["argv"] = json!(argv);
        for key in ["directory", "output", "configuration", "compiler"] {
            unit[key] = Value::Null;
        }
        for define in unit["defines"].as_array_mut().unwrap() {
            if define["name"] == "QT_TESTCASE_BUILDDIR" {
                define["value"] = Value::Null;
            }
        }
        for path in unit["include_paths"].as_array_mut().unwrap() {
            if path["scope"] == "project" && path["path"] != "tests" {
                path["path"] = json!("<shadow>");
            }
        }
    }
    for manifest in context["artifact_manifests"].as_array_mut().unwrap() {
        assert_eq!(manifest["source_commit"], commit.as_str());
        for key in ["config_digest", "toolchain_digest"] {
            assert!(digest(&manifest[key]), "{key}");
            manifest[key] = Value::Null;
        }
        for key in ["source_commit", "shadow_root"] {
            manifest[key] = Value::Null;
        }
        for artifact in manifest["artifacts"].as_array_mut().unwrap() {
            assert!(digest(&artifact["sha256"]));
            assert!(artifact["size"].as_u64().unwrap() > 0);
            for key in ["command", "sha256", "size"] {
                artifact[key] = Value::Null;
            }
        }
    }
    context
}

/// Positive: a SUBDIRS qmake project whose QTest binary lives in tests/ runs it through
/// `make check TESTARGS=-xunitxml` under coverage and ASan/UBSan: `[C++] test_unit` FAIL with the
/// two failed QTest cases, the frozen coverage targets and `[C++ ASan/UBSan] test_unit` at
/// project.pro (verify, test and sanitize exit 1). Negative: the configured but missing
/// `source_dirs=['src']` still runs the qmake test and sanitizer suites like frozen (FAIL, exit
/// 1) instead of blocking every C++ check. Failure: the failing QTest cases fail the engines.
#[test]
#[ignore = "explicit actual qmake6/make/g++ runs: qmake6 (Qt 6), make and /usr/bin/g++ must be the captured tools"]
fn legacy_qmake_verify_matches_frozen_exit_codes_engine_verdicts_and_context() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/qmake-verify-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let qmake = Command::new("qmake6").arg("--version").output().unwrap();
    assert_eq!(
        String::from_utf8(qmake.stdout).unwrap().lines().next(),
        oracle["qmake"].as_str()
    );
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 4);
    let mut mismatches = vec![];
    for (name, case) in cases {
        let (actual, gate) = run(&oracle, case, "/tmp/ici-cpp-qmake-verify/project");
        // The native gate keeps C++ checks refused for an empty configured scope as incomplete
        // evidence; the legacy verdicts and exit code are frozen's.
        let unscoped = gate["reasons"].as_array().is_some_and(|reasons| {
            reasons.iter().all(|reason| {
                reason
                    .as_str()
                    .is_some_and(|reason| reason.ends_with(": no sources in scope for cpp"))
            })
        });
        if gate["selected"] == "INCOMPLETE" && !(name == "verify-missing-src" && unscoped) {
            eprintln!("== {name}: incomplete gate {gate}");
            mismatches.push(name.clone());
        }
        let actual = comparable(actual, false, name);
        let expected = comparable(case["frozen"].clone(), true, name);
        if actual != expected {
            let mut lines = vec![];
            diff(&expected, &actual, "", &mut lines);
            eprintln!("== {name}\n{}", lines.join("\n"));
            mismatches.push(name.clone());
        }
        assert_eq!(case["frozen"]["exit_code"], 1, "{name}");
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}

/// Positive: a legacy `[build.make]` backend runs its configured `test_argv` in the workdir and
/// frozen `parse_make_check_stdout` names each `./<test>` invocation ('[C++] test_calc' at
/// tests/test_calc.cpp), with gcov coverage over the Make shadow; the coverage variant's
/// `coverage_test_argv` and the sanitizer variant's `sanitize_test_argv` replace `test_argv`
/// like frozen `make_plan`. Failure: a `make: *** ... Error` line fails the started test and the
/// engines (exit 1); the passing sanitizer run is the frozen clean PASS (exit 0).
#[test]
#[ignore = "explicit actual make/g++ runs: GNU make and /usr/bin/g++ must be the captured tools"]
fn legacy_make_test_argv_runs_match_frozen_targets_findings_and_exit_codes() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/make-test-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let make = Command::new("make").arg("--version").output().unwrap();
    assert_eq!(
        String::from_utf8(make.stdout).unwrap().lines().next(),
        oracle["make"].as_str()
    );
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 5);
    let mut mismatches = vec![];
    for (name, case) in cases {
        let (actual, gate) = run(&oracle, case, "/tmp/ici-cpp-make-test/project");
        if gate["selected"] == "INCOMPLETE" {
            eprintln!("== {name}: incomplete gate {gate}");
            mismatches.push(name.clone());
        }
        let actual = comparable(actual, false, name);
        let expected = comparable(case["frozen"].clone(), true, name);
        if actual != expected {
            let mut lines = vec![];
            diff(&expected, &actual, "", &mut lines);
            eprintln!("== {name}\n{}", lines.join("\n"));
            mismatches.push(name.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
