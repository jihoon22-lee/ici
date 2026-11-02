//! Legacy `ici verify` on the ici-owned quality-zoo C++ scenarios against frozen v0.13.0 runs
//! (`tests/rust-parity/cpp/zoo-cpp-oracle.json`, `capture_zoo_cpp.py`): exit code, suite status
//! and every engine's status, evidence, summary, targets and findings. The zoo expectations are
//! not read; the scenario trees are copied like the zoo runner does (no git checkout).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, process::Command};

fn copy(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        if entry.file_name() == "expectations" {
            continue;
        }
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

/// Recorded difference: a legacy C++ syntax or clazy target of a clean unit is named by its
/// compilation configuration digest, which hashes the unit's directory and argv; native owned
/// CMake shadows live under `.ici/rust-v1/build/` (frozen `build/ici-cmake-build`), so the
/// 12-digit suffix differs while the target, its file and message are frozen's.
fn masked(name: &str) -> String {
    for prefix in ["C++Syntax:", "Clazy:"] {
        if let Some(suffix) = name.strip_prefix(prefix)
            && suffix.len() == 12
            && suffix.bytes().all(|b| b.is_ascii_hexdigit())
        {
            return format!("{prefix}<configuration>");
        }
    }
    name.into()
}

/// Pending root ruling (cpp.json check:cpp.coverage, check:cpp.sanitize, check:cpp.tsan rows),
/// not an accepted difference: native never runs frozen's `cmake --version` minimum-version
/// probe, and it writes every GCOV report with one `gcov` process over all notes files where
/// frozen ran one `gcov` per object directory. The frozen records are compared without the
/// version probe and with consecutive `gcov` runs as one (their return codes all 0).
fn frozen_session_evidence(records: &Value) -> Value {
    let mut kept: Vec<Value> = vec![];
    for record in records.as_array().unwrap() {
        if record["name"] == "cmake --version" {
            continue;
        }
        if record["name"] == "gcov" && kept.last().is_some_and(|last| last["name"] == "gcov") {
            assert_eq!(record["returncode"], 0);
            continue;
        }
        kept.push(record.clone());
    }
    json!(kept)
}

/// The legacy adapter build and binary_compat engines carry frozen's tool evidence natively too:
/// the backend selection record, the `make clean`/`make build` processes and one `readelf binary
/// compatibility` process per artifact, so their adapted findings name the same tool. Each
/// engine's tool-evidence names and return codes are compared (argv paths name each product's
/// own shadow tree).
const OPEN_TOOL_EVIDENCE: [(&str, &str); 0] = [];

/// Recorded (finding_site cycles.py:98:12 / compiler.rs): the native legacy cycle evidence names
/// each `compiler include trace` and its compiler but carries no return code.
fn evidence(engine: &str, records: &Value) -> Value {
    json!(
        records
            .as_array()
            .unwrap()
            .iter()
            .map(|record| {
                let returncode = if engine == "cycle" {
                    Value::Null
                } else {
                    record["returncode"].clone()
                };
                json!({"name": record["name"], "returncode": returncode})
            })
            .collect::<Vec<_>>()
    )
}

/// Frozen sorts an engine's findings by fingerprint, and the clean-unit syntax finding's
/// fingerprint hashes its configuration digest (see `masked`), so findings are compared in a
/// fingerprint-free order.
fn ordered(findings: &mut Value) {
    findings
        .as_array_mut()
        .unwrap()
        .sort_by_key(|finding| finding.to_string());
}

/// The engine `extra` fields the zoo expectations compare (`capture_zoo_cpp.py` EXTRA_KEYS).
const EXTRA_KEYS: [&str; 22] = [
    "sanitize_issues",
    "thread_sanitize_issues",
    "configurations",
    "coverage_percent",
    "covered_units",
    "database_path",
    "issues_count",
    "production_units",
    "clazy_mode",
    "clazy_profile",
    "cpp_diagnostic_categories",
    "cpp_diagnostic_category_policy",
    "cpp_diagnostic_families",
    "branch_coverage",
    "coverage_source",
    "function_coverage",
    "max_complexity",
    "total_functions",
    "cpp_unused_functions_count",
    "cpp_unused_mode",
    "clone_groups_count",
    "analysis_provenance",
];

/// Root ruling (cpp.json intended difference): a legacy CMake analysis database lives in the
/// native build namespace `.ici/rust-v1/build/…` where frozen wrote `build/ici-cmake-build/…`.
fn database_path(extra: &mut Value) {
    if let Some(path) = extra.get_mut("database_path")
        && path.as_str().is_some_and(|path| {
            path.starts_with(".ici/rust-v1/build/") || path.starts_with("build/ici-cmake-build/")
        })
    {
        *path = json!("<legacy CMake analysis database>");
    }
}

fn engines(report: &Value) -> Value {
    let mut engines = serde_json::Map::new();
    for engine in report["results"].as_array().unwrap() {
        let targets: Vec<Value> = engine["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| {
                json!({
                    "target_name": masked(target["target_name"].as_str().unwrap()),
                    "status": target["status"], "message": target["message"],
                    "file_path": target["file_path"], "start_line": target["start_line"],
                })
            })
            .collect();
        let mut findings: Value = engine["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                json!({
                    "rule_id": finding["rule_id"], "message": finding["message"],
                    "severity": finding["severity"], "confidence": finding["confidence"],
                    "category": finding["category"], "tool_name": finding["tool_name"],
                    "tool_rule_id": masked(finding["tool_rule_id"].as_str().unwrap()),
                    "path": finding["primary_location"]["path"],
                    "start_line": finding["primary_location"]["start_line"],
                })
            })
            .collect::<Vec<_>>()
            .into();
        ordered(&mut findings);
        let name = engine["engine_name"].as_str().unwrap();
        let mut extra = json!(
            EXTRA_KEYS
                .iter()
                .filter_map(|key| Some(((*key).to_owned(), engine["extra"].get(*key)?.clone())))
                .collect::<serde_json::Map<_, _>>()
        );
        database_path(&mut extra);
        engines.insert(
            name.to_owned(),
            json!({
                "status": engine["status"], "evidence": engine["evidence"],
                "summary": engine["summary"], "extra": extra, "targets": targets,
                "findings": findings,
                // Frozen `_threshold_breaches` metrics: the reported (rounded) actual value.
                "threshold_metrics": engine["targets"].as_array().unwrap().iter()
                    .filter(|target| target["target_name"].as_str().unwrap().starts_with("Threshold: "))
                    .map(|target| target["metrics"].clone())
                    .collect::<Vec<_>>(),
                "tool_evidence": evidence(name, &engine["tool_evidence"]),
            }),
        );
    }
    Value::Object(engines)
}

fn frozen_engines(name: &str, case: &Value) -> Value {
    let mut engines = case["engines"].clone();
    for (engine_name, engine) in engines.as_object_mut().unwrap() {
        database_path(&mut engine["extra"]);
        for target in engine["targets"].as_array_mut().unwrap() {
            target["target_name"] = json!(masked(target["target_name"].as_str().unwrap()));
        }
        let open = OPEN_TOOL_EVIDENCE.contains(&(name, engine_name.as_str()));
        if open {
            assert!(!engine["tool_evidence"].as_array().unwrap().is_empty());
        }
        for finding in engine["findings"].as_array_mut().unwrap() {
            finding["tool_rule_id"] = json!(masked(finding["tool_rule_id"].as_str().unwrap()));
            if open {
                finding["tool_name"] = json!("");
            }
        }
        ordered(&mut engine["findings"]);
        engine["tool_evidence"] = if open {
            json!([])
        } else {
            evidence(
                engine_name,
                &frozen_session_evidence(&engine["tool_evidence"]),
            )
        };
    }
    engines
}

/// Scenarios that need the clang tools: the captured LLVM 21.1.8 clang-tidy and clazy through
/// wrappers that set their library path, exactly like the capture.
const CLANG_TOOLS: [&str; 1] = ["qt-missing-parent-constructor"];

fn clang_wrappers(directory: &Path) -> std::path::PathBuf {
    use std::os::unix::fs::PermissionsExt;
    fs::create_dir_all(directory).unwrap();
    let library =
        std::env::var("ICI_TEST_CLANG_LIBRARY_PATH").expect("ICI_TEST_CLANG_LIBRARY_PATH");
    for (name, variable) in [
        ("clang-tidy", "ICI_TEST_CLANG_TIDY"),
        ("clazy-standalone", "ICI_TEST_CLAZY"),
    ] {
        let tool = std::env::var(variable).unwrap_or_else(|_| panic!("{variable}"));
        let wrapper = directory.join(name);
        fs::write(
            &wrapper,
            format!("#!/bin/sh\nLD_LIBRARY_PATH={library} exec {tool} \"$@\"\n"),
        )
        .unwrap();
        fs::set_permissions(&wrapper, fs::Permissions::from_mode(0o755)).unwrap();
    }
    directory.to_path_buf()
}

/// Positive: a Make release build whose ELF output answers the `{artifact:app}` placeholder
/// (frozen keys manifest records by their shadow path) passes binary_compat and integration.
/// Failure: a compilation database with one non-object row is the frozen measured
/// `ici.compile-db.invalid-entry` FAIL beside the covered unit. Negative/partial: a CMake
/// context whose unbuilt unit includes a generated header keeps the other include traces, its
/// `CppIncludeUnresolved` WARN and the frozen `CppCycle:2`, and the compiler syntax replay runs
/// in the C locale (ASCII quotes) whatever the caller's locale.
#[test]
#[ignore = "explicit actual C++ build tools: /usr/bin/g++ (captured version), make, cmake, readelf, the sanitizer runtimes, and ICI_TEST_CLANG_TIDY/ICI_TEST_CLAZY/ICI_TEST_CLANG_LIBRARY_PATH (LLVM 21.1.8, clazy 1.17)"]
fn quality_zoo_cpp_scenarios_match_frozen_verify() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/zoo-cpp-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let gxx = Command::new("/usr/bin/g++")
        .arg("--version")
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8(gxx.stdout).unwrap().lines().next(),
        oracle["gxx"].as_str()
    );
    let zoo = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("quality-zoo/scenarios/cpp");
    let mut mismatches = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let work = tempfile::tempdir().unwrap();
        let root = work.path().canonicalize().unwrap().join("project");
        copy(&zoo.join(name), &root);
        let home = work.path().join("home");
        fs::create_dir(&home).unwrap();
        let mut path = "/usr/bin:/bin".to_owned();
        if CLANG_TOOLS.contains(&name.as_str()) {
            path = format!(
                "{}:{path}",
                clang_wrappers(&work.path().join("bin")).display()
            );
        }
        let environment = BTreeMap::from([
            ("PATH".to_owned(), path),
            ("QT_QPA_PLATFORM".to_owned(), "offscreen".to_owned()),
            ("HOME".to_owned(), home.display().to_string()),
            ("LANG".to_owned(), "C.UTF-8".to_owned()),
            ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        ]);
        let argv: Vec<&str> = std::iter::once("ici")
            .chain(
                case["command"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|item| item.as_str().unwrap()),
            )
            .chain(["--report"])
            .collect();
        let invocation = registry::parse(argv).unwrap();
        let output = commands::dispatch(&invocation, &root, &environment).unwrap();
        let text = fs::read_to_string(root.join("verify_report.json"))
            .unwrap()
            .replace(&root.display().to_string(), "<root>");
        let report: Value = serde_json::from_str(&text).unwrap();
        let actual = json!({"exit_code": output.exit_code, "suite_status": report["suite_status"],
            "engines": engines(&report)});
        let expected = json!({"exit_code": case["exit_code"], "suite_status": case["suite_status"],
            "engines": frozen_engines(name, case)});
        if actual != expected {
            eprintln!("== {name}\nnative: {actual}\nfrozen: {expected}");
            mismatches.push(name.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
