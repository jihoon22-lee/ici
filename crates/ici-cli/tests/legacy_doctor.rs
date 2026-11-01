//! Historical `ici doctor --json` over legacy-schema and configless projects against the frozen
//! v0.13.0 doctor (`tests/rust-parity/doctor/capture_legacy_doctor.py`): the core/support.py
//! matrix, `derive_tool_policy` (mypy/ruff/coverage promotion, disabled and inapplicable rows,
//! profiles, C++ clang-tidy/clazy/linker policies, `doctor.required_tools`) and the policy rows.
use ici_cli::{commands::dispatch, registry::parse};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

const ROW_KEYS: [&str; 5] = [
    "available",
    "required",
    "optional",
    "required_by",
    "optional_by",
];

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/doctor/legacy-doctor-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

fn required(name: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| panic!("required native fixture tool {name} is missing"))
}

/// Native document reduced to the frozen capture's fields.
fn run(case: &Value, tools: &std::path::Path) -> Value {
    let root = tempfile::tempdir().unwrap();
    if let Some(config) = case["config"].as_str() {
        fs::write(root.path().join("ici.toml"), config).unwrap();
    }
    for (name, content) in case["files"].as_object().unwrap() {
        let path = root.path().join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, content.as_str().unwrap()).unwrap();
    }
    let environment = BTreeMap::from([
        (
            "PATH".to_string(),
            format!("{}:/usr/bin:/bin", tools.display()),
        ),
        ("HOME".to_string(), root.path().display().to_string()),
    ]);
    let cwd = root.path().join(case["cwd"].as_str().unwrap());
    let output = dispatch(
        &parse(["ici", "doctor", "--json"]).unwrap(),
        &cwd,
        &environment,
    )
    .unwrap();
    let data: Value = serde_json::from_str(&output.stdout).unwrap();
    let mut rows = serde_json::Map::new();
    for row in data["capability_inventory"]["tools"].as_array().unwrap() {
        if row["required"] == true || row["optional"] == true {
            rows.insert(
                row["name"].as_str().unwrap().to_owned(),
                Value::Object(
                    ROW_KEYS
                        .iter()
                        .map(|key| ((*key).to_owned(), row[*key].clone()))
                        .collect(),
                ),
            );
        }
    }
    json!({
        "exit_code": output.exit_code,
        "required_tools": data["required_tools"],
        "effective_required_tools": data["effective_required_tools"],
        "analysis_profile": data["analysis_profile"],
        "support_matrix": data["support_matrix"],
        "policy_tools": rows,
        "capability_status": data["capability_inventory"]["status"],
    })
}

/// Recorded differences applied to the frozen value: frozen probed `mypy`/`pytest`/`coverage`
/// as `<.venv or ici's own interpreter> -m <module>` (core/toolchain.py
/// `_resolve_python_module_interpreter`); the Rust product has no Python of its own (ADR-0008)
/// and resolves those executables from the project `.venv`/PATH, so their availability (and the
/// capability status it decides) is native's.
fn corrected(frozen: &Value, native: &Value) -> Value {
    let mut value = frozen.clone();
    for module in ["mypy", "pytest", "coverage"] {
        if let Some(row) = value["policy_tools"].get_mut(module) {
            row["available"] = native["policy_tools"][module]["available"].clone();
        }
    }
    let module_unavailable = ["mypy", "pytest", "coverage"].iter().any(|module| {
        frozen["policy_tools"][*module]["available"] != native["policy_tools"][*module]["available"]
    });
    if module_unavailable {
        value["capability_status"] = native["capability_status"].clone();
    }
    value
}

fn replay(names: &[&str]) {
    let oracle = oracle();
    let tools = tempfile::tempdir().unwrap();
    for (name, variable) in [("ruff", "ICI_TEST_RUFF"), ("mypy", "ICI_TEST_MYPY")] {
        let target = required(variable);
        let version = std::process::Command::new(&target)
            .arg("--version")
            .output()
            .unwrap();
        assert_eq!(
            String::from_utf8_lossy(&version.stdout).trim(),
            oracle["tool_versions"][name],
            "{variable} is not the captured {name}"
        );
        std::os::unix::fs::symlink(target, tools.path().join(name)).unwrap();
    }
    for name in names {
        let case = &oracle["cases"][*name];
        assert!(case.is_object(), "{name}: no frozen case");
        let actual = run(case, tools.path());
        let expected = corrected(&case["frozen"], &actual);
        if actual != expected {
            panic!(
                "{name}\nnative {}\nfrozen {}",
                serde_json::to_string_pretty(&actual).unwrap(),
                serde_json::to_string_pretty(&expected).unwrap()
            );
        }
    }
}

/// Promotions frozen derived from the legacy policy: mypy_required, ruff_required,
/// coverage_required, a C++ clang-tidy/linker `required`, the deep profile and the Qt framework.
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn legacy_doctor_promotes_the_frozen_policy_tools() {
    replay(&[
        "python-policies",
        "python-mypy-required",
        "cpp-policies",
        "python-profile-deep",
        "hybrid-qt",
        "configless-src",
        "configless-pyproject",
    ]);
}

/// No promotion without the policy key, for a disabled engine, under the fast profile, with
/// `cpp_unused = "off"`, and no Python scope for a configless file outside the source dirs or for
/// a subdirectory below a legacy root (frozen reads only the working directory's configuration).
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn legacy_doctor_keeps_unpromoted_disabled_and_inapplicable_rows_frozen() {
    replay(&[
        "python-default",
        "python-mypy-required-type-disabled",
        "python-profile-fast",
        "cpp-unused-off",
        "configless-flat",
        "subdirectory-below-legacy-root",
    ]);
}

/// A missing `doctor.required_tools` executable is a required unavailable row and a WARN
/// capability status, as frozen.
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn legacy_doctor_reports_a_missing_configured_required_tool() {
    replay(&["python-doctor-required-tools"]);
}

#[test]
fn every_captured_doctor_case_is_replayed() {
    assert_eq!(oracle()["cases"].as_object().unwrap().len(), 14);
}
