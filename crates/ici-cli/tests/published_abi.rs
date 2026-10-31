//! Verified publication selectors are independent from a C++ build declaration.
use ici_cli::{cpp_validation, execute, planning, registry};
use ici_domain::{CheckDefinition, GateVerdict, Profile, TaskState};
use ici_execution::Cancellation;
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path};
fn configuration(language: &str, extra: &str) -> String {
    let mut text = "schema_version=2\nprofile='deep'\n".to_owned();
    for check in ici_domain::builtin_catalog().checks {
        text.push_str(&format!(
            "[checks.\"{}\"]\nenabled={}\nrequired={}\n",
            check.id,
            check.id == "cpp.binary-compat" && language == "cpp",
            check.id == "cpp.binary-compat" && language == "cpp"
        ));
    }
    text.push_str(extra);
    text.push_str(&format!(
        "\n[[components]]\nid='project'\nroot='.'\nlanguages=['{language}']\nsources=[{}]\n",
        if language == "cpp" {
            "'main.cpp'"
        } else {
            "'app.py'"
        }
    ));
    text
}
fn fake_readelf(root: &Path) -> String {
    let path = root.join("readelf-proof-only");
    fs::write(
        &path,
        "#!/bin/sh\nprintf invoked > unexpected-readelf\nexit 93\n",
    )
    .unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
    path.to_string_lossy().into_owned()
}
fn python_check() -> CheckDefinition {
    CheckDefinition {
        id: "python.binary-compat".into(),
        title: "Published binary ABI compatibility".into(),
        language: "python".into(),
        tool: None,
        required: false,
        profiles: vec![Profile::Deep],
        needs: vec![],
        provides: vec![],
        is_static: false,
    }
}
#[test]
fn typed_report_python_selector_binds_canonical_identity_without_cpp_build_or_python_runtime() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("app.py"), "VALUE=1\n").unwrap();
    fs::write(root.path().join("report.txt"), "declared report\n").unwrap();
    fs::write(root.path().join("unpublished.txt"), "user data\n").unwrap();
    let tool = fake_readelf(root.path());
    for (selector, allowed) in [
        ("release:project:report.txt", true),
        ("report.txt", true),
        ("release:project:unpublished.txt", false),
        ("release:shadow:report.txt", false),
        ("release:project:../escape", false),
    ] {
        let text = configuration(
            "python",
            &format!(
                "[build.artifacts]\nreport=['report.txt']\n[engines.binary_compat]\nartifacts=[{}]\n[tools.readelf]\npath={}\n",
                serde_json::to_string(selector).unwrap(),
                serde_json::to_string(&tool).unwrap()
            ),
        );
        fs::write(root.path().join("ici.toml"), text).unwrap();
        let project =
            ici_config::resolve_with_environment(root.path(), None, None, &BTreeMap::new())
                .unwrap();
        let bound = cpp_validation::bind(
            &project,
            &project.components[0],
            &python_check(),
            "project.python.binary-compat",
            &ici_application::request::RunRequest::default(),
            &BTreeMap::new(),
            "fixture",
        )
        .unwrap();
        assert_eq!(!bound.plans.is_empty(), allowed, "{selector}");
        if allowed {
            assert_eq!(bound.plans[0].context.targets, vec!["report.txt"]);
            assert_eq!(bound.plans[0].context.tool.path, Path::new(&tool));
            assert!(bound.plans[0].context.source_files.is_empty());
        }
        assert!(!root.path().join(".ici").exists());
        assert!(!root.path().join("unexpected-readelf").exists());
    }
}
#[test]
fn published_launcher_selector_requires_unchanged_manifest_membership() {
    use ici_application::snapshot::{InputLimits, InputSnapshot};
    use ici_cli::artifact_build;
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("app.py"), "def main():\n    return 0\n").unwrap();
    fs::write(
        root.path().join("pyproject.toml"),
        "[project]\nname='example'\nversion='1.2.3'\n[project.scripts]\nexample='app:main'\n",
    )
    .unwrap();
    let tool = fake_readelf(root.path());
    let text = configuration(
        "python",
        &format!(
            "[engines.binary_compat]\nartifacts=['release:project:v1.2.3/x86_64/bin/example']\n[tools.readelf]\npath={}\n",
            serde_json::to_string(&tool).unwrap()
        ),
    );
    fs::write(root.path().join("ici.toml"), text).unwrap();
    let project =
        ici_config::resolve_with_environment(root.path(), None, None, &BTreeMap::new()).unwrap();
    let plan =
        artifact_build::describe(&project, &project.components[0], "project.python.artifact")
            .unwrap();
    let paths = plan
        .task
        .input_refs
        .iter()
        .map(|path| {
            Path::new(path)
                .strip_prefix(root.path())
                .unwrap()
                .to_string_lossy()
                .into_owned()
        })
        .collect::<Vec<_>>();
    let snapshot = InputSnapshot::take(root.path(), &paths, &[], InputLimits::default()).unwrap();
    let packaged = artifact_build::execute(&plan, &snapshot, &Cancellation::default());
    assert_eq!(
        packaged.observation.state,
        TaskState::Succeeded,
        "{packaged:?}"
    );
    let target = "v1.2.3/x86_64/bin/example";
    assert_eq!(
        artifact_build::published_artifacts(root.path()).unwrap()[target],
        "python-launcher"
    );
    let bind = || {
        cpp_validation::bind(
            &project,
            &project.components[0],
            &python_check(),
            "project.python.binary-compat",
            &ici_application::request::RunRequest::default(),
            &BTreeMap::new(),
            "manifest",
        )
        .unwrap()
    };
    let bound = bind();
    assert_eq!(bound.plans[0].context.targets, vec![target]);
    assert!(!root.path().join("unexpected-readelf").exists());
    // User edits invalidate the publication witness and are preserved byte for byte.
    let edited = b"#!/bin/sh\n# user edit\n";
    fs::write(root.path().join(target), edited).unwrap();
    assert!(bind().plans.is_empty());
    assert!(artifact_build::published_artifacts(root.path()).is_err());
    assert_eq!(fs::read(root.path().join(target)).unwrap(), edited);
    assert!(!root.path().join("unexpected-readelf").exists());
}
#[test]
fn cpp_explicit_report_without_build_executes_header_policy_instead_of_missing_contract() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    fs::write(root.path().join("report.txt"), "declared report\n").unwrap();
    let tool = fake_readelf(root.path());
    let text = configuration(
        "cpp",
        &format!(
            "[build.artifacts]\nreport=['report.txt']\n[engines.binary_compat]\nartifacts=['release:project:report.txt']\n[tools.readelf]\npath={}\n",
            serde_json::to_string(&tool).unwrap()
        ),
    );
    fs::write(root.path().join("ici.toml"), text).unwrap();
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let prepared = planning::prepare(&invocation, root.path(), &BTreeMap::new(), false).unwrap();
    assert!(!root.path().join(".ici").exists());
    assert_eq!(
        prepared.validations["project.cpp.binary-compat"][0]
            .context
            .targets,
        vec!["report.txt"]
    );
    let actual = execute::execute(
        &invocation,
        root.path(),
        &BTreeMap::new(),
        false,
        &Cancellation::default(),
    )
    .unwrap();
    let output = &actual.outputs["project.cpp.binary-compat"];
    assert_eq!(
        output.observation.state,
        TaskState::Succeeded,
        "{output:?}; {:?}",
        actual.result.gate
    );
    assert_eq!(actual.result.gate.selected, GateVerdict::Fail);
    assert!(
        actual
            .result
            .findings
            .iter()
            .any(|finding| finding.rule_id == "ici.binary.non-elf"
                && finding.primary_location.path == "report.txt")
    );
    assert!(!root.path().join("unexpected-readelf").exists());
}

#[test]
#[ignore = "explicit actual compiler/readelf Python-only declared publication ABI acceptance"]
fn actual_python_only_declared_elf_and_non_elf_need_no_cpp_build_or_project_python() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("dist")).unwrap();
    fs::write(root.path().join("app.py"), "VALUE=1\n").unwrap();
    fs::write(root.path().join("input.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(root.path().join("dist/report.txt"), "declared report\n").unwrap();
    let compiler = std::env::var("ICI_TEST_GXX").expect("explicit actual compiler");
    let readelf = std::env::var("ICI_TEST_READELF").expect("explicit actual readelf");
    assert!(
        std::process::Command::new(compiler)
            .args(["input.cpp", "-o", "dist/program"])
            .current_dir(root.path())
            .status()
            .unwrap()
            .success()
    );
    let configured = configuration(
        "python",
        &format!(
            "[checks.\"python.binary-compat\"]\nenabled=true\nrequired=true\n[build.artifacts]\nreport=['dist/*']\n[engines.binary_compat]\nartifacts=['release:project:dist/program']\nexpected_machine='ICI-IMPOSSIBLE-MACHINE'\n[tools.readelf]\npath={}\n",
            serde_json::to_string(&readelf).unwrap()
        ),
    );
    for (selector, allow_non_elf, rule) in [
        ("dist/program", false, Some("ici.binary.machine-mismatch")),
        ("dist/report.txt", false, Some("ici.binary.non-elf")),
        ("dist/report.txt", true, None),
    ] {
        let text = configured
            .replace(
                "release:project:dist/program",
                &format!("release:project:{selector}"),
            )
            .replace(
                "expected_machine='ICI-IMPOSSIBLE-MACHINE'",
                &format!(
                    "expected_machine='ICI-IMPOSSIBLE-MACHINE'\nallow_non_elf={allow_non_elf}"
                ),
            );
        // Artifact-only components remain inspectable without a production source file.
        let text = text.replace("sources=['app.py']", "sources=[]");
        fs::write(root.path().join("ici.toml"), text).unwrap();
        let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
        let prepared =
            planning::prepare(&invocation, root.path(), &BTreeMap::new(), false).unwrap();
        assert!(prepared.project.builds.is_empty());
        assert!(prepared.context_preparations.is_empty());
        assert_eq!(
            prepared.validations["project.python.binary-compat"][0]
                .context
                .targets,
            vec![selector]
        );
        let actual = execute::execute(
            &invocation,
            root.path(),
            &BTreeMap::new(),
            false,
            &Cancellation::default(),
        )
        .unwrap();
        let output = &actual.outputs["project.python.binary-compat"];
        assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
        assert_eq!(
            actual.result.gate.selected,
            if rule.is_some() {
                GateVerdict::Fail
            } else {
                GateVerdict::Pass
            }
        );
        if let Some(rule) = rule {
            assert!(
                actual
                    .result
                    .findings
                    .iter()
                    .any(|finding| finding.rule_id == rule
                        && finding.primary_location.path == selector),
                "{:?}",
                actual.result.findings
            );
        } else {
            assert!(actual.result.findings.is_empty());
        }
    }
    let missing = configured.replace(
        "release:project:dist/program",
        "release:project:dist/missing",
    );
    fs::write(root.path().join("ici.toml"), missing).unwrap();
    let prepared = planning::prepare(
        &registry::parse(["ici", "next", "verify"]).unwrap(),
        root.path(),
        &BTreeMap::new(),
        false,
    )
    .unwrap();
    assert!(
        prepared
            .plan
            .checks
            .iter()
            .find(|check| check.check.id == "python.binary-compat")
            .unwrap()
            .blocked()
            .is_some()
    );
}
