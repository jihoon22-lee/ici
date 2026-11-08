//! Legacy C++ CLI projections (metrics, exception, cycle, dup, dead) against frozen CLI oracles.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "end_line",
    "file_path",
];

fn run(config: &str, command: &str) -> Value {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("src")).unwrap();
    fs::write(
        root.path().join("src/main.cpp"),
        include_str!("../../../parity-fixtures/cpp/metric-cli-fixture.cpp"),
    )
    .unwrap();
    fs::write(
        root.path().join("ici.toml"),
        format!("[project]\ntype='cpp'\nsource_dirs=['src']\n{config}"),
    )
    .unwrap();
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(root.path().join(format!("{command}_report.json"))).unwrap(),
    )
    .unwrap();
    let targets: Vec<Value> = report["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            Value::Object(
                FIELDS
                    .iter()
                    .map(|key| ((*key).to_owned(), target[*key].clone()))
                    .collect(),
            )
        })
        .collect();
    let metrics: Vec<Value> = report["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| target["metrics"].clone())
        .collect();
    serde_json::json!({"exit_code":output.exit_code,"status":report["status"],"targets":targets,
        "target_metrics":metrics,"cpp_scope_exclusions":report["extra"]["cpp_scope_exclusions"]})
}

#[test]
fn legacy_cpp_complexity_and_cognitive_match_frozen_thresholds_messages_and_exit_codes() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/metric-cli-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 8);
    for (name, expected) in cases {
        let (command, config) = name.split_once(':').unwrap();
        let actual = run(oracle["configs"][config].as_str().unwrap(), command);
        assert_eq!(&actual, expected, "{name}");
    }
}

#[test]
fn inverted_or_invalid_cpp_metric_thresholds_stop_before_analysis() {
    for (command, config) in [
        ("complexity", "[engines.complexity]\nwarn_cc=9\nfail_cc=8\n"),
        ("cognitive", "[engines.cognitive]\nwarn=9\nfail=8\n"),
        ("complexity", "[engines.complexity]\nwarn_nesting='deep'\n"),
    ] {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("src")).unwrap();
        fs::write(root.path().join("src/main.cpp"), "int main(){return 0;}\n").unwrap();
        fs::write(
            root.path().join("ici.toml"),
            format!("[project]\ntype='cpp'\nsource_dirs=['src']\n{config}"),
        )
        .unwrap();
        let invocation = registry::parse(["ici", command, "--report"]).unwrap();
        let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
        assert!(
            commands::dispatch(&invocation, root.path(), &environment).is_err(),
            "{config}"
        );
        assert!(!root.path().join(format!("{command}_report.json")).exists());
    }
}

#[test]
fn legacy_cpp_metric_and_exception_scope_excludes_headers_and_matches_frozen_targets() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/legacy-scope-oracle.json"
    ))
    .unwrap();
    let files = [
        (
            "a.hpp",
            include_str!("../../../parity-fixtures/cpp/legacy-scope-fixture/src/a.hpp"),
        ),
        (
            "b.hpp",
            include_str!("../../../parity-fixtures/cpp/legacy-scope-fixture/src/b.hpp"),
        ),
        (
            "extra.cc",
            include_str!("../../../parity-fixtures/cpp/legacy-scope-fixture/src/extra.cc"),
        ),
        (
            "main.cpp",
            include_str!("../../../parity-fixtures/cpp/legacy-scope-fixture/src/main.cpp"),
        ),
        (
            "util.cpp",
            include_str!("../../../parity-fixtures/cpp/legacy-scope-fixture/src/util.cpp"),
        ),
    ];
    for (command, expected) in oracle["cases"].as_object().unwrap() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("src")).unwrap();
        for (name, text) in files {
            fs::write(root.path().join("src").join(name), text).unwrap();
        }
        fs::write(
            root.path().join("ici.toml"),
            "[project]\ntype='cpp'\nsource_dirs=['src']\n",
        )
        .unwrap();
        let invocation = registry::parse(["ici", command.as_str(), "--report"]).unwrap();
        let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
        let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
        let report: Value = serde_json::from_slice(
            &fs::read(root.path().join(format!("{command}_report.json"))).unwrap(),
        )
        .unwrap();
        let targets: Vec<Value> = report["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| {
                Value::Object(
                    FIELDS
                        .iter()
                        .map(|key| ((*key).to_owned(), target[*key].clone()))
                        .collect(),
                )
            })
            .collect();
        let actual = serde_json::json!({"exit_code":output.exit_code,"status":report["status"],"targets":targets});
        assert_eq!(&actual, expected, "{command}");
        // Header functions and the header destructor throw stay outside these frozen scopes.
        assert!(
            targets
                .iter()
                .all(|target| !target["file_path"].as_str().unwrap().ends_with(".hpp"))
        );
    }
}

fn cycle_report(config: &str) -> Value {
    let root = tempfile::tempdir().unwrap();
    for (name, text) in [
        (
            "core/fmt.hpp",
            include_str!("../../../parity-fixtures/cpp/cycle-fixture/src/core/fmt.hpp"),
        ),
        (
            "ui/fmt.hpp",
            include_str!("../../../parity-fixtures/cpp/cycle-fixture/src/ui/fmt.hpp"),
        ),
        (
            "main.cpp",
            include_str!("../../../parity-fixtures/cpp/cycle-fixture/src/main.cpp"),
        ),
        (
            "self.cpp",
            include_str!("../../../parity-fixtures/cpp/cycle-fixture/src/self.cpp"),
        ),
        (
            "x.hpp",
            include_str!("../../../parity-fixtures/cpp/cycle-fixture/src/x.hpp"),
        ),
        (
            "y.hpp",
            include_str!("../../../parity-fixtures/cpp/cycle-fixture/src/y.hpp"),
        ),
        (
            "z.hpp",
            include_str!("../../../parity-fixtures/cpp/cycle-fixture/src/z.hpp"),
        ),
    ] {
        let path = root.path().join("src").join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }
    fs::write(
        root.path().join("ici.toml"),
        format!("[project]\ntype='cpp'\nsource_dirs=['src']\n{config}"),
    )
    .unwrap();
    let invocation = registry::parse(["ici", "cycle", "--report"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join("cycle_report.json")).unwrap()).unwrap();
    let targets: Vec<Value> = report["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            Value::Object(
                FIELDS
                    .iter()
                    .map(|key| ((*key).to_owned(), target[*key].clone()))
                    .collect(),
            )
        })
        .collect();
    serde_json::json!({"exit_code":output.exit_code,"status":report["status"],"targets":targets})
}
fn split_cycles(targets: &Value) -> (Vec<Value>, Vec<Value>) {
    targets
        .as_array()
        .unwrap()
        .iter()
        .cloned()
        .partition(|target| {
            target["target_name"]
                .as_str()
                .unwrap()
                .starts_with("CppCycle:")
        })
}

#[test]
fn legacy_cpp_include_cycles_and_resolution_diagnostics_match_frozen_heuristic() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/cycle-oracle.json"
    ))
    .unwrap();
    let full = &oracle["cases"]["default"];
    let (mut all_cycles, _) = split_cycles(&full["targets"]);
    all_cycles.sort_by_key(|target| target.to_string());
    for (name, expected) in oracle["cases"].as_object().unwrap() {
        let actual = cycle_report(oracle["configs"][name].as_str().unwrap());
        assert_eq!(actual["exit_code"], expected["exit_code"], "{name}");
        assert_eq!(actual["status"], expected["status"], "{name}");
        let (mut cycles, diagnostics) = split_cycles(&actual["targets"]);
        let (expected_cycles, expected_diagnostics) = split_cycles(&expected["targets"]);
        // Frozen Tarjan order follows Python set iteration, so only the reported cycle set,
        // its bounded count and every cycle's path/message are contractual.
        assert_eq!(diagnostics, expected_diagnostics, "{name}");
        assert_eq!(cycles.len(), expected_cycles.len(), "{name}");
        cycles.sort_by_key(|target| target.to_string());
        assert!(
            cycles.iter().all(|cycle| all_cycles.contains(cycle)),
            "{name}: {cycles:?}"
        );
        if name == "default" {
            assert_eq!(cycles, all_cycles);
        }
    }
}

#[test]
fn legacy_cpp_exception_lines_match_frozen_masked_line_heuristic() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/exception-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("src")).unwrap();
    for (name, text) in [
        (
            "dtor.cpp",
            include_str!("../../../parity-fixtures/cpp/exception-fixture/src/dtor.cpp"),
        ),
        (
            "catch.cpp",
            include_str!("../../../parity-fixtures/cpp/exception-fixture/src/catch.cpp"),
        ),
        (
            "clean.cc",
            include_str!("../../../parity-fixtures/cpp/exception-fixture/src/clean.cc"),
        ),
        (
            "literals.cpp",
            include_str!("../../../parity-fixtures/cpp/exception-fixture/src/literals.cpp"),
        ),
        (
            "crlf.cpp",
            include_str!("../../../parity-fixtures/cpp/exception-fixture/src/crlf.cpp"),
        ),
    ] {
        fs::write(root.path().join("src").join(name), text).unwrap();
    }
    fs::write(
        root.path().join("ici.toml"),
        "[project]\ntype='cpp'\nsource_dirs=['src']\n",
    )
    .unwrap();
    let invocation = registry::parse(["ici", "exception", "--report"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join("exception_report.json")).unwrap())
            .unwrap();
    let targets: Vec<Value> = report["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            Value::Object(
                FIELDS
                    .iter()
                    .map(|key| ((*key).to_owned(), target[*key].clone()))
                    .collect(),
            )
        })
        .collect();
    let actual = serde_json::json!({"exit_code":output.exit_code,"status":report["status"],"targets":targets});
    let expected = &oracle["cases"]["exception"];
    assert_eq!(&actual, expected);
    // Every frozen finding is kept: a lambda throw inside a destructor (line 10), two throws on
    // one line reported once (lines 4 and 37), and the frozen `~Spec() throw()` line (27).
    let lines = |name: &str| {
        targets
            .iter()
            .filter(|target| target["target_name"] == name && target["file_path"] == "src/dtor.cpp")
            .map(|target| target["start_line"].as_u64().unwrap())
            .collect::<Vec<_>>()
    };
    assert_eq!(lines("DestructorThrow"), [3, 4, 10, 17, 22, 25, 27, 32, 37]);
    assert_eq!(output.exit_code, 1);
}

fn include_scope_project(config: &str) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    for (name, text) in [
        (
            "include/top.hpp",
            include_str!("../../../parity-fixtures/cpp/include-scope-fixture/include/top.hpp"),
        ),
        (
            "include/sub/deep.h",
            include_str!("../../../parity-fixtures/cpp/include-scope-fixture/include/sub/deep.h"),
        ),
        (
            "include/extra.cpp",
            include_str!("../../../parity-fixtures/cpp/include-scope-fixture/include/extra.cpp"),
        ),
        (
            "src/inner.hpp",
            include_str!("../../../parity-fixtures/cpp/include-scope-fixture/src/inner.hpp"),
        ),
        (
            "src/main.cpp",
            include_str!("../../../parity-fixtures/cpp/include-scope-fixture/src/main.cpp"),
        ),
    ] {
        let path = root.path().join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }
    fs::write(
        root.path().join("ici.toml"),
        format!("[project]\ntype='cpp'\nsource_dirs=['src']\n{config}"),
    )
    .unwrap();
    root
}

fn report(root: &std::path::Path, command: &str) -> Value {
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    let output = commands::dispatch(&invocation, root, &environment).unwrap();
    let report: Value =
        serde_json::from_slice(&fs::read(root.join(format!("{command}_report.json"))).unwrap())
            .unwrap();
    let targets: Vec<Value> = report["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            Value::Object(
                FIELDS
                    .iter()
                    .map(|key| ((*key).to_owned(), target[*key].clone()))
                    .collect(),
            )
        })
        .collect();
    serde_json::json!({"exit_code":output.exit_code,"status":report["status"],"targets":targets})
}

#[test]
fn legacy_cpp_top_level_include_headers_join_cycle_dup_and_dead_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/include-scope-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    for command in ["cycle", "dup", "dead"] {
        let root = include_scope_project("");
        let actual = report(root.path(), command);
        assert_eq!(actual, oracle["cases"][command], "{command}");
        // include/*.cpp stays outside the frozen header scope.
        assert!(
            actual["targets"]
                .as_array()
                .unwrap()
                .iter()
                .all(|target| target["file_path"] != "include/extra.cpp"),
            "{command}"
        );
    }
    // The include/ header defines the cycle, the clone group and the first dead-code source.
    assert_eq!(
        oracle["cases"]["cycle"]["targets"][0]["file_path"],
        "include/top.hpp"
    );
    assert_eq!(
        oracle["cases"]["dead"]["targets"][0]["file_path"],
        "include/sub/deep.h"
    );
}

#[test]
fn legacy_cpp_dead_without_compilation_database_reports_frozen_policy_targets() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/include-scope-oracle.json"
    ))
    .unwrap();
    // auto: SKIP exit 2; required: ERROR exit 1; off: the disabled-policy target at ".".
    for (policy, case) in [
        ("auto", "dead"),
        ("off", "dead:off"),
        ("required", "dead:required"),
    ] {
        let root = include_scope_project(&format!("[engines.dead]\ncpp_unused='{policy}'\n"));
        assert_eq!(
            report(root.path(), "dead"),
            oracle["cases"][case],
            "{policy}"
        );
    }
    // The modern gate never turns the missing compilation evidence into a pass or a skip.
    let root = include_scope_project("");
    let invocation = registry::parse(["ici", "analyze"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
    assert_eq!(output.exit_code, 3, "{}", output.stdout);
    assert!(
        output
            .stdout
            .contains("unused-function analysis requires complete recorded"),
        "{}",
        output.stdout
    );
}

/// Failure: frozen `ici exception` over a C++ source that is not strict UTF-8 reports a
/// `ReadError` ERROR target with CPython's `UnicodeDecodeError` text (invalid start byte,
/// invalid continuation byte, unexpected end of data), keeps analysing the other sources and
/// names the first three errors in the summary (`tests/rust-parity/cpp/capture_exception_read.py`).
/// Intended: native exits 3 for the incomplete required evidence (accepted incomplete-exit-3
/// policy; frozen exits 1 for the same ERROR).
#[test]
fn legacy_cpp_exception_read_errors_match_frozen_decode_text_targets_and_summary() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/exception-read-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 4);
    for (name, case) in cases {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("src")).unwrap();
        fs::write(
            root.path().join("ici.toml"),
            oracle["config"].as_str().unwrap(),
        )
        .unwrap();
        for (path, hex) in case["files"].as_object().unwrap() {
            let hex = hex.as_str().unwrap();
            let bytes: Vec<u8> = (0..hex.len())
                .step_by(2)
                .map(|index| u8::from_str_radix(&hex[index..index + 2], 16).unwrap())
                .collect();
            fs::write(root.path().join(path), bytes).unwrap();
        }
        let invocation = registry::parse(["ici", "exception", "--report"]).unwrap();
        let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
        let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
        let report: Value =
            serde_json::from_slice(&fs::read(root.path().join("exception_report.json")).unwrap())
                .unwrap();
        let frozen = &case["frozen"];
        assert_eq!(frozen["exit_code"], 1, "{name}");
        assert_eq!(output.exit_code, 3, "{name}");
        for key in ["status", "summary", "evidence"] {
            assert_eq!(report[key], frozen[key], "{name} {key}");
        }
        let targets: Vec<Value> = report["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| {
                serde_json::json!({
                    "target_name": target["target_name"],
                    "status": target["status"],
                    "message": target["message"],
                    "start_line": target["start_line"],
                    "file_path": target["file_path"],
                })
            })
            .collect();
        assert_eq!(&serde_json::json!(targets), &frozen["targets"], "{name}");
    }
}

/// A captured project tree: (path, bytes).
type Tree<'a> = &'a [(&'a str, &'a [u8])];

/// `cpp_unused = 'off'` adds frozen's disabled-policy target at "." only when C/C++ candidates
/// exist (a header alone or an excluded vendor source counts), never for a Python-only project,
/// and still beside an unreadable Python source that turns the report into ERROR. With no Python
/// source left to read (unreadable, or every Python file excluded as vendor/generated without its
/// `include_*` opt-in), frozen `_effective_required` makes that engine optional; beside an
/// all-excluded Python scope frozen adds no empty-scope target, reports NOT_RUN and the disabled
/// policy and counts the exclusions. Summary, inventory counts and findings are compared too.
#[test]
fn legacy_dead_off_policy_target_follows_the_cpp_candidates_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/dead-off-scope-oracle.json"
    ))
    .unwrap();
    assert_eq!(
        oracle["oracle"],
        "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
    );
    let used: &[u8] = b"def used():\n    return 1\n\n\nused()\n";
    let cpp: &[u8] = b"int main() { return 0; }\n";
    let cases: [(&str, Tree); 7] = [
        ("off-without-cpp", &[("src/app.py", used)]),
        (
            "off-vendor-python",
            &[("src/main.cpp", cpp), ("src/vendor/tool.py", used)],
        ),
        (
            "off-generated-python",
            &[("src/main.cpp", cpp), ("src/generated/tool.py", used)],
        ),
        (
            "off-vendor-python-included",
            &[("src/main.cpp", cpp), ("src/vendor/tool.py", used)],
        ),
        (
            "off-header-only",
            &[("src/app.py", used), ("src/api.hpp", b"int api();\n")],
        ),
        (
            "off-vendor-only",
            &[
                ("src/app.py", used),
                ("src/vendor/lib.cpp", b"int lib() { return 1; }\n"),
            ],
        ),
        (
            "off-unreadable-python",
            &[
                ("src/app.py", used),
                ("src/bad.py", b"\xff\xfe\n"),
                ("src/main.cpp", b"int main() { return 0; }\n"),
            ],
        ),
    ];
    for (name, tree) in cases {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join(".hg")).unwrap();
        let extra = if name == "off-vendor-python-included" {
            "include_vendor=true\n"
        } else {
            ""
        };
        fs::write(
            root.path().join("ici.toml"),
            format!(
                "[project]\ntype='cpp'\nsource_dirs=['src']\n[engines.dead]\ncpp_unused='off'\n{extra}"
            ),
        )
        .unwrap();
        for (path, content) in tree {
            let path = root.path().join(path);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, content).unwrap();
        }
        let mut actual = report(root.path(), "dead");
        let saved: Value =
            serde_json::from_slice(&fs::read(root.path().join("dead_report.json")).unwrap())
                .unwrap();
        actual["evidence"] = saved["evidence"].clone();
        actual["required"] = saved["required"].clone();
        actual["summary"] = saved["summary"].clone();
        actual["source_extra"] = json!({});
        for key in [
            "source_exclusion_counts",
            "source_files_excluded",
            "source_files_analyzed",
            "source_files_snapshotted",
            "source_bytes_analyzed",
        ] {
            actual["source_extra"][key] = saved["extra"][key].clone();
        }
        actual["findings"] = saved["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                let mut row = json!({"label": finding["primary_location"]["label"]});
                for key in [
                    "rule_id",
                    "message",
                    "confidence",
                    "explanation",
                    "remediation",
                    "severity",
                ] {
                    row[key] = finding[key].clone();
                }
                row
            })
            .collect();
        let frozen = &oracle["cases"][name];
        assert_eq!(&actual, frozen, "{name}");
    }
}
