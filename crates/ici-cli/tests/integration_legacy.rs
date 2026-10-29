//! Frozen legacy IntegrationEngine parity: identity, targets, case table, bounds and artifacts.
use ici_application::{plan::Work, request::RunRequest};
use ici_cli::{commands, registry, validation};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/integration-legacy-oracle.json");

fn oracle() -> Value {
    serde_json::from_str(ORACLE).unwrap()
}
/// The frozen fixture with a POSIX shell as the named target: identities do not depend on argv.
fn fixture(config: &str) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("ici.toml"), config).unwrap();
    fs::write(root.path().join("main.py"), "def f():\n    return 1\n").unwrap();
    fs::write(root.path().join("contract.py"), "echo actual\n").unwrap();
    fs::write(root.path().join("advisory.py"), "exit 2\n").unwrap();
    root
}
fn frozen_config() -> String {
    oracle()["fixture_config"]
        .as_str()
        .unwrap()
        .replace("\"<PYTHON>\"", "\"/bin/sh\"")
}
fn verify(root: &Path) -> (i32, Value) {
    let output = commands::dispatch(
        &registry::parse([
            "ici",
            "verify",
            "--report",
            "--profile",
            "deep",
            "--no-cache",
        ])
        .unwrap(),
        root,
        &BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]),
    )
    .unwrap();
    let report: Value =
        serde_json::from_slice(&fs::read(root.join("verify_report.json")).unwrap()).unwrap();
    let engine = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "integration")
        .cloned()
        .unwrap();
    (output.exit_code, engine)
}
fn cases_without_duration(engine: &Value) -> Value {
    let mut cases = engine["extra"]["integration"].clone();
    for case in cases["cases"].as_array_mut().unwrap() {
        case.as_object_mut().unwrap().remove("duration_seconds");
    }
    cases
}

#[test]
fn legacy_verify_reproduces_frozen_integration_fingerprints_targets_and_case_table() {
    let expected = oracle();
    let root = fixture(&frozen_config());
    let (exit_code, engine) = verify(root.path());
    assert_eq!(exit_code, expected["exit_code"].as_i64().unwrap() as i32);
    let frozen = &expected["integration"];
    for key in ["status", "summary", "required", "evidence", "targets"] {
        assert_eq!(engine[key], frozen[key], "{key}");
    }
    assert_eq!(
        cases_without_duration(&engine),
        frozen["extra"]["integration"]
    );
    let actual: BTreeMap<_, _> = engine["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| (finding["fingerprint"].as_str().unwrap().to_owned(), finding))
        .collect();
    assert_eq!(actual.len(), frozen["findings"].as_array().unwrap().len());
    for finding in frozen["findings"].as_array().unwrap() {
        let native = actual[finding["fingerprint"].as_str().unwrap()];
        for key in [
            "rule_id",
            "severity",
            "confidence",
            "category",
            "message",
            "explanation",
            "remediation",
            "tool_rule_id",
            "primary_location",
        ] {
            assert_eq!(native[key], finding[key], "{key} of {}", finding["message"]);
        }
        if finding["rule_id"] == "ici.hybrid.process-contract" {
            assert_eq!(native["tool_name"], finding["tool_name"]);
        }
    }
    // The case scripts are user data; neither run deletes nor rewrites them.
    assert_eq!(
        fs::read_to_string(root.path().join("contract.py")).unwrap(),
        "echo actual\n"
    );
}

#[test]
fn advisory_case_before_required_failure_never_downgrades_the_engine_status() {
    let config = frozen_config();
    let (head, cases) = config.split_once("[[engines.integration.cases]]").unwrap();
    let (first, rest) = cases.split_once("[[engines.integration.cases]]").unwrap();
    let (second, tail) = rest.split_once("[engines.line]").unwrap();
    let reordered = format!(
        "{head}[[engines.integration.cases]]{second}[[engines.integration.cases]]{first}[engines.line]{tail}"
    );
    let root = fixture(&reordered);
    let (exit_code, engine) = verify(root.path());
    assert_eq!(exit_code, 1);
    assert_eq!(engine["status"], "FAIL");
    assert_eq!(
        engine["summary"],
        "Integration contracts: 2 run, 2 violation(s)"
    );
    let names: Vec<_> = engine["extra"]["integration"]["cases"]
        .as_array()
        .unwrap()
        .iter()
        .map(|case| (case["name"].clone(), case["status"].clone()))
        .collect();
    assert_eq!(
        names,
        vec![
            (json!("advisory two"), json!("WARN")),
            (json!("contract one"), json!("FAIL"))
        ]
    );
}

fn modern(component: &str) -> String {
    let mut text = "schema_version=2\nprofile='deep'\n".to_owned();
    for check in ici_domain::builtin_catalog().checks {
        text.push_str(&format!(
            "[checks.\"{}\"]\nenabled={}\nrequired={}\n",
            check.id,
            check.id == "integration",
            check.id == "integration"
        ));
    }
    text.push_str(
        "\n[[components]]\nid='project'\nroot='.'\nlanguages=['python']\nsources=['app.py']\n",
    );
    text.push_str(component);
    text
}
fn bind(
    project: &ici_config::ResolvedProject,
    environment: &BTreeMap<String, String>,
) -> validation::BoundValidation {
    let check = ici_domain::builtin_catalog()
        .checks
        .into_iter()
        .find(|check| check.id == "integration")
        .unwrap();
    validation::bind(
        project,
        &project.components[0],
        &check,
        "project.integration",
        &RunRequest::default(),
        environment,
        "fixture",
    )
    .unwrap()
}
fn blocked_reason(bound: &validation::BoundValidation) -> Option<String> {
    match &bound.work {
        Work::Blocked { reason } => Some(reason.clone()),
        _ => None,
    }
}

#[test]
fn frozen_transport_bounds_block_before_any_case_runs() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("app.py"), "VALUE=1\n").unwrap();
    let environment = BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]);
    let accepted = "[[components.integrations]]\nname='ok'\nargv=['{python:sh}']\n[components.integrations.python_targets]\nsh='/bin/sh'\n";
    let long = "x".repeat(1025);
    let many = vec!["'a'"; 64].join(",");
    let mut refused_by_schema = vec![];
    for (case, expected) in [
        (accepted.to_owned(), None),
        (
            format!("[[components.integrations]]\nname='argv'\nargv=['{{python:sh}}',{many}]\n[components.integrations.python_targets]\nsh='/bin/sh'\n"),
            Some("at most 64"),
        ),
        (
            format!("[[components.integrations]]\nname='token'\nargv=['{{python:sh}}','{long}']\n[components.integrations.python_targets]\nsh='/bin/sh'\n"),
            Some("at most 64"),
        ),
        (
            format!("[[components.integrations]]\nname='needle'\nargv=['{{python:sh}}']\nstdout_contains=['{long}']\n[components.integrations.python_targets]\nsh='/bin/sh'\n"),
            Some("stdout_contains"),
        ),
        (
            "[[components.integrations]]\nname='target'\nargv=['{python:-sh}']\n[components.integrations.python_targets]\n-sh='/bin/sh'\n".into(),
            Some("bounded name"),
        ),
        (
            "[[components.integrations]]\nname='inherit'\nargv=['{python:sh}']\ninherit_env=['1BAD']\n[components.integrations.python_targets]\nsh='/bin/sh'\n".into(),
            Some("environment name"),
        ),
        (
            "[[components.integrations]]\nname='glued'\nargv=['{python:sh}','--x={python:sh}']\n[components.integrations.python_targets]\nsh='/bin/sh'\n".into(),
            Some("whole tokens"),
        ),
        (
            "[[components.integrations]]\nname='unknown'\nargv=['{python:sh}','{service:db}']\n[components.integrations.python_targets]\nsh='/bin/sh'\n".into(),
            Some("unknown integration placeholder"),
        ),
    ] {
        fs::write(root.path().join("ici.toml"), modern(&case)).unwrap();
        let project = match ici_config::resolve_with_environment(
            root.path(),
            None,
            None,
            &environment,
        ) {
            Ok(project) => project,
            // The shared schema may refuse the same bound first; both stop before execution.
            Err(_) if expected.is_some() => {
                refused_by_schema.push(expected);
                continue;
            }
            Err(error) => panic!("{error}"),
        };
        let bound = bind(&project, &environment);
        match expected {
            None => assert!(blocked_reason(&bound).is_none(), "{:?}", blocked_reason(&bound)),
            Some(text) => assert!(
                blocked_reason(&bound).is_some_and(|reason| reason.contains(text)),
                "{text}: {:?}",
                blocked_reason(&bound)
            ),
        }
        assert!(!root.path().join(".ici").exists());
    }
    // The shared schema already refuses argv size/placeholder spelling at configuration load;
    // needle, target-name and inherited-name bounds are enforced by native binding.
    assert_eq!(
        refused_by_schema,
        vec![
            Some("at most 64"),
            Some("at most 64"),
            Some("whole tokens"),
            Some("unknown integration placeholder")
        ]
    );
}

#[test]
fn shadow_artifact_identities_resolve_only_observed_prepared_variant_outputs() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("app.py"), "VALUE=1\n").unwrap();
    let shadow = ".ici/rust-v1/builds/release";
    let deep = "nested/".repeat(80);
    for relative in ["bin/app", &format!("{deep}tool")] {
        let path = root.path().join(shadow).join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, "#!/bin/sh\necho from-shadow\n").unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
    }
    fs::write(root.path().join(shadow).join("bin/unlisted"), "#!/bin/sh\n").unwrap();
    let long_id = format!("release:shadow:{deep}tool");
    assert!(long_id.len() > 512);
    let hashed = format!(
        "release:shadow:path-{}",
        ici_domain::sha256(&long_id)
            .strip_prefix("sha256:")
            .unwrap()
    );
    let environment = BTreeMap::new();
    for (identity, expected, prepared) in [
        (
            "release:shadow:bin/app",
            Ok(format!("{shadow}/bin/app")),
            true,
        ),
        (hashed.as_str(), Ok(format!("{shadow}/{deep}tool")), true),
        (long_id.as_str(), Err("not an observed output"), true),
        (
            "release:shadow:bin/unlisted",
            Err("not an observed output"),
            true,
        ),
        (
            "coverage:shadow:bin/app",
            Err("no prepared native coverage build"),
            true,
        ),
        ("debug:shadow:bin/app", Err("unknown build variant"), true),
        (
            "release:shadow:bin/app",
            Err("no prepared native release build"),
            false,
        ),
    ] {
        fs::write(
            root.path().join("ici.toml"),
            modern(&format!(
                "[[components.integrations]]\nname='shadow'\nargv=[{}]\nstdout_contains=['from-shadow']\n",
                serde_json::to_string(&format!("{{artifact:{identity}}}")).unwrap()
            )),
        )
        .unwrap();
        let mut project =
            ici_config::resolve_with_environment(root.path(), None, None, &environment).unwrap();
        if prepared {
            project.components[0].settings["native_build_variants"]["release"] = json!({
                "build_id":"native-release","directory":shadow,
                "artifacts":[format!("{shadow}/bin/app"),format!("{shadow}/{deep}tool")]
            });
        }
        let bound = bind(&project, &environment);
        match expected {
            Ok(path) => {
                assert!(
                    blocked_reason(&bound).is_none(),
                    "{identity}: {:?}",
                    blocked_reason(&bound)
                );
                assert_eq!(
                    bound.plans[0].tasks[0].argv[0],
                    root.path().join(path).to_string_lossy()
                );
            }
            Err(text) => assert!(
                blocked_reason(&bound).is_some_and(|reason| reason.contains(text)),
                "{identity}: {:?}",
                blocked_reason(&bound)
            ),
        }
    }
}

#[test]
fn shadow_identity_selects_one_binary_compat_target_from_the_linked_build_inventory() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){}\n").unwrap();
    let shadow = ".ici/rust-v1/builds/release";
    fs::create_dir_all(root.path().join(shadow).join("bin")).unwrap();
    for name in ["app", "other"] {
        let mut bytes = b"\x7fELF\x02\x01\x01".to_vec();
        bytes.resize(64, 0);
        fs::write(root.path().join(shadow).join("bin").join(name), bytes).unwrap();
    }
    let readelf = root.path().join("readelf-proof-only");
    fs::write(&readelf, "#!/bin/sh\nexit 93\n").unwrap();
    fs::set_permissions(&readelf, fs::Permissions::from_mode(0o755)).unwrap();
    let check = ici_domain::builtin_catalog()
        .checks
        .into_iter()
        .find(|check| check.id == "cpp.binary-compat")
        .unwrap();
    for (selector, expected) in [
        (
            "release:shadow:bin/app",
            Ok(vec![format!("{shadow}/bin/app")]),
        ),
        ("release:shadow:bin/missing", Err("not an observed output")),
        (
            "sanitize:shadow:bin/app",
            Err("no prepared native sanitize build"),
        ),
    ] {
        let mut text = "schema_version=2\nprofile='deep'\n".to_owned();
        for item in ici_domain::builtin_catalog().checks {
            let on = item.id == "cpp.binary-compat";
            text.push_str(&format!(
                "[checks.\"{}\"]\nenabled={on}\nrequired={on}\n",
                item.id
            ));
        }
        text.push_str(&format!(
            "[builds.native]\ndirectory='{shadow}'\nartifacts=['bin/*']\n[engines.binary_compat]\nartifacts=[{}]\n[tools.readelf]\npath={}\n[[components]]\nid='project'\nroot='.'\nlanguages=['cpp']\nsources=['main.cpp']\nbuild='native'\n",
            serde_json::to_string(selector).unwrap(),
            serde_json::to_string(&readelf).unwrap()
        ));
        fs::write(root.path().join("ici.toml"), text).unwrap();
        let mut project =
            ici_config::resolve_with_environment(root.path(), None, None, &BTreeMap::new())
                .unwrap();
        project.components[0].settings["native_build_variants"]["release"] = json!({
            "build_id":"native","directory":shadow,
            "artifacts":[format!("{shadow}/bin/app"),format!("{shadow}/bin/other")]
        });
        let bound = ici_cli::cpp_validation::bind(
            &project,
            &project.components[0],
            &check,
            "project.cpp.binary-compat",
            &RunRequest::default(),
            &BTreeMap::new(),
            "fixture",
        )
        .unwrap();
        match expected {
            Ok(targets) => {
                assert!(bound.plans.len() == 1, "{selector}: {:?}", bound.work);
                assert_eq!(bound.plans[0].context.targets, targets);
            }
            Err(text) => match &bound.work {
                Work::Blocked { reason } => assert!(reason.contains(text), "{reason}"),
                other => panic!("{selector}: {other:?}"),
            },
        }
        assert!(!root.path().join(shadow).join("../../runs").exists());
    }
}
