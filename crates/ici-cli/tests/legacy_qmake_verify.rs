//! Legacy qmake `ici verify`/`ici test`/`ici sanitize` against the frozen CLI
//! (`tests/rust-parity/cpp/capture_qmake_verify.py`): exit code, suite counters, every engine's
//! verdict, the test/sanitize targets and findings and the verify `analysis_context`; legacy
//! Make `build.make.*test_argv` runs (`tests/rust-parity/cpp/capture_make_suites.py`).
use serde_json::{Value, json};
use std::{fs, path::Path, process::Command};

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

fn run(oracle: &Value, case: &Value, capture: &str, path: &str) -> (Value, Value) {
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
            case["base"].as_str().or(oracle["base"].as_str()).unwrap(),
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
            // The capture's fixed dates give the same commit id (a deep build's version).
            .env("GIT_AUTHOR_DATE", "2026-01-01T00:00:00+00:00")
            .env("GIT_COMMITTER_DATE", "2026-01-01T00:00:00+00:00")
            .env("HOME", work.path())
            .status()
            .unwrap();
        assert!(status.success());
    }
    let command = case["command"].as_str().unwrap();
    // The qmake capture re-enters the ici binary (`__qmake-configure-capture`, `__qmake-record`),
    // so the real CLI runs. No locale is set: the frozen interpreter coerced the C locale to
    // LC_CTYPE=C.UTF-8 (PEP 538) for its tools and the stable commands do the same (root ruling
    // 2026-11-14 (b6(b))), so g++ quotes the same way in both runs.
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args([command, "--report"])
        .current_dir(&root)
        .env_clear()
        .env("PATH", path)
        .env("HOME", work.path())
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
/// - a project without production sources has no fabricated coverage/TEM estimate;
/// - analysis_context: native shadow paths, digests, sizes and the short-commit version; the
///   unit's compiler is the realpath name like frozen (the replayed argv keeps the invocation
///   alias), and a shadow inside `.ici/rust-v1/build` makes qmake spell project paths
///   absolutely (frozen `../../../tests`), so unit argv paths are compared project-relative.
///
/// Compared in full (cpp36 removed the stale masks): the adapted findings' `tool_name` (frozen's
/// first `tool_evidence` record, `build backend selection: ...`, or the sanitizer's own tool) and
/// the diagnostics' `process_evidence_index` (execute.rs legacy_runtime_tool_evidence records the
/// backend selection, the session processes and the test command like frozen `run_tests`);
/// complexity's summary and score (legacy complexity takes every function from the ported frozen
/// inventory, so the `base-clause-members` correction no longer applies); and the test engine's
/// `Threshold:` targets (frozen `_threshold_breaches`: a met threshold is no target, as
/// execute.rs `legacy_threshold_breaches` drops it).
fn comparable(mut view: Value, frozen: bool, case: &str) -> Value {
    if case == "verify-missing-src" {
        // Root-accepted: no fabricated coverage/TEM estimate (frozen 'Line: 100.0% (est)') when
        // no production source is in scope; the verdict, targets and ESTIMATED evidence match.
        view["engines"]["test"]["summary"] = Value::Null;
        view["engines"]["test"]["score"] = Value::Null;
        view["counters"]["tem_score"] = Value::Null;
    }
    if case == "verify-shared-shadow-sanitize" {
        // Intended difference: every legacy Make variant shares the one configured shadow, so
        // the sanitize build after the coverage build is a no-op and frozen "sanitizes" the
        // uninstrumented coverage binaries (identical sha256 in both manifests) and reports
        // "Memory Safety & Sanitize Clean". Rust probes the declared executables for their
        // sanitizer runtime and refuses that claim: the engine stays SKIP/NOT_RUN and the
        // required evidence incomplete (suite ERROR, exit 3). Everything else matches.
        let sanitize = view["engines"]["sanitize"].take();
        if frozen {
            assert_eq!(sanitize["status"], "PASS");
            let manifests = view["analysis_context"]["artifact_manifests"]
                .as_array()
                .unwrap();
            let digests = |variant: &str| {
                manifests
                    .iter()
                    .find(|manifest| manifest["variant"] == variant)
                    .map(|manifest| manifest["artifacts"][0]["sha256"].clone())
            };
            assert_eq!(digests("coverage"), digests("sanitize"));
        } else {
            assert_eq!(sanitize["status"], "SKIP");
            assert_eq!(sanitize["evidence"], "NOT_RUN");
            assert_eq!(
                sanitize["summary"],
                "declared sanitizer variant lacks its native runtime symbols"
            );
            assert_eq!(view["exit_code"], 3);
            assert_eq!(view["counters"]["suite_status"], "ERROR");
        }
        view["exit_code"] = Value::Null;
        for key in ["suite_status", "passed_count", "skipped_count"] {
            view["counters"][key] = Value::Null;
        }
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
        for key in ["directory", "output", "configuration"] {
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
    let oracle = oracle("qmake-verify-oracle.json");
    let qmake = Command::new("qmake6").arg("--version").output().unwrap();
    assert_eq!(
        String::from_utf8(qmake.stdout).unwrap().lines().next(),
        oracle["qmake"].as_str()
    );
    replay_qmake(&oracle, "/usr/bin:/bin", false);
}

/// The CI twin with the host's own qmake6/make/g++/gcov and sanitizer runtimes (any release, as
/// on the GitHub runner) behind a mirror of `/usr/bin` and `/bin` without clang*/clazy* (the
/// capture host had none; `support/host_tools.rs`). Incidental masks (`host_view`): what the Qt
/// and GCC releases write — each captured unit's argv, defines and include paths (Qt module
/// directories and macros) and the test/sanitize text and measurements. Exit codes, counters,
/// verdicts, evidence, target names/statuses/paths, finding rules and the context's database
/// origin/generator/diagnostics/unit sources stay compared.
#[test]
fn legacy_qmake_verify_matches_frozen_with_the_host_qmake6() {
    if host_tools::version(Path::new("/usr/bin/qmake6")).is_none() {
        println!("no /usr/bin/qmake6 on this host: the qmake twin did not run");
        return;
    }
    let host = host_tools::mirror(&host_tools::NO_LLVM);
    replay_qmake(&oracle("qmake-verify-oracle.json"), &host.path, true);
}

fn oracle(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/rust-parity/cpp")
        .join(name);
    let oracle: Value = serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
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
    if let Some(units) = view["analysis_context"]["compilation"]["units"].as_array_mut() {
        for unit in units {
            for key in ["argv", "defines", "include_paths"] {
                unit[key] = json!("<host>");
            }
        }
    }
    view
}

fn replay_qmake(oracle: &Value, path: &str, host: bool) {
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 4);
    let mut mismatches = vec![];
    for (name, case) in cases {
        let (actual, gate) = run(oracle, case, "/tmp/ici-cpp-qmake-verify/project", path);
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
        let mut actual = comparable(actual, false, name);
        let mut expected = comparable(case["frozen"].clone(), true, name);
        if host {
            (actual, expected) = (host_view(actual), host_view(expected));
        }
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
    let oracle = oracle("make-test-oracle.json");
    let make = Command::new("make").arg("--version").output().unwrap();
    assert_eq!(
        String::from_utf8(make.stdout).unwrap().lines().next(),
        oracle["make"].as_str()
    );
    replay_make(&oracle, "/usr/bin:/bin", false);
}

/// The CI twin of the Make replay with the host's own make/g++/gcov (any release) behind the
/// clang*/clazy*-free mirror, with the same incidental `host_view` masks.
#[test]
fn legacy_make_test_argv_runs_match_frozen_with_the_host_make() {
    let host = host_tools::mirror(&host_tools::NO_LLVM);
    replay_make(&oracle("make-test-oracle.json"), &host.path, true);
}

fn replay_make(oracle: &Value, path: &str, host: bool) {
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 23);
    let mut mismatches = vec![];
    // Each legacy engine-key case changes the frozen engine view of the case it varies, so the
    // byte-equal native replay below proves the key is consumed (not merely accepted).
    for (name, case) in cases {
        if let Some(reference) = case["reference"].as_str() {
            assert_ne!(
                case["frozen"]["engines"], cases[reference]["frozen"]["engines"],
                "{name}: no frozen effect"
            );
        }
    }
    for (name, case) in cases {
        let (actual, gate) = run(oracle, case, "/tmp/ici-cpp-make-test/project", path);
        if gate["selected"] == "INCOMPLETE" && name != "verify-shared-shadow-sanitize" {
            eprintln!("== {name}: incomplete gate {gate}");
            mismatches.push(name.clone());
        }
        let mut actual = comparable(actual, false, name);
        let mut expected = comparable(case["frozen"].clone(), true, name);
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
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
