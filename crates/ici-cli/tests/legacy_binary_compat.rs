//! Legacy deep-profile `binary_compat` (and its `build` producer) against the frozen
//! `ici verify --profile deep --report` oracle (`tests/rust-parity/cpp/capture_binary_compat.py`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const FINDING_FIELDS: [&str; 9] = [
    "rule_id",
    "message",
    "severity",
    "confidence",
    "category",
    "tool_name",
    "tool_rule_id",
    "primary_location",
    "fingerprint",
];

fn engines(case: &Value, base: &str, environment: &BTreeMap<String, String>) -> Value {
    let root = tempfile::tempdir().unwrap();
    let fixture =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity-fixtures/cpp/package-make");
    for name in ["Makefile", "pyproject.toml"] {
        fs::copy(fixture.join(name), root.path().join(name)).unwrap();
    }
    fs::create_dir(root.path().join("src")).unwrap();
    fs::copy(
        fixture.join("src/main.cpp"),
        root.path().join("src/main.cpp"),
    )
    .unwrap();
    fs::write(root.path().join("ici.toml"), base).unwrap();
    for (relative, text) in case["changes"].as_object().unwrap() {
        fs::write(root.path().join(relative), text.as_str().unwrap()).unwrap();
    }
    let invocation = registry::parse(["ici", "verify", "--profile", "deep", "--report"]).unwrap();
    let output = commands::dispatch(&invocation, root.path(), environment).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(root.path().join("verify_report.json"))
            .unwrap_or_else(|_| panic!("no report: {}", output.stdout)),
    )
    .unwrap();
    let mut engines = serde_json::Map::new();
    for engine in report["results"].as_array().unwrap() {
        let name = engine["engine_name"].as_str().unwrap_or_default();
        if !matches!(name, "build" | "binary_compat") {
            continue;
        }
        let targets: Vec<Value> = engine["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| {
                json!({
                    "target_name": target["target_name"],
                    "status": target["status"],
                    "message": target["message"],
                    "start_line": target["start_line"],
                    "file_path": target["file_path"],
                })
            })
            .collect();
        let mut rules: Vec<String> = engine["findings"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|finding| finding["rule_id"].as_str().map(str::to_owned))
            .collect();
        rules.sort();
        // Every ABI finding with the capture's "<project>" normalization of loader paths.
        let project = root.path().display().to_string();
        let mut findings: Vec<Value> = engine["findings"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|finding| {
                finding["rule_id"]
                    .as_str()
                    .is_some_and(|rule| rule.starts_with("ici.binary."))
            })
            .map(|finding| {
                let picked = Value::Object(
                    FINDING_FIELDS
                        .iter()
                        .map(|key| ((*key).to_owned(), finding[*key].clone()))
                        .collect(),
                );
                serde_json::from_str(&picked.to_string().replace(&project, "<project>")).unwrap()
            })
            .collect();
        findings.sort_by_key(|finding| {
            (
                finding["rule_id"].as_str().unwrap_or_default().to_owned(),
                finding["fingerprint"]
                    .as_str()
                    .unwrap_or_default()
                    .to_owned(),
            )
        });
        engines.insert(
            name.into(),
            json!({"status":engine["status"],"summary":engine["summary"],"targets":targets,"rules":rules,"findings":findings}),
        );
    }
    Value::Object(engines)
}

/// Positive: every `engines.binary_compat` key (enabled, mode, expected_class/machine,
/// max_glibc/glibcxx/cxxabi, require_static, forbid_absolute_rpath, forbid_build_paths,
/// forbidden/allowed_needed, allow_non_elf, artifacts, max_artifacts, required) changes the
/// native view exactly as frozen's; the capture asserts each key case differs from its
/// reference. Negative: in-policy values (matching class/machine, higher floors) PASS.
/// Failure: missing/truncated readelf, unpublished selections and max_artifacts overflow are
/// the frozen AnalysisUnavailable ERROR.
#[test]
#[ignore = "explicit actual GNU Make, g++ and readelf deep legacy run (ICI_TEST_MAKE, ICI_TEST_CXX)"]
fn legacy_binary_compat_targets_and_summaries_match_the_frozen_deep_pipeline() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/binary-compat-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let path = ["ICI_TEST_MAKE", "ICI_TEST_CXX"]
        .map(|name| std::env::var(name).unwrap_or_else(|_| panic!("explicit {name}")))
        .iter()
        .map(|tool| Path::new(tool).parent().unwrap().display().to_string())
        .chain(["/usr/bin".into(), "/bin".into()])
        .collect::<Vec<_>>()
        .join(":");
    let base = oracle["base"].as_str().unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 28);
    for (name, case) in cases {
        // A case may hide executables: PATH becomes a symlink farm of every other entry.
        let farm = tempfile::tempdir().unwrap();
        let hidden: Vec<&str> = case["hidden"]
            .as_array()
            .map(|items| items.iter().filter_map(Value::as_str).collect())
            .unwrap_or_default();
        // A hidden executable may be replaced by a script (a readelf that exits 0 with only
        // the first lines of its evidence).
        if let Some(replaced) = case["replaced"].as_object() {
            use std::os::unix::fs::PermissionsExt;
            for (tool, text) in replaced {
                let script = farm.path().join(tool);
                fs::write(&script, text.as_str().unwrap()).unwrap();
                fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();
            }
        }
        let search = if hidden.is_empty() {
            path.clone()
        } else {
            for directory in path.split(':') {
                let mut entries: Vec<_> = fs::read_dir(directory)
                    .unwrap()
                    .map(|entry| entry.unwrap().file_name())
                    .collect();
                entries.sort();
                for entry in entries {
                    let link = farm.path().join(&entry);
                    if !hidden.contains(&entry.to_string_lossy().as_ref())
                        && fs::symlink_metadata(&link).is_err()
                    {
                        std::os::unix::fs::symlink(Path::new(directory).join(&entry), link)
                            .unwrap();
                    }
                }
            }
            farm.path().display().to_string()
        };
        let environment = BTreeMap::from([("PATH".to_string(), search)]);
        let actual = engines(case, base, &environment);
        // `disabled`: engines.binary_compat.enabled=false leaves no binary_compat result.
        assert_eq!(
            actual.as_object().unwrap().keys().collect::<Vec<_>>(),
            case["frozen"]
                .as_object()
                .unwrap()
                .keys()
                .collect::<Vec<_>>(),
            "{name}: engine set"
        );
        for (engine, frozen) in case["frozen"].as_object().unwrap() {
            // A Make build without linked outputs is frozen's BuildAdapter PASS (0) +
            // BuildArtifacts FAIL (build.py:92).
            assert_eq!(&actual[engine], frozen, "{name} {engine}");
        }
    }
}

/// Frozen `_analysis_config._validate_binary_compat` refuses an ABI floor that is neither empty
/// nor a dotted version of at most 32 characters before anything runs
/// (`tests/rust-parity/cpp/capture_abi_floor_refusal.py`): exit 2, no saved report, and the
/// frozen wording `<key> must be empty or an ABI version such as 2.17`. The native line keeps
/// the located form every native configuration refusal has (`<file>:<line>:<column>: <key>: `,
/// config.json legacy config_key rows); the problem text after the key is frozen's.
#[test]
fn invalid_abi_floor_is_refused_with_the_frozen_wording() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/abi-floor-refusal-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let work = tempfile::tempdir().unwrap();
        let project = work.path().join("project");
        fs::create_dir_all(project.join("src")).unwrap();
        fs::write(project.join("src/main.cpp"), "int main() { return 0; }\n").unwrap();
        let config = case["config"].as_str().unwrap();
        fs::write(project.join("ici.toml"), config).unwrap();
        let output = std::process::Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(["verify", "--profile", "deep", "--report"])
            .current_dir(&project)
            .env_clear()
            .env("HOME", work.path())
            .env("PATH", "/usr/bin:/bin")
            .output()
            .unwrap();
        assert_eq!(
            json!(output.status.code().unwrap()),
            case["exit_code"],
            "{name}: exit"
        );
        assert_eq!(
            String::from_utf8_lossy(&output.stdout),
            case["stdout"].as_str().unwrap(),
            "{name}: stdout"
        );
        assert_eq!(
            json!(project.join("verify_report.json").exists()),
            case["report_written"],
            "{name}: report"
        );
        let frozen = case["stderr"].as_str().unwrap();
        let (key, problem) = frozen
            .strip_prefix("Configuration error: ")
            .and_then(|rest| rest.split_once(' '))
            .unwrap();
        assert_eq!(problem, "must be empty or an ABI version such as 2.17\n");
        let line = config
            .lines()
            .position(|text| text.starts_with(key.rsplit('.').next().unwrap()))
            .unwrap()
            + 1;
        assert_eq!(
            String::from_utf8_lossy(&output.stderr),
            format!(
                "Configuration error: {}:{line}:1: {key}: {problem}",
                project.join("ici.toml").display()
            ),
            "{name}: stderr"
        );
    }
}
