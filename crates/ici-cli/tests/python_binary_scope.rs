use ici_cli::{execute, planning, registry};
use ici_domain::{EvidenceLevel, GateVerdict};
use ici_execution::Cancellation;
use std::{collections::BTreeMap, fs};

#[test]
fn python_only_binary_descriptor_is_located_not_applicable_without_artifacts_or_readelf() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("app.py"), "VALUE = 1\n").unwrap();
    let mut config = "schema_version=2\nprofile='deep'\n".to_owned();
    for check in ici_domain::builtin_catalog().checks {
        config.push_str(&format!("[checks.\"{}\"]\nenabled=false\n", check.id));
    }
    config.push_str("[engines.binary_compat]\nenabled=true\nrequired=false\n[[components]]\nid='project'\nroot='.'\nlanguages=['python']\nsources=['app.py']\n");
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let env = BTreeMap::from([("PATH".into(), String::new())]);
    let planned = planning::prepare(&invocation, root.path(), &env, false).unwrap();
    assert_eq!(
        planned
            .plan
            .checks
            .iter()
            .map(|check| check.check.id.as_str())
            .collect::<Vec<_>>(),
        vec!["python.binary-compat"]
    );
    let run = execute::execute(
        &invocation,
        root.path(),
        &env,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    let check = &run.result.checks.as_ref().unwrap()[0];
    assert!(check.evidence_complete);
    let output = &run.outputs[&check.task_id];
    assert_eq!(output.status, "SKIP");
    assert!(
        output
            .targets
            .iter()
            .any(|target| target.file_path == "ici.toml" && target.status == "SKIP")
    );
    assert!(
        output
            .observation
            .measurements
            .iter()
            .any(|metric| metric.evidence == EvidenceLevel::NotApplicable)
    );
    assert_eq!(run.result.gate.selected, GateVerdict::Pass);
}

#[test]
fn python_published_report_is_pinned_as_binary_input_and_uses_abi_header_policy() {
    use ici_cli::inputs;
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("app.py"), "VALUE = 1\n").unwrap();
    fs::write(root.path().join("report.txt"), "not an ELF binary\n").unwrap();
    let mut config = "schema_version=2\nprofile='deep'\n".to_owned();
    for check in ici_domain::builtin_catalog().checks {
        config.push_str(&format!("[checks.\"{}\"]\nenabled=false\n", check.id));
    }
    config.push_str("[build.artifacts]\nreport=['report.txt']\n[engines.binary_compat]\nenabled=true\nrequired=true\nartifacts=['release:project:report.txt']\n[[components]]\nid='project'\nroot='.'\nlanguages=['python']\nsources=['app.py']\n");
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let env = BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]);
    let planned = planning::prepare(&invocation, root.path(), &env, false).unwrap();
    assert_eq!(
        planned.validations["project.python.binary-compat"][0]
            .context
            .targets,
        vec!["report.txt"]
    );
    let snapshot = inputs::capture(&planned).unwrap();
    assert!(snapshot.files.contains_key("report.txt"));
    assert_eq!(
        inputs::binary_limits(&planned)["report.txt"],
        256 * 1024 * 1024
    );
    let run = execute::execute(
        &invocation,
        root.path(),
        &env,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert!(
        run.result
            .findings
            .iter()
            .any(|finding| finding.rule_id == "ici.binary.non-elf"
                && finding.primary_location.path == "report.txt")
    );
    assert_eq!(run.result.gate.selected, GateVerdict::Fail);
}

#[test]
fn hybrid_verified_artifact_has_one_binary_owner_and_no_phantom_build_dependency() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("app.py"), "VALUE=1\n").unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(root.path().join("report.txt"), "published report\n").unwrap();
    let mut config = "schema_version=2\nprofile='deep'\n".to_owned();
    for check in ici_domain::builtin_catalog().checks {
        let enabled = check.id == "cpp.binary-compat";
        config.push_str(&format!("[checks.\"{}\"]\nenabled={enabled}\n", check.id));
    }
    config.push_str("[build.artifacts]\nreport=['report.txt']\n[engines.binary_compat]\nenabled=true\nrequired=false\nartifacts=['release:project:report.txt']\n[[components]]\nid='cpp'\nroot='.'\nlanguages=['cpp']\nsources=['main.cpp']\n[[components]]\nid='python'\nroot='.'\nlanguages=['python']\nsources=['app.py']\n");
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let env = BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]);
    let plan = planning::prepare(&invocation, root.path(), &env, false).unwrap();
    assert_eq!(
        plan.plan
            .checks
            .iter()
            .map(|check| check.check.id.as_str())
            .collect::<Vec<_>>(),
        vec!["cpp.binary-compat"]
    );
    let selected = &plan.plan.checks[0];
    assert!(selected.check.needs.is_empty());
    assert!(plan.graph.blocked.is_empty(), "{:?}", plan.graph.blocked);
    assert_eq!(
        plan.validations[&selected.task_id][0].context.targets,
        vec!["report.txt"]
    );
}
