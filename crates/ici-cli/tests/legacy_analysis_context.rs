//! The historical `verify` report's top-level `analysis_context` against the frozen CLI
//! (`tests/rust-parity/cpp/capture_analysis_context.py`) for C++, Python and hybrid projects.
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

fn run(oracle: &Value, name: &str) -> Value {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    fs::create_dir_all(&root).unwrap();
    let (fixture, kind) = (
        oracle["case_projects"][name][0].as_str(),
        oracle["case_projects"][name][1].as_str().unwrap(),
    );
    if let Some(fixture) = fixture {
        copy_tree(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../parity-fixtures/cpp")
                .join(fixture),
            &root,
        );
    }
    if kind != "cpp" {
        for (name, text) in oracle["python_files"].as_object().unwrap() {
            let path = root.join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, text.as_str().unwrap()).unwrap();
        }
    }
    fs::write(
        root.join("ici.toml"),
        format!(
            "[project]\ntype='{kind}'\nsource_dirs=['src']\n{}",
            oracle["case_configs"][name].as_str().unwrap_or_default()
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
    let environment = BTreeMap::from([
        ("PATH".to_string(), "/usr/bin:/bin".to_string()),
        ("HOME".to_string(), work.path().display().to_string()),
        ("LC_CTYPE".to_string(), "C.UTF-8".to_string()),
    ]);
    commands::dispatch(
        &registry::parse(["ici", "verify", "--report"]).unwrap(),
        &root,
        &environment,
    )
    .unwrap();
    let text = fs::read_to_string(root.join("verify_report.json")).unwrap();
    let report: Value =
        serde_json::from_str(&text.replace(&root.display().to_string(), "<ROOT>")).unwrap();
    report["analysis_context"].clone()
}

fn digest(value: &Value) -> bool {
    value.as_str().is_some_and(|text| {
        text.strip_prefix("sha256:")
            .is_some_and(|hex| hex.len() == 64 && hex.bytes().all(|b| b.is_ascii_hexdigit()))
    })
}

/// Checks the run-specific values (commit, digests, shadow paths, sizes) for their shape and
/// removes them. Recorded difference: ici prepares its shadow builds under `.ici/rust-v1/build`
/// (frozen `build/ici-cmake*`); digests are the native identities. The release analysis
/// database is configured with frozen's `-DCMAKE_UNITY_BUILD=OFF`, so `unity_build` is compared.
fn comparable(mut context: Value, name: &str) -> Value {
    assert_eq!(
        context["schema_version"], "ici.analysis-context/v1",
        "{name}"
    );
    let commit = context["identity"]["source_commit"]
        .as_str()
        .unwrap()
        .to_owned();
    assert!(
        commit.len() == 40 && commit.bytes().all(|b| b.is_ascii_hexdigit()),
        "{name}"
    );
    assert_eq!(
        context["project"]["version"],
        format!("v{}", &commit[..7]),
        "{name}"
    );
    context["project"]["version"] = Value::Null;
    for key in ["config_digest", "toolchain_digest"] {
        assert!(digest(&context["identity"][key]), "{name} {key}");
    }
    context["identity"] = Value::Null;
    let compilation = &mut context["compilation"];
    if compilation["database_path"].is_string() {
        assert!(
            compilation["database_path"]
                .as_str()
                .unwrap()
                .ends_with("/compile_commands.json")
        );
        assert!(digest(&compilation["database_digest"]), "{name}");
        compilation["database_path"] = json!("<shadow>");
        compilation["database_digest"] = Value::Null;
    }
    for unit in compilation["units"].as_array_mut().unwrap() {
        assert!(digest(&unit["configuration"]), "{name}");
        for key in ["directory", "output", "configuration"] {
            unit[key] = Value::Null;
        }
    }
    for manifest in context["artifact_manifests"].as_array_mut().unwrap() {
        assert_eq!(manifest["source_commit"], commit.as_str(), "{name}");
        for key in ["config_digest", "toolchain_digest"] {
            assert!(digest(&manifest[key]), "{name} {key}");
            manifest[key] = Value::Null;
        }
        for key in ["source_commit", "shadow_root"] {
            manifest[key] = Value::Null;
        }
        for artifact in manifest["artifacts"].as_array_mut().unwrap() {
            assert!(digest(&artifact["sha256"]), "{name}");
            assert!(artifact["size"].as_u64().unwrap() > 0, "{name}");
            for key in ["command", "sha256", "size"] {
                artifact[key] = Value::Null;
            }
        }
    }
    context
}

/// Positive: a CMake project reports its root descriptor, sources, the configured release
/// database's units (compiler `[external]`, project-relative sources) and the coverage/sanitize
/// manifests. Negative: a Python project has no backend, no database (null path, no digest) and
/// no manifests, but the same standard-profile requested variants. A hybrid project carries both.
/// A project enabling unity builds through its cache keeps one unit per source with
/// `unity_build` false: the release analysis database is configured `-DCMAKE_UNITY_BUILD=OFF`.
#[test]
#[ignore = "explicit actual CMake/ctest/g++ runs: /usr/bin/cmake (4.2.3), ctest and /usr/bin/g++ must be the captured tools"]
fn legacy_verify_analysis_context_matches_frozen_for_cpp_python_and_hybrid() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/analysis-context-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cmake = Command::new("cmake").arg("--version").output().unwrap();
    assert_eq!(
        String::from_utf8(cmake.stdout).unwrap().lines().next(),
        oracle["cmake"].as_str()
    );
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 5);
    assert_eq!(cases["cpp-unity"]["compilation"]["unity_build"], false);
    assert_eq!(
        cases["cpp-unity"]["compilation"]["units"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert!(cases["python"]["compilation"]["database_path"].is_null());
    assert!(
        cases["python"]["compilation"]
            .get("database_digest")
            .is_none()
    );
    let mut mismatches = vec![];
    for (kind, frozen) in cases {
        let native = run(&oracle, kind);
        if kind == "python" {
            assert!(native["compilation"].get("database_digest").is_none());
        }
        let (native, frozen) = (comparable(native, kind), comparable(frozen.clone(), kind));
        if native != frozen {
            eprintln!(
                "== {kind}\nfrozen: {}\nnative: {}",
                serde_json::to_string_pretty(&frozen).unwrap(),
                serde_json::to_string_pretty(&native).unwrap()
            );
            mismatches.push(kind.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
