//! Frozen `[build.artifacts]` (engines/build.py:185-227) per schema family, replayed from
//! `tests/rust-parity/validation/build-declared-artifacts-oracle.json`
//! (`capture_build_declared_artifacts.py`, frozen v0.13.0).
//!
//! Legacy deep `ici verify`: the package and loaders are written and recorded first, then the
//! declaration is evaluated over the project's files and this run's outputs. Positive: kept
//! declarations (`DeclaredArtifacts` PASS and the whole `config.artifacts` manifest), including
//! globs that only match this build's own outputs (`output-tree`). Failure: a pattern that
//! matched nothing (missing, one of two missing, an outside link, a directory) is the BuildError
//! ERROR after the produced targets, suite WARN, exit 0, with no manifest. Negative: no
//! declaration. Standalone `ici build` keeps the approved build.py:230 correction (cpp.json
//! `command:ici build`): a kept declaration is native success, exit 0; a broken one is the
//! BuildError, exit 1; frozen's blanket 'shared analysis context' refusal is not ported.
//! Schema 1: `[build.artifacts]` is the unknown-key configuration refusal (exit 2, no tool run,
//! no writes) under `next plan`, `next verify` and `analyze` with the narrow display difference
//! of decisions.md "schema 1 `[build.artifacts]` 거부 표시"; stable `ici build` keeps the exact
//! `[workspace]` refusal.
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
    process::Command,
};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/build-declared-artifacts-oracle.json");
const PYPROJECT: &str = "[project]\nname = 'pkg'\nversion = '2.3.4'\n";

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["source"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

fn listing(root: &Path) -> BTreeSet<String> {
    let mut found = BTreeSet::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory).unwrap().flatten() {
            let kind = entry.file_type().unwrap();
            let path = entry.path();
            if kind.is_dir() {
                pending.push(path);
            } else {
                found.insert(
                    path.strip_prefix(root)
                        .unwrap()
                        .to_string_lossy()
                        .into_owned(),
                );
            }
        }
    }
    found
}

struct Project {
    _work: tempfile::TempDir,
    home: PathBuf,
    root: PathBuf,
    before: BTreeSet<String>,
}

/// Frozen `populate`: `ici.toml`, `pyproject.toml`, the files and links, beside `../outside.txt`.
fn populate(case: &Value) -> Project {
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let home = base.join("home");
    fs::create_dir(&home).unwrap();
    fs::write(base.join("outside.txt"), "outside\n").unwrap();
    let root = base.join("p");
    fs::create_dir(&root).unwrap();
    fs::write(root.join("ici.toml"), case["config"].as_str().unwrap()).unwrap();
    fs::write(root.join("pyproject.toml"), PYPROJECT).unwrap();
    for (name, text) in case["files"].as_object().unwrap() {
        fs::create_dir_all(root.join(name).parent().unwrap()).unwrap();
        fs::write(root.join(name), text.as_str().unwrap()).unwrap();
    }
    for (name, target) in case["links"].as_object().into_iter().flatten() {
        fs::create_dir_all(root.join(name).parent().unwrap()).unwrap();
        std::os::unix::fs::symlink(target.as_str().unwrap(), root.join(name)).unwrap();
    }
    let before = listing(&root);
    Project {
        _work: work,
        home,
        root,
        before,
    }
}

fn ici(project: &Project, argv: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(argv)
        .current_dir(&project.root)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", &project.home)
        .output()
        .unwrap()
}

/// Files the run created, outside ici's own state directory.
fn created(project: &Project) -> Vec<String> {
    listing(&project.root)
        .difference(&project.before)
        .filter(|path| !path.starts_with(".ici/"))
        .cloned()
        .collect()
}

fn words(text: &[u8]) -> String {
    String::from_utf8_lossy(text)
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

/// The run identity's digests are native namespaces (decisions.md "identity"): both sides are
/// sha256 digests; every other manifest field, record and order is compared as recorded.
fn comparable(manifests: &[Value]) -> Vec<Value> {
    manifests
        .iter()
        .map(|manifest| {
            let mut manifest = manifest.clone();
            for key in ["config_digest", "toolchain_digest"] {
                assert!(
                    manifest[key]
                        .as_str()
                        .is_some_and(|digest| digest.starts_with("sha256:")),
                    "{key}: {manifest}"
                );
                manifest[key] = Value::Null;
            }
            manifest
        })
        .collect()
}

fn broken(frozen: &Value) -> bool {
    frozen["verify"]["build"]["status"] == "ERROR"
}

#[test]
fn legacy_deep_verify_records_declared_artifacts_after_the_build_like_frozen() {
    let oracle = oracle();
    let cases = oracle["legacy"].as_object().unwrap();
    assert_eq!(cases.len(), 9);
    for (name, frozen) in cases {
        let project = populate(frozen);
        let output = ici(&project, &["verify", "--report", "--no-cache"]);
        let text = fs::read_to_string(project.root.join("verify_report.json"))
            .unwrap_or_else(|_| panic!("{name}: no report: {}", words(&output.stderr)))
            .replace(&project.root.display().to_string(), "<ROOT>");
        let report: Value = serde_json::from_str(&text).unwrap();
        let engine = report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|item| item["engine_name"] == "build")
            .unwrap();
        let expected = &frozen["verify"];
        assert_eq!(
            output.status.code(),
            expected["exit_code"].as_i64().map(|code| code as i32),
            "{name}"
        );
        assert_eq!(report["suite_status"], expected["suite_status"], "{name}");
        for key in ["status", "summary", "evidence", "required", "targets"] {
            assert_eq!(engine[key], expected["build"][key], "{name} {key}");
        }
        let manifests: Vec<Value> = engine["artifact_manifests"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|manifest| {
                manifest["artifacts"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|record| record["producer"] == "config.artifacts")
            })
            .cloned()
            .collect();
        let frozen_manifests = expected["manifests"].as_array().unwrap();
        assert_eq!(
            comparable(&manifests),
            comparable(frozen_manifests),
            "{name}"
        );
        // Failure: no `config.artifacts` manifest and no DeclaredArtifacts PASS.
        assert_eq!(
            broken(frozen),
            frozen_manifests.is_empty() && name != "none",
            "{name}"
        );
    }
}

#[test]
fn standalone_legacy_build_keeps_the_produced_outputs_and_reruns_over_its_own_records() {
    let oracle = oracle();
    for (name, frozen) in oracle["legacy"].as_object().unwrap() {
        let project = populate(frozen);
        let output = ici(&project, &["build"]);
        let expected = &frozen["build"];
        // The package and loaders are written before the declaration is evaluated, either way.
        assert_eq!(json!(created(&project)), expected["created"], "{name}");
        let declared = frozen["config"]
            .as_str()
            .unwrap()
            .contains("[build.artifacts]");
        let stdout = words(&output.stdout);
        if !declared {
            assert_eq!(output.status.code(), Some(0), "{name}");
            assert_eq!(stdout, expected["stdout"], "{name}");
        } else if broken(frozen) {
            // Approved correction (build.py:230): the real BuildError, frozen's exit 1.
            assert_eq!(expected["exit_code"], 1);
            assert_eq!(output.status.code(), Some(1), "{name}");
            assert_eq!(
                stdout,
                format!(
                    "✘ {}",
                    frozen["verify"]["build"]["summary"].as_str().unwrap()
                ),
                "{name}"
            );
        } else {
            assert_eq!(expected["exit_code"], 1);
            assert_eq!(output.status.code(), Some(0), "{name}: {stdout}");
            assert_eq!(
                stdout, "✔ Build Completed: pkg v2.3.4 into v2.3.4/x86_64",
                "{name}"
            );
        }
        // Ownership records name exactly the files this run wrote; declared project files and
        // the outside link stay user files.
        // (`.ici/rust-v1/python-artifacts/<digest>.json` is that ownership manifest itself.)
        let (state, owned): (BTreeSet<String>, BTreeSet<String>) =
            ici_cli::artifact_build::owned_outputs(&project.root)
                .unwrap()
                .into_iter()
                .partition(|path| path.starts_with(".ici/rust-v1/python-artifacts/"));
        assert_eq!(state.len(), 1, "{name}: {state:?}");
        let written: BTreeSet<String> = created(&project).into_iter().collect();
        assert_eq!(owned, written, "{name}");
        // A rerun reads its own outputs as owned, never as user files it must refuse.
        let rerun = ici(&project, &["build"]);
        assert_eq!(rerun.status.code(), output.status.code(), "{name} rerun");
        assert_eq!(words(&rerun.stdout), stdout, "{name} rerun");
        assert_eq!(
            json!(created(&project)),
            expected["created"],
            "{name} rerun"
        );
    }
}

/// R-2 (narrow ruling e): the same refusal class, exit 2, no tool run and no writes; the
/// display adds the `file:line:column` origin, says 'unknown configuration key' and loses
/// frozen's 'did you mean builds?' hint. The stable command's refusal is byte-equal.
#[test]
fn schema1_build_artifacts_is_the_unknown_key_refusal_under_every_next_command() {
    let oracle = oracle();
    let frozen = &oracle["schema1"]["schema1-artifacts"];
    let case = json!({"config": frozen["config"], "files": frozen["files"]});
    for command in ["next plan", "next verify", "analyze", "build"] {
        let project = populate(&case);
        let argv: Vec<&str> = command.split(' ').collect();
        let output = ici(&project, &argv);
        let expected = &frozen[command];
        let stderr = String::from_utf8_lossy(&output.stderr)
            .replace(&project.root.display().to_string(), "<ROOT>")
            .trim()
            .to_owned();
        assert_eq!(output.status.code(), Some(2), "{command}");
        assert_eq!(expected["exit_code"], 2, "{command}");
        assert_eq!(output.stdout, b"", "{command}");
        assert_eq!(expected["stdout"], "", "{command}");
        assert_eq!(json!(created(&project)), expected["created"], "{command}");
        assert!(!project.root.join(".ici").exists(), "{command}");
        if command == "build" {
            assert_eq!(stderr, expected["stderr"], "{command}");
        } else {
            assert_eq!(
                expected["stderr"],
                "config: <ROOT>/ici.toml: build: unknown key (did you mean builds?)"
            );
            assert_eq!(
                stderr, "config: <ROOT>/ici.toml:8:2: build: unknown configuration key",
                "{command}"
            );
        }
    }
    // Control: without the table the same project plans (frozen exit 0).
    let plain = &oracle["schema1"]["schema1-plain"];
    let project = populate(&json!({"config": plain["config"], "files": plain["files"]}));
    assert_eq!(plain["next plan"]["exit_code"], 0);
    assert_eq!(ici(&project, &["next", "plan"]).status.code(), Some(0));
}

/// Schema 2 has no frozen counterpart (native policy, root ruling R-3): through the CLI, a
/// declared pattern that matched nothing is the check's blocking finding at its declaring line
/// in the saved result, the required gate FAILs with exit 1 (optional: the existing optional
/// rule) and nothing is written; a kept declaration passes with exit 0.
#[test]
fn schema2_unmatched_declaration_is_a_measured_contract_failure_through_the_cli() {
    for (declared, required, exit) in [
        ("report = ['reports/*.txt']", true, 0),
        ("report = ['missing/*.txt']", true, 1),
        ("report = ['missing/*.txt']", false, 0),
    ] {
        let mut config = "schema_version = 2\nprofile = 'deep'\n".to_owned();
        // Every catalog check off; `python.artifact` (outside the catalog) on.
        for check in ici_domain::builtin_catalog().checks {
            config += &format!(
                "[checks.\"{}\"]\nenabled = false\nrequired = false\n",
                check.id
            );
        }
        config += &format!("[checks.\"python.artifact\"]\nenabled = true\nrequired = {required}\n");
        config += "[[components]]\nid = 'app'\nroot = '.'\nlanguages = ['python']\nsource_dirs = ['src']\nsources = ['src/**/*.py']\n[build.artifacts]\n";
        config += declared;
        config += "\n";
        let line = config.lines().count();
        let project = populate(&json!({
            "config": config,
            "files": {"src/app.py": "def main():\n    return 0\n", "reports/summary.txt": "ok\n"},
        }));
        let output = ici(&project, &["next", "verify"]);
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert_eq!(
            output.status.code(),
            Some(exit),
            "{declared} {required}: {stdout} {}",
            words(&output.stderr)
        );
        let result: Value = serde_json::from_slice(
            &fs::read(project.root.join(".ici/rust-v1/result.json")).unwrap(),
        )
        .unwrap();
        let check = result["checks"]
            .as_array()
            .unwrap()
            .iter()
            .find(|check| check["check_id"] == "python.artifact")
            .unwrap();
        assert_eq!(check["state"], "SUCCEEDED", "{declared}");
        assert_eq!(check["evidence_complete"], true, "{declared}");
        let findings: Vec<&Value> = result["findings"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|finding| finding["rule_id"] == "artifact.declared-unmatched")
            .collect();
        if declared.contains("missing") {
            assert_eq!(findings.len(), 1, "{result}");
            assert_eq!(
                findings[0]["message"],
                "declared artifact glob matched nothing: report: missing/*.txt"
            );
            assert_eq!(findings[0]["primary_location"]["path"], "ici.toml");
            assert_eq!(findings[0]["primary_location"]["start_line"], line);
            assert_eq!(
                result["gate"]["selected"],
                if required { "FAIL" } else { "PASS" },
                "{result}"
            );
            // An optional check's finding stays reported without failing the gate.
            assert_eq!(
                result["gate"]["reasons"],
                if required {
                    json!(["1 violation(s) found"])
                } else {
                    json!([])
                }
            );
            assert!(!project.root.join("v2.3.4").exists());
            assert!(created(&project).is_empty(), "{:?}", created(&project));
        } else {
            assert!(findings.is_empty());
            assert_eq!(result["gate"]["selected"], "PASS", "{result}");
            assert!(project.root.join("v2.3.4/x86_64/env.sh").is_file());
        }
    }
}
