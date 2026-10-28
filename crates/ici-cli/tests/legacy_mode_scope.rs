use ici_cli::{legacy, planning, registry};
use ici_domain::{AnalysisOutput, Finding, SourceSpan, TaskState};
use std::{collections::BTreeMap, fs};

#[test]
fn explicit_migrated_mode_applies_per_check_without_changing_modern_default() {
    for site in ["default", "root"] {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("src")).unwrap();
        fs::write(root.path().join("src/a.cpp"), "int value;\n").unwrap();
        let mode = if site == "root" {
            "[engines.line]\nmode='pass_warn'\n"
        } else {
            ""
        };
        let config = format!(
            "schema_version=2\n[checks.\"cpp.line\"]\nenabled=true\nrequired=true\n{mode}[[components]]\nid='project'\nroot='.'\nlanguages=['cpp']\nsources=['src/a.cpp']\n"
        );
        fs::write(root.path().join("ici.toml"), config).unwrap();
        let prepared = planning::prepare(
            &registry::parse(["ici", "next", "verify"]).unwrap(),
            root.path(),
            &BTreeMap::new(),
            false,
        )
        .unwrap();
        let line = prepared
            .plan
            .checks
            .iter()
            .find(|check| check.check.id == "cpp.line")
            .unwrap();
        let mut output = AnalysisOutput::new(&line.task_id, "ici-analysis");
        output.status = "FAIL".into();
        output.observation.state = TaskState::Succeeded;
        output.observation.findings.push(Finding::new(
            "ici.line.limit",
            "line",
            SourceSpan::line("src/a.cpp", 1),
            "ici-analysis",
        ));
        let mut outputs = BTreeMap::from([(line.task_id.clone(), output)]);
        legacy::apply_gate_policy(&prepared, &mut outputs);
        assert_eq!(
            outputs[&line.task_id].status,
            if site == "default" { "FAIL" } else { "WARN" }
        );
    }
}
