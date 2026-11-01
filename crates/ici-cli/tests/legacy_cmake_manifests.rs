//! Legacy `ici.artifacts/v2` manifests of compiled CMake build sessions against the frozen CLI
//! (`tests/rust-parity/cpp/capture_cmake_manifests.py`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, process::Command};

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

/// The comparable manifest: recorded differences are reduced to their frozen shape.
fn comparable(manifests: &Value, commit: &str) -> Value {
    let Some(items) = manifests.as_array() else {
        return manifests.clone();
    };
    json!(items.iter().map(|manifest| {
        assert_eq!(manifest["source_commit"], commit, "the manifest names the checkout commit");
        for key in ["config_digest", "toolchain_digest"] {
            assert!(manifest[key].as_str().unwrap().starts_with("sha256:"));
        }
        let artifacts: Vec<Value> = manifest["artifacts"].as_array().unwrap().iter().map(|artifact| {
            assert!(artifact["sha256"].as_str().unwrap().starts_with("sha256:"));
            assert!(artifact["size"].as_u64().unwrap() > 0);
            let command = artifact["command"].as_array().unwrap();
            json!({
                "path": artifact["path"], "scope": artifact["scope"], "kind": artifact["kind"],
                "mode": artifact["mode"], "producer": artifact["producer"], "id": artifact["id"],
                "target": artifact["target"], "command_head": command[..2],
            })
        }).collect();
        json!({"schema_version": manifest["schema_version"], "project_root": manifest["project_root"],
            "variant": manifest["variant"], "artifacts": artifacts})
    }).collect::<Vec<_>>())
}

fn run(oracle: &Value, case: &Value) -> Value {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp")
            .join(case["fixture"].as_str().unwrap()),
        &root,
    );
    fs::write(root.join("ici.toml"), oracle["base"].as_str().unwrap()).unwrap();
    let git = |args: &[&str]| {
        let output = Command::new("git")
            .args([
                "-c",
                "user.email=ici@example.invalid",
                "-c",
                "user.name=ici",
            ])
            .args(args)
            .current_dir(&root)
            .output()
            .unwrap();
        assert!(output.status.success());
        String::from_utf8(output.stdout).unwrap().trim().to_owned()
    };
    git(&["init", "-q", "."]);
    git(&["add", "-A"]);
    git(&["commit", "-qm", "fixture"]);
    let commit = git(&["rev-parse", "HEAD"]);
    let command = case["command"].as_str().unwrap();
    let args: Vec<&str> = case["args"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    let invocation =
        registry::parse(["ici", command].into_iter().chain(args).chain(["--report"])).unwrap();
    let environment = BTreeMap::from([
        ("PATH".to_string(), "/usr/bin:/bin".to_string()),
        ("HOME".to_string(), work.path().display().to_string()),
    ]);
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
    let report: Value =
        serde_json::from_slice(&fs::read(root.join(format!("{command}_report.json"))).unwrap())
            .unwrap();
    // The owned CMake preparation never reads as input drift: the initial plan's legacy
    // fallback compiler is carried (re-measured) into the final re-plan.
    let reasons = report["native_run"]["gate"]["reasons"].to_string();
    assert!(
        !reasons.contains("inputs changed while running"),
        "{reasons}"
    );
    let engines: Vec<Value> = if command == "verify" {
        report["results"].as_array().unwrap().clone()
    } else {
        vec![report]
    };
    let engines: serde_json::Map<String, Value> = engines
        .iter()
        .filter(|engine| {
            ["build", "test", "sanitize", "thread_sanitize"]
                .contains(&engine["engine_name"].as_str().unwrap())
        })
        .map(|engine| {
            (
                engine["engine_name"].as_str().unwrap().to_owned(),
                comparable(&engine["artifact_manifests"], &commit),
            )
        })
        .collect();
    json!({"exit_code": output.exit_code, "engines": engines})
}

/// Frozen view with the same reductions: digests and commit are run identity values and the
/// command keeps its `cmake --build` head. Recorded differences: native shadow trees live under
/// `.ici/rust-v1/build/` (frozen `build/ici-cmake[-asan]`), so `shadow_root` and the command's
/// directory and `--parallel <jobs>` differ, and the digests hash the native run identity.
fn frozen(case: &Value) -> Value {
    let mut frozen =
        json!({"exit_code": case["frozen"]["exit_code"], "engines": case["frozen"]["engines"]});
    for manifests in frozen["engines"].as_object_mut().unwrap().values_mut() {
        if let Some(items) = manifests.as_array_mut() {
            for manifest in items.iter_mut() {
                let artifacts: Vec<Value> = manifest["artifacts"].as_array().unwrap().iter().map(|artifact| {
                    json!({
                        "path": artifact["path"], "scope": artifact["scope"], "kind": artifact["kind"],
                        "mode": artifact["mode"], "producer": artifact["producer"], "id": artifact["id"],
                        "target": artifact["target"], "command_head": artifact["command"].as_array().unwrap()[..2],
                    })
                }).collect();
                *manifest = json!({"schema_version": manifest["schema_version"], "project_root": manifest["project_root"],
                    "variant": manifest["variant"], "artifacts": artifacts});
            }
        }
    }
    frozen
}

/// Positive: `ici verify` on a CMake suite publishes one coverage (test) and one sanitize
/// manifest with frozen paths, kinds, modes, ids and targets. Negative: standalone `ici test`
/// has no analysis context and publishes none. Failure: a CMake build that fails publishes none.
#[test]
#[ignore = "explicit actual CMake/g++ build: /usr/bin/cmake (4.2.3) and /usr/bin/g++ must be the captured tools"]
fn legacy_cmake_build_sessions_publish_the_frozen_artifact_manifests() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/cmake-manifests-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cmake = Command::new("cmake").arg("--version").output().unwrap();
    assert_eq!(
        String::from_utf8(cmake.stdout).unwrap().lines().next(),
        oracle["cmake"].as_str()
    );
    let cases = oracle["cases"].as_object().unwrap();
    let mut mismatches = vec![];
    for (name, case) in cases {
        let actual = run(&oracle, case);
        let expected = frozen(case);
        if actual != expected {
            eprintln!("== {name}\nactual: {actual}\nfrozen: {expected}");
            mismatches.push(name.clone());
        }
    }
    assert!(
        cases["verify-suite"]["frozen"]["engines"]["test"][0]["artifacts"]
            .as_array()
            .unwrap()
            .iter()
            .any(|artifact| artifact["id"] == "coverage:shadow:libcalc.a")
    );
    // The deep profile publishes the thread-sanitize session's manifest.
    assert_eq!(
        cases["verify-deep-plain"]["frozen"]["engines"]["thread_sanitize"][0]["variant"],
        "thread-sanitize"
    );
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
