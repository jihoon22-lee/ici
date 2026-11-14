//! Legacy `ici verify` over a Qt AUTOMOC CMake project against the frozen v0.13.0 runs in
//! `tests/rust-parity/cpp/qt-automoc-verify-oracle.json` (`capture_qt_automoc_verify.py`).
//!
//! The native legacy shadow build tree is the linked build directory, so the compilation
//! context keeps the generated `<target>_autogen/mocs_compilation.cpp` units under it. Frozen
//! `_cpp_include_graph._selected_units` and `_cpp_tooling.selected_units` trace and replay only
//! the units of selected production sources (every C/C++ unit of a unity build): the cycle engine
//! traces `src/counter.cpp` alone (PASS MEASURED, one configuration), clang-tidy replays it alone
//! (one configuration, no warning from the generated units) and the compiler-backed complexity
//! boundaries replay it alone (PASS MEASURED, mode `exact`), instead of refusing the mocs units as
//! uncaptured include configurations and unavailable boundary sources. Frozen `_cpp_lint` syntax
//! checks still compile the Qt generated units.
//!
//! Lint targets are compared after the approved shadow difference (decisions.md "canonical CMake
//! 분석 DB 준비", CPPPREP_SHADOW in support/legacy_result_allowances.rs): the native shadow
//! `.ici/rust-v1/build/legacy-<digest>/release` is spelled frozen's `build/ici-cmake-build`, the
//! 12-digit configuration digest over those paths in `C++Syntax:` / `ClangTidy:` names is masked
//! and the targets, which sort by that digest, are compared unordered.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, process::Command};

const SHADOW: &str = r"\.ici/rust-v1/build/legacy-[0-9a-f]{16}/release";

const FIELDS: [&str; 5] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "file_path",
];
const ENGINES: [&str; 4] = ["lint", "test", "cycle", "complexity"];

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

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/qt-automoc-verify-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cmake = Command::new("/usr/bin/cmake")
        .arg("--version")
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8(cmake.stdout).unwrap().lines().next(),
        oracle["cmake"].as_str(),
        "/usr/bin/cmake must be the captured CMake"
    );
    oracle
}

fn replay(oracle: &Value, name: &str, tidy_dir: Option<&str>) {
    let case = &oracle["cases"][name];
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let root = base.join("project");
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity-fixtures/cpp/qt-automoc"),
        &root,
    );
    fs::write(root.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
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
    let home = base.join("home");
    fs::create_dir(&home).unwrap();
    let path = case["path"]
        .as_str()
        .unwrap()
        .replace("{clang-tidy-dir}", tidy_dir.unwrap_or("{clang-tidy-dir}"));
    let environment = BTreeMap::from([
        ("PATH".to_owned(), path),
        ("HOME".to_owned(), home.display().to_string()),
        ("LANG".to_owned(), "C.UTF-8".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        ("QT_QPA_PLATFORM".to_owned(), "offscreen".to_owned()),
    ]);
    let invocation = registry::parse(["ici", "verify", "--report"]).unwrap();
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
    let text = fs::read_to_string(root.join("verify_report.json"))
        .unwrap()
        .replace(&root.display().to_string(), "{root}");
    let report: Value = serde_json::from_str(&text).unwrap();
    let frozen = &case["frozen"];
    let mut engines = serde_json::Map::new();
    for engine in report["results"].as_array().unwrap() {
        let engine_name = engine["engine_name"].as_str().unwrap();
        if !ENGINES.contains(&engine_name) {
            continue;
        }
        let keys = frozen["engines"][engine_name]["extra"]
            .as_object()
            .unwrap()
            .keys();
        engines.insert(
            engine_name.to_owned(),
            json!({
                "status": engine["status"],
                "evidence": engine["evidence"],
                "summary": engine["summary"],
                "targets": engine["targets"].as_array().unwrap().iter().map(|target| {
                    Value::Object(FIELDS.iter().map(|key| ((*key).to_owned(), target[*key].clone())).collect())
                }).collect::<Vec<_>>(),
                "extra": Value::Object(keys.map(|key| (key.clone(), engine["extra"].get(key).cloned().unwrap_or(Value::Null))).collect()),
            }),
        );
    }
    let mut actual = json!({
        "exit_code": output.exit_code,
        "suite_status": report["suite_status"],
        "engines": engines,
    });
    let mut frozen = frozen.clone();
    for view in [&mut actual, &mut frozen] {
        shadow_view(&mut view["engines"]["lint"]["targets"]);
    }
    assert_eq!(
        actual, frozen,
        "{name}\nnative: {actual:#}\nfrozen: {frozen:#}"
    );
}

/// The approved shadow spelling of lint targets (module comment), unordered.
fn shadow_view(targets: &mut Value) {
    let shadow = regex::Regex::new(SHADOW).unwrap();
    let digest = regex::Regex::new(r"^(C\+\+Syntax|ClangTidy):[0-9a-f]{12}$").unwrap();
    let mut rows: Vec<Value> = targets
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            let mut target = target.clone();
            for key in ["file_path", "message"] {
                let text = target[key].as_str().unwrap().to_owned();
                target[key] = json!(shadow.replace_all(&text, "build/ici-cmake-build"));
            }
            let name = target["target_name"].as_str().unwrap().to_owned();
            target["target_name"] = json!(digest.replace(&name, "$1:<digest>"));
            target
        })
        .collect();
    rows.sort_by_key(|row| row.to_string());
    *targets = json!(rows);
}

/// Positive: without clang-tidy the cycle engine traces only the production unit
/// (`cpp_configurations_checked` 1, one `CppIncludeTrace` target) and passes measured, as frozen.
/// Negative: the eight-unit failure class (`include-configuration-mismatch` for each
/// `mocs_compilation.cpp` plus `include-configuration-missing`) does not appear.
#[test]
#[ignore = "explicit actual CMake/ctest/g++/Qt 6 AUTOMOC runs: /usr/bin/cmake (4.2.3), ctest, /usr/bin/g++ and Qt 6 Core development files must be the captured tools"]
fn legacy_verify_traces_only_production_units_of_a_qt_automoc_build_like_frozen() {
    replay(&oracle(), "plain", None);
}

/// Positive: with clang-tidy on PATH clang-tidy and the compiler-backed boundaries replay only
/// `src/counter.cpp` (lint: one clang-tidy configuration and source, only the Clazy-unavailable
/// warning; boundaries: mode `exact`, one configuration and source, no boundary error) and the
/// complexity engine passes measured, as frozen. Failure class it guards: `function boundary
/// source is unavailable: <shadow>/..._autogen/mocs_compilation.cpp` (CI run 43566463718).
#[test]
#[ignore = "explicit actual CMake/ctest/g++/Qt 6 AUTOMOC runs with clang-tidy 21 (ICI_TEST_CLANG_TIDY, LLVM 21.1.6): /usr/bin/cmake (4.2.3), ctest, /usr/bin/g++ and Qt 6 Core development files"]
fn legacy_verify_replays_boundaries_only_for_production_units_of_a_qt_automoc_build_like_frozen() {
    let oracle = oracle();
    let tidy =
        std::env::var("ICI_TEST_CLANG_TIDY").expect("ICI_TEST_CLANG_TIDY names clang-tidy 21");
    let tidy = Path::new(&tidy).canonicalize().unwrap();
    let version = Command::new(&tidy).arg("--version").output().unwrap();
    assert!(
        String::from_utf8(version.stdout)
            .unwrap()
            .contains(oracle["clang_tidy"].as_str().unwrap()),
        "ICI_TEST_CLANG_TIDY must be the captured clang-tidy"
    );
    let directory = tidy.parent().unwrap().display().to_string();
    replay(&oracle, "clang-tidy", Some(&directory));
}
