//! Legacy CMake `ici verify` verdicts against the frozen CLI
//! (`tests/rust-parity/cpp/capture_cmake_verify.py`): exit code, suite counters, every engine's
//! verdict and the test/sanitize targets and findings.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, process::Command};

#[path = "support/host_tools.rs"]
mod host_tools;

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

fn run(oracle: &Value, case: &Value, path: &str) -> (Value, Value) {
    // Frozen cuts adapter messages at 200 characters of absolute-path stderr; a project root as
    // long as the capture's (`/tmp/ici-cpp-cmake-verify/project`) cuts at the same place.
    let work = tempfile::Builder::new()
        .prefix("ici-cmake-vfy-")
        .rand_bytes(6)
        .tempdir_in("/tmp")
        .unwrap();
    let root = work.path().join("project");
    assert_eq!(
        root.display().to_string().len(),
        "/tmp/ici-cpp-cmake-verify/project".len()
    );
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
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    // No locale is set: the frozen interpreter coerced the C locale to LC_CTYPE=C.UTF-8 (PEP 538)
    // for its tools and the stable commands do the same (root ruling 2026-11-14 (b6(b))), so g++
    // quotes the same way in both runs.
    let environment = BTreeMap::from([
        ("PATH".to_string(), path.to_string()),
        ("HOME".to_string(), work.path().display().to_string()),
    ]);
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
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
        "exit_code": output.exit_code,
        "counters": if command == "verify" { pick(&report, &COUNTERS) } else { json!({}) },
        "engines": engines,
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
/// - a failed coverage build has no fabricated ESTIMATED coverage/TEM (frozen 'Line: 45.0%
///   (est)'): the test engine keeps the frozen targets/findings/status, its own summary, NOT_RUN
///   evidence (so `low`, not `medium`, finding confidence) and no score; the suite TEM is null.
///
/// The adapted findings' `tool_name` and the diagnostics' `process_evidence_index` are compared
/// (cpp36 removed the stale mask): execute.rs legacy_runtime_tool_evidence records frozen's
/// backend selection, the session's cmake processes and the ctest run in frozen's order.
fn comparable(mut view: Value, case: &str) -> Value {
    if case == "make-unadapted" {
        // The same refusal to fabricate: frozen's fixed 85%/95% estimate for a direct build whose
        // only production source defines main; the verdict class and ESTIMATED evidence match.
        view["engines"]["test"]["summary"] = Value::Null;
        view["engines"]["test"]["score"] = Value::Null;
        view["counters"]["tem_score"] = Value::Null;
    }
    if case == "broken-build" {
        let test = &mut view["engines"]["test"];
        for key in ["summary", "evidence", "score"] {
            test[key] = Value::Null;
        }
        for finding in test["findings"].as_array_mut().unwrap() {
            finding["confidence"] = Value::Null;
        }
        view["counters"]["tem_score"] = Value::Null;
    }
    view
}

/// Positive: a CMake suite with passing, failing, crashing, DISABLED and SKIP_RETURN_CODE=77
/// cases runs every executable case under ASan/UBSan; the two cases that did not run are frozen
/// per-case ERROR scopes (engine ERROR, NOT_RUN) and the suite exits 1, not 3 (the plain suite
/// exits 1 with a FAIL verdict). Negative: `engines.sanitize.required = false` turns them into
/// SKIP scopes (partial FAIL, ESTIMATED). Failure: a test that does not compile fails the
/// coverage and sanitizer builds (`[C++] build` FAIL, `C++Sanitizer` ERROR) while the
/// configured release database still serves compile_db/lint/dead, and the suite exits 1.
/// `ici thread-sanitize` on the suite adds the TSan labels and a report without a project frame
/// (frozen `SanitizerDiagnosticLocation` ERROR at `[external]`, exit 1). A descriptor-free project
/// and one with an unadapted Makefile compile their tests with direct g++ (exit 0) without that
/// test compile becoming the lint/compile_db analysis context.
#[test]
#[ignore = "explicit actual CMake/ctest/g++ runs: /usr/bin/cmake (4.2.3), ctest and /usr/bin/g++ must be the captured tools"]
fn legacy_cmake_verify_matches_frozen_exit_codes_and_engine_verdicts() {
    let oracle = oracle();
    let cmake = Command::new("cmake").arg("--version").output().unwrap();
    assert_eq!(
        String::from_utf8(cmake.stdout).unwrap().lines().next(),
        oracle["cmake"].as_str()
    );
    replay(&oracle, "/usr/bin:/bin", false);
}

/// The CI twin with the host's own cmake/ctest/g++/gcov and sanitizer runtimes (any release, as
/// on the GitHub runner) behind a mirror of `/usr/bin` and `/bin` without clang*/clazy* (the
/// capture host had none; `support/host_tools.rs`). Incidental masks (`host_view`): what the
/// GCC release itself writes or measures — the test/sanitize engines' summaries, scores, target
/// messages and metrics, finding messages, fingerprints and locations (sanitizer report text,
/// gcov line counts) and the TEM counters. Every exit code, suite counter, engine verdict and
/// evidence, target name/status/path and finding rule/severity/confidence stays compared.
#[test]
fn legacy_cmake_verify_matches_frozen_exit_codes_and_engine_verdicts_with_the_host_toolchain() {
    let host = host_tools::mirror(&host_tools::NO_LLVM);
    replay(&oracle(), &host.path, true);
}

fn host_view(mut view: Value) -> Value {
    for key in ["tem_score", "max_tem_score"] {
        view["counters"][key] = json!("<host>");
    }
    for name in ["test", "sanitize", "thread_sanitize"] {
        let Some(engine) = view["engines"].get_mut(name) else {
            continue;
        };
        for key in ["summary", "score"] {
            engine[key] = json!("<host>");
        }
        for target in engine["targets"].as_array_mut().into_iter().flatten() {
            for key in ["message", "metrics", "start_line"] {
                target[key] = json!("<host>");
            }
        }
        for finding in engine["findings"].as_array_mut().into_iter().flatten() {
            for key in ["message", "fingerprint", "primary_location"] {
                finding[key] = json!("<host>");
            }
        }
    }
    view
}

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/cmake-verify-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

fn replay(oracle: &Value, path: &str, host: bool) {
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 8);
    let mut mismatches = vec![];
    for (name, case) in cases {
        let (actual, gate) = run(oracle, case, path);
        // Only a run with an incomplete required check may exit 3; none of these is incomplete.
        assert_ne!(gate["selected"], "INCOMPLETE", "{name}: {gate}");
        let mut actual = comparable(actual, name);
        let mut expected = comparable(case["frozen"].clone(), name);
        if host {
            (actual, expected) = (host_view(actual), host_view(expected));
        }
        if actual != expected {
            let mut lines = vec![];
            diff(&expected, &actual, "", &mut lines);
            eprintln!("== {name}\n{}", lines.join("\n"));
            mismatches.push(name.clone());
        }
    }
    // The frozen exits these fixtures pin: never the incomplete class.
    for (name, code) in [
        ("plain", 1),
        ("suite", 1),
        ("suite-sanitize-optional", 1),
        ("broken-build", 1),
        ("suite-thread-sanitize", 1),
        ("suite-thread-sanitize-optional", 1),
        ("direct-tests", 0),
        ("make-unadapted", 0),
    ] {
        assert_eq!(cases[name]["frozen"]["exit_code"], code, "{name}");
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
