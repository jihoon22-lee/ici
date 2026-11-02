//! Frozen `ici next plan` C++ planning facts (v0.13.0 `application/planning.py::
//! compile_limitations`, `workspace/compile_units.py::_prepare`): a C++ component without a
//! compilation database carries frozen's note in every plan and its coverage gap as a gate reason
//! only under `--require-full`; a recorded database removes both. The human wording of the same
//! trees is compared line by line in `plan_text_oracle.rs`.
use ici_cli::{planning, registry::parse};
use std::{collections::BTreeMap, fs, path::Path};

const NOTE: &str = "lib: no compilation database for its C++ scope (build 'native' declares no approved configure command — give it a prepare vector or generate out/compile_commands.json)";
const GAP: &str = "lib: no compilation database captured its C++ scope";

fn tree(database: bool) -> tempfile::TempDir {
    let tree = tempfile::tempdir().unwrap();
    let root = tree.path();
    fs::write(
        root.join("ici.toml"),
        "schema_version = 1\n[workspace]\nname = \"demo\"\nprofile = \"standard\"\n\
         [builds.native]\nsystem = \"cmake\"\ndirectory = \"out\"\nproject = \"CMakeLists.txt\"\n\
         variant = \"release\"\nprepare = \"explicit\"\n\
         [[components]]\nid = \"lib\"\nroot = \"lib\"\nlanguages = [\"cpp\"]\nbuild = \"native\"\n",
    )
    .unwrap();
    fs::write(
        root.join("CMakeLists.txt"),
        "cmake_minimum_required(VERSION 3.16)\nproject(demo CXX)\nadd_library(a lib/a.cpp)\n",
    )
    .unwrap();
    fs::create_dir(root.join("lib")).unwrap();
    fs::write(root.join("lib/a.cpp"), "int f() { return 1; }\n").unwrap();
    if database {
        fs::create_dir(root.join("out")).unwrap();
        let rows = serde_json::json!([{
            "directory": root.join("out"),
            "file": root.join("lib/a.cpp"),
            "arguments": ["/usr/bin/g++", "-std=c++17", "-c", root.join("lib/a.cpp"), "-o", "a.o"],
        }]);
        fs::write(root.join("out/compile_commands.json"), rows.to_string()).unwrap();
    }
    tree
}

fn plan(root: &Path, argv: &[&str]) -> planning::PreparedPlan {
    let environment = BTreeMap::from([
        ("PATH".to_string(), "/usr/bin:/bin".to_string()),
        ("HOME".to_string(), root.display().to_string()),
    ]);
    planning::prepare(
        &parse(["ici", "next", "plan"].iter().chain(argv)).unwrap(),
        root,
        &environment,
        false,
    )
    .unwrap()
}

/// Positive: no database — the note is a plan limitation and the gap only becomes a gate reason
/// under `--require-full` (frozen `apply_require_full`).
#[test]
fn missing_cpp_database_notes_every_plan_and_gates_only_under_require_full() {
    let tree = tree(false);
    let ordinary = plan(tree.path(), &[]);
    assert!(ordinary.plan.limitations.iter().any(|note| note == NOTE));
    assert!(!ordinary.plan.coverage_gaps.iter().any(|gap| gap == GAP));
    let full = plan(tree.path(), &["--require-full"]);
    assert!(full.plan.limitations.iter().any(|note| note == NOTE));
    assert!(full.plan.coverage_gaps.iter().any(|gap| gap == GAP));
}

/// Negative: a recorded database for the declared build removes both.
#[test]
fn recorded_cpp_database_carries_no_missing_database_note_or_gap() {
    let tree = tree(true);
    let full = plan(tree.path(), &["--require-full"]);
    assert!(
        !full
            .plan
            .limitations
            .iter()
            .any(|note| note.contains("no compilation database"))
    );
    assert!(!full.plan.coverage_gaps.iter().any(|gap| gap == GAP));
}
