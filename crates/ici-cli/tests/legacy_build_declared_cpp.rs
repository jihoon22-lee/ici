//! Frozen `[build.artifacts]` beside a C++ adapter release build in deep `ici verify`, replayed
//! from `tests/rust-parity/validation/build-declared-cpp-oracle.json`
//! (`capture_build_declared_cpp.py`, frozen v0.13.0, CMake 4.2.3 and g++ 15.2 from /usr/bin).
//!
//! Frozen BuildEngine appends the adapter session's manifest (build.py:736) and then the
//! `config.artifacts` manifest (build.py:227); the suite's analysis context carries both
//! (engines/verify.py:321). Positive (`cmake-declared`, `make-declared`): both manifests are kept
//! in that order on the build engine and in the analysis context. Failure (`*-declared-missing`):
//! the BuildError after the adapter and loader targets, the adapter manifest only. Negative
//! (`cmake-plain`): no declaration, the adapter manifest only.
use serde_json::{Value, json};
use std::{fs, path::Path, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/build-declared-cpp-oracle.json");

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

/// `v` + seven hex digits (the checkout's `git describe --always`) reads `v<commit>`.
fn versionless(text: &str) -> String {
    let mut out = String::new();
    let mut rest = text;
    while let Some(index) = rest.find('v') {
        let (head, tail) = rest.split_at(index);
        out.push_str(head);
        let hex = &tail[1..];
        if hex.len() >= 7
            && hex[..7]
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
        {
            out.push_str("v<commit>");
            rest = &hex[7..];
        } else {
            out.push('v');
            rest = hex;
        }
    }
    out + rest
}

/// One manifest reduced to the compared facts. The adapter manifest keeps frozen's comparable
/// record facts (`legacy_cmake_manifests.rs`: native shadow root and command directory, decisions.md
/// "native C++ shadow build 위치"); the `config.artifacts` manifest is compared whole but for the
/// run identity digests and the checkout commit (asserted on both sides).
fn comparable(manifest: &Value) -> Value {
    let commit = manifest["source_commit"].as_str().unwrap();
    assert!(
        commit.len() == 40 && commit.bytes().all(|byte| byte.is_ascii_hexdigit()),
        "{manifest}"
    );
    for key in ["config_digest", "toolchain_digest"] {
        assert!(
            manifest[key].as_str().unwrap().starts_with("<sha256>")
                || manifest[key].as_str().unwrap().starts_with("sha256:"),
            "{key}"
        );
    }
    let records = manifest["artifacts"].as_array().unwrap();
    let declared = records
        .iter()
        .all(|record| record["producer"] == "config.artifacts");
    let artifacts: Vec<Value> = records
        .iter()
        .map(|record| {
            if declared {
                let mut record = record.clone();
                assert!(record["sha256"].as_str().unwrap().len() > 7);
                record["sha256"] = Value::Null;
                record
            } else {
                json!({"path": record["path"], "scope": record["scope"], "kind": record["kind"],
                    "mode": record["mode"], "producer": record["producer"], "id": record["id"],
                    "target": record["target"],
                    "command_head": record["command"].as_array().unwrap().iter().take(2).collect::<Vec<_>>()})
            }
        })
        .collect();
    json!({"schema_version": manifest["schema_version"], "project_root": manifest["project_root"],
        "variant": manifest["variant"],
        "shadow_root": if declared { manifest["shadow_root"].clone() } else { json!("<shadow>") },
        "artifacts": artifacts})
}

fn comparable_all(manifests: &Value) -> Vec<Value> {
    manifests
        .as_array()
        .into_iter()
        .flatten()
        .map(comparable)
        .collect()
}

#[test]
#[ignore = "explicit actual CMake/Make/g++ build: /usr/bin/cmake (4.2.3), /usr/bin/make and /usr/bin/g++ must be the captured tools"]
fn declared_artifacts_beside_a_cpp_adapter_build_keep_both_manifests_like_frozen() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["source"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cmake = Command::new("/usr/bin/cmake")
        .arg("--version")
        .output()
        .unwrap();
    assert!(String::from_utf8_lossy(&cmake.stdout).starts_with(oracle["cmake"].as_str().unwrap()));
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 5);
    for (name, frozen) in cases {
        let work = tempfile::tempdir().unwrap();
        let root = work.path().canonicalize().unwrap().join("project");
        copy_tree(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../parity-fixtures/cpp")
                .join(frozen["fixture"].as_str().unwrap()),
            &root,
        );
        fs::write(root.join("ici.toml"), frozen["config"].as_str().unwrap()).unwrap();
        for (file, text) in frozen["files"].as_object().unwrap() {
            fs::create_dir_all(root.join(file).parent().unwrap()).unwrap();
            fs::write(root.join(file), text.as_str().unwrap()).unwrap();
        }
        let git = |args: &[&str]| {
            let output = Command::new("/usr/bin/git")
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
            assert!(output.status.success(), "{output:?}");
        };
        git(&["init", "-q", "."]);
        git(&["add", "-A"]);
        git(&["commit", "-qm", "fixture"]);
        let output = Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(["verify", "--profile", "deep", "--report"])
            .current_dir(&root)
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("HOME", work.path())
            .output()
            .unwrap();
        let report: Value = serde_json::from_slice(
            &fs::read(root.join("verify_report.json"))
                .unwrap_or_else(|_| panic!("{name}: {}", String::from_utf8_lossy(&output.stderr))),
        )
        .unwrap();
        let build = report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == "build")
            .unwrap();
        assert_eq!(
            output.status.code(),
            frozen["exit_code"].as_i64().map(|code| code as i32),
            "{name}"
        );
        assert_eq!(report["suite_status"], frozen["suite_status"], "{name}");
        assert_eq!(build["status"], frozen["build"]["status"], "{name}");
        assert_eq!(
            versionless(build["summary"].as_str().unwrap()),
            versionless(frozen["build"]["summary"].as_str().unwrap()),
            "{name}"
        );
        let targets: Vec<Value> = build["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| json!({"target_name": target["target_name"], "status": target["status"]}))
            .collect();
        assert_eq!(json!(targets), frozen["build"]["targets"], "{name}");
        let declared: Vec<&Value> = build["targets"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|target| {
                matches!(
                    target["target_name"].as_str(),
                    Some("DeclaredArtifacts" | "BuildError")
                )
            })
            .map(|target| &target["message"])
            .collect();
        assert_eq!(
            json!(declared),
            frozen["build"]["declared_message"],
            "{name}"
        );
        // The build engine's manifests and the analysis context's, in frozen order.
        assert_eq!(
            comparable_all(&build["artifact_manifests"]),
            comparable_all(&frozen["engines"]["build"]),
            "{name} build manifests"
        );
        assert_eq!(
            comparable_all(&report["analysis_context"]["artifact_manifests"]),
            comparable_all(&frozen["analysis_context"]),
            "{name} analysis context"
        );
        // `ici report --sarif` of the saved run: the C++ build engine's targets at the project
        // root (`BuildAdapter[...]`, `DeclaredArtifacts`, `BuildError`) are location-less results.
        let sarif = Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(["report", "--sarif", "run.sarif"])
            .current_dir(&root)
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("HOME", work.path())
            .output()
            .unwrap();
        assert_eq!(
            sarif.status.code(),
            Some(0),
            "{name}: {}",
            String::from_utf8_lossy(&sarif.stderr)
        );
        let sarif: Value =
            serde_json::from_slice(&fs::read(root.join("run.sarif")).unwrap()).unwrap();
        let mut located: Vec<&str> = sarif["runs"][0]["results"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|result| result["properties"]["projectLocation"]["path"] == ".")
            .inspect(|result| assert_eq!(result["locations"], json!([])))
            .filter(|result| {
                result["properties"]["engine"] == "build"
                    || result["ruleId"] == "ici.legacy.build.target"
            })
            .map(|result| {
                result["properties"]["projectLocation"]["label"]
                    .as_str()
                    .unwrap()
            })
            .collect();
        located.sort_unstable();
        let mut expected: Vec<&str> = build["targets"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|target| target["file_path"] == ".")
            .map(|target| target["target_name"].as_str().unwrap())
            .collect();
        expected.sort_unstable();
        assert_eq!(located, expected, "{name}");
    }
}
