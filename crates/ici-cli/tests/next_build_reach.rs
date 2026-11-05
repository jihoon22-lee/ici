//! `ici next` compilation-database origin and linked-build reach against frozen v0.13.0
//! (`tests/rust-parity/cpp/next-build-reach-oracle.json`, `capture_next_build_reach.py`).
//!
//! Frozen `workspace/compile_units.py` picks the database at the linked build's directory
//! (labelled "<system> build '<id>'[ <variant>]"), else the component root, the component
//! build/, the workspace root and the workspace build/ ("conventional location (...)"), and
//! reads what the linked build's project file reaches: qmake SUBDIRS and cmake
//! add_subdirectory trees, with `<system>-target-missing` when the component root is not
//! reached, `build-definition-missing` for an absent make/explicit definition, and the readers'
//! own warnings. Compared per case: `ici next doctor --json` database, origin, covered,
//! missing and targets; the `ici next plan --json` compile notes (`app: ...`) and coverage
//! gaps; the `ici next verify --json` cpp.compile state, the compile and reach limitations in
//! frozen order, and the gate with and without `--require-full` (selection, workspace,
//! violations and every reason except the `cpp.test` suite reasons, see [`gate`]).
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/next-build-reach-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

const SOURCES: [(&str, &str); 2] = [
    ("src/a.cpp", "int a() { return 1; }\n"),
    ("src/b.cpp", "int main() { return 0; }\n"),
];

fn materialize(project: &Path, case: &Value) {
    let files = case["files"].as_object().unwrap();
    let written = SOURCES
        .iter()
        .map(|(name, text)| (name.to_string(), text.to_string()))
        .chain(
            files
                .iter()
                .map(|(name, text)| (name.clone(), text.as_str().unwrap().to_owned())),
        );
    for (name, text) in written {
        let path = project.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }
    fs::write(project.join("ici.toml"), case["config"].as_str().unwrap()).unwrap();
    let rows: Vec<Value> = case["sources"]
        .as_array()
        .unwrap()
        .iter()
        .map(|source| {
            let file = project.join(source.as_str().unwrap());
            serde_json::json!({"directory": project, "file": file,
                "arguments": ["/usr/bin/g++", "-std=c++17", "-c", file]})
        })
        .collect();
    for (index, location) in case["databases"].as_array().unwrap().iter().enumerate() {
        let path = project.join(location.as_str().unwrap());
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let text = if index == 0 {
            Value::from(rows.clone()).to_string()
        } else {
            "[]".to_owned()
        };
        fs::write(path, text).unwrap();
    }
}

/// Native `ici next <arguments> --json`: (exit code, document with the root written `{root}`).
fn next(project: &Path, home: &Path, arguments: &[&str]) -> (i32, Value) {
    let environment = BTreeMap::from([
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
        (
            "XDG_CACHE_HOME".to_owned(),
            home.join("cache").display().to_string(),
        ),
        ("LANG".to_owned(), "C.UTF-8".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
    ]);
    let mut argv = vec!["ici", "next"];
    argv.extend_from_slice(arguments);
    argv.push("--json");
    let invocation = registry::parse(argv).unwrap();
    let output = commands::dispatch(&invocation, project, &environment).unwrap();
    let text = output
        .stdout
        .replace(&project.display().to_string(), "{root}");
    (
        output.exit_code,
        serde_json::from_str(&text).unwrap_or_else(|error| panic!("{error}: {text}")),
    )
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item.as_str().unwrap().to_owned())
        .collect()
}

/// The compile-input and reach limitations of a run, deduplicated in first-seen order: the
/// plan's compile notes (`app: ...`), cpp.compile's missing-invocation line, the reach warnings
/// and the qmake resolution count (`reach` names the frozen reach messages of the case).
fn compile_notes(limitations: &[String], reach: &[String]) -> Vec<String> {
    let mut notes: Vec<String> = vec![];
    for item in limitations {
        let wanted = item.starts_with("app: ")
            || item.starts_with("no compile invocation for ")
            || item.starts_with("qmake targets resolved: ")
            || reach.contains(item);
        if wanted && !notes.contains(item) {
            notes.push(item.clone());
        }
    }
    notes
}

/// The gate (selection, workspace, violations and reasons) without the `cpp.test` reasons:
/// frozen's CTest/QTest suite reasons (`app.cpp.test.native-ctest: test suite not built ...`,
/// the qmake testlib reader) belong to the C++ validation rows, not to the database and reach
/// compared here.
fn gate(gate: &Value) -> Value {
    let reasons: Vec<&str> = gate["reasons"]
        .as_array()
        .unwrap()
        .iter()
        .map(|reason| reason.as_str().unwrap())
        .filter(|reason| !reason.starts_with("app.cpp.test"))
        .collect();
    serde_json::json!({"selected": gate["selected"], "workspace": gate["workspace"],
        "has_violations": gate["has_violations"], "reasons": reasons})
}

#[test]
fn next_database_origin_and_build_reach_match_frozen() {
    let oracle = oracle();
    let mut mismatches = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let root = tempfile::tempdir().unwrap();
        let project = root.path().join("project");
        let home = root.path().join("home");
        fs::create_dir_all(&home).unwrap();
        materialize(&project, case);
        let mut differences = vec![];
        let (doctor_code, doctor) = next(&project, &home, &["doctor"]);
        if doctor_code != case["doctor_exit_code"] {
            differences.push(format!("doctor exit {doctor_code}"));
        }
        let expected = &case["compile_inputs"][0];
        let actual = &doctor["components"][0]["compile_inputs"];
        for key in ["database", "origin", "covered", "missing", "targets"] {
            if actual[key] != expected[key] {
                differences.push(format!(
                    "doctor {key}: {} vs {}",
                    actual[key], expected[key]
                ));
            }
        }
        let (plan_code, plan) = next(&project, &home, &["plan"]);
        if plan_code != case["plan_exit_code"] {
            differences.push(format!("plan exit {plan_code}"));
        }
        let frozen_plan: Vec<String> = strings(&case["plan_limitations"])
            .into_iter()
            .filter(|item| item.starts_with("app: "))
            .collect();
        let native_plan: Vec<String> = strings(&plan["limitations"])
            .into_iter()
            .filter(|item| item.starts_with("app: "))
            .collect();
        if native_plan != frozen_plan {
            differences.push(format!("plan notes {native_plan:?} vs {frozen_plan:?}"));
        }
        if plan["coverage_gaps"] != case["plan_coverage_gaps"] {
            differences.push(format!(
                "plan gaps {} vs {}",
                plan["coverage_gaps"], case["plan_coverage_gaps"]
            ));
        }
        // Frozen reach messages: the doctor-visible warnings are the limitations naming a
        // project file, an unresolved SUBDIRS entry or the component root's reach.
        let reach: Vec<String> = strings(&case["limitations"])
            .into_iter()
            .filter(|item| {
                (item.contains("CMakeLists.txt: ")
                    || (item.contains(".pro") && !item.contains("testlib/testcase")))
                    || item.contains("is not on disk")
                    || item.contains("a variable reference is not resolved")
                    || item.contains("is not reached by the declared root project")
            })
            .collect();
        let (code, verify) = next(&project, &home, &["verify"]);
        if code != case["exit_code"] {
            differences.push(format!("verify exit {code}"));
        }
        let check: Vec<Value> = verify["checks"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|check| check["check_id"] == "cpp.compile")
            .map(|check| {
                serde_json::json!({"task_id": check["task_id"], "state": check["state"],
                    "evidence_complete": check["evidence_complete"]})
            })
            .collect();
        if Value::from(check.clone()) != case["compile_check"] {
            differences.push(format!(
                "cpp.compile {check:?} vs {}",
                case["compile_check"]
            ));
        }
        let native_notes = compile_notes(&strings(&verify["limitations"]), &reach);
        let frozen_notes = compile_notes(&strings(&case["limitations"]), &reach);
        if native_notes != frozen_notes {
            differences.push(format!("notes {native_notes:#?} vs {frozen_notes:#?}"));
        }
        if gate(&verify["gate"]) != gate(&case["gate"]) {
            differences.push(format!("gate {} vs {}", verify["gate"], case["gate"]));
        }
        let (full_code, full) = next(&project, &home, &["verify", "--require-full"]);
        if full_code != case["full_exit_code"] {
            differences.push(format!("full exit {full_code}"));
        }
        if gate(&full["gate"]) != gate(&case["full_gate"]) {
            differences.push(format!(
                "full gate {} vs {}",
                full["gate"], case["full_gate"]
            ));
        }
        if !differences.is_empty() {
            eprintln!("== {name}\n{}", differences.join("\n"));
            mismatches.push(name.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
