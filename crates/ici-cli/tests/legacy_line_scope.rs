use ici_cli::{commands::dispatch, inputs, planning, registry::parse};
use serde_json::Value;
use std::{collections::BTreeMap, fs};

fn project(kind: &str) -> tempfile::TempDir {
    let tree = tempfile::tempdir().unwrap();
    for dir in ["src", "scripts", "tests", "docs", "omit"] {
        fs::create_dir(tree.path().join(dir)).unwrap();
    }
    fs::write(tree.path().join("ici.toml"),format!("[project]\ntype='{kind}'\nsource_dirs=['src']\n[engines.line]\nwarn_limit=2\nfail_limit=4\ninclude_dirs=['scripts']\ngate_dirs=['src']\nexclude_dirs=['omit']\n")).unwrap();
    fs::write(tree.path().join("src/a.cpp"), "int a;\nint b;\nint c;\n").unwrap();
    fs::write(tree.path().join("src/equal.cpp"), "int a;\nint b;\n").unwrap();
    fs::write(
        tree.path().join("scripts/run.sh"),
        "# comment\na\nb\nc\nd\n",
    )
    .unwrap();
    fs::write(tree.path().join("tests/helper.py"), "a\nb\nc\nd\ne\nf\n").unwrap();
    fs::write(tree.path().join("docs/guide.md"), "one\ntwo\nthree\nfour\n").unwrap();
    fs::write(tree.path().join("omit/bad.cpp"), "x;\n".repeat(20)).unwrap();
    tree
}

#[test]
fn legacy_scope_keeps_source_gate_and_extra_inputs_distinct_without_double_counting() {
    let tree = project("hybrid");
    fs::write(tree.path().join("src/main.py"), "value=1\n").unwrap();
    let prepared = planning::prepare(
        &parse(["ici", "line"]).unwrap(),
        tree.path(),
        &BTreeMap::new(),
        false,
    )
    .unwrap();
    let all: Vec<_> = prepared
        .legacy_lines
        .values()
        .flat_map(|scope| &scope.inputs)
        .collect();
    let unique: std::collections::BTreeSet<_> = all.iter().copied().collect();
    assert_eq!(all.len(), unique.len());
    assert!(unique.contains(&"scripts/run.sh".to_string()));
    assert!(unique.contains(&"docs/guide.md".to_string()));
    assert!(!unique.contains(&"omit/bad.cpp".to_string()));
    let snapshot = inputs::capture(&prepared).unwrap();
    for check in &prepared.plan.checks {
        let request = inputs::analysis_request(&prepared, check, &snapshot, true);
        assert_eq!(
            request.inputs.len(),
            prepared.legacy_lines[&check.task_id].inputs.len()
        );
        assert_eq!(
            request.options["gated_paths"],
            serde_json::json!(prepared.legacy_lines[&check.task_id].gated_paths)
        );
    }
    let python = &prepared.legacy_lines["project.python.line"];
    assert!(python.source_paths.contains(&"scripts/run.sh".into()));
    assert!(!python.gated_paths.contains(&"scripts/run.sh".into()));
    assert!(!python.source_paths.contains(&"tests/helper.py".into()));
    dispatch(
        &parse(["ici", "line", "--report"]).unwrap(),
        tree.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    let report: Value =
        serde_json::from_slice(&fs::read(tree.path().join("line_report.json")).unwrap()).unwrap();
    assert_eq!(report["extra"]["code"], 10);
    assert_eq!(
        report["extra"]["files_data"].as_array().unwrap().len(),
        unique.len()
    );
    assert!(
        report["extra"]["top_files"]
            .as_array()
            .unwrap()
            .iter()
            .all(|row| row["scope"] == "source")
    );
}

#[test]
fn actual_cpp_line_command_retains_extras_labels_and_scoped_thresholds() {
    let tree = project("cpp");
    let before = fs::read(tree.path().join("scripts/run.sh")).unwrap();
    let invocation = parse(["ici", "line", "--report"]).unwrap();
    for _ in 0..2 {
        let output = dispatch(&invocation, tree.path(), &BTreeMap::new()).unwrap();
        assert_eq!(output.exit_code, 0, "{}", output.stdout);
        let report: Value =
            serde_json::from_slice(&fs::read(tree.path().join("line_report.json")).unwrap())
                .unwrap();
        let rows = report["extra"]["files_data"].as_array().unwrap();
        let row = |path: &str| rows.iter().find(|row| row["path"] == path).unwrap();
        assert_eq!(row("src/a.cpp")["status"], "WARN");
        assert_eq!(row("src/equal.cpp")["status"], "PASS");
        assert_eq!(row("scripts/run.sh")["status"], "PASS");
        assert_eq!(row("scripts/run.sh")["lang"], "Shell");
        assert_eq!(row("scripts/run.sh")["scope"], "source");
        assert_eq!(row("tests/helper.py")["scope"], "extra");
        assert_eq!(row("docs/guide.md")["lang"], "Markdown");
        assert!(
            !rows
                .iter()
                .any(|row| row["path"] == "omit/bad.cpp" || row["path"] == "line_report.json")
        );
        assert_eq!(report["extra"]["code"], 9);
        assert!(
            report["extra"]["top_files"]
                .as_array()
                .unwrap()
                .iter()
                .all(|row| row["scope"] == "source")
        );
    }
    assert_eq!(
        fs::read(tree.path().join("scripts/run.sh")).unwrap(),
        before
    );
}

#[test]
fn legacy_warn_and_fail_modes_keep_measured_findings_and_their_exit_contract() {
    for (mode, lines, exit, status) in [
        ("pass_warn_fail", 3, 0, "WARN"),
        ("pass_fail", 3, 1, "FAIL"),
        ("pass_warn", 5, 0, "WARN"),
    ] {
        let tree = project("cpp");
        let path = tree.path().join("ici.toml");
        let config = fs::read_to_string(&path).unwrap() + &format!("mode='{mode}'\n");
        fs::write(&path, config).unwrap();
        fs::write(tree.path().join("src/a.cpp"), "int value;\n".repeat(lines)).unwrap();
        let output = dispatch(
            &parse(["ici", "line", "--report"]).unwrap(),
            tree.path(),
            &BTreeMap::new(),
        )
        .unwrap();
        assert_eq!(output.exit_code, exit, "{mode}: {}", output.stdout);
        let report: Value =
            serde_json::from_slice(&fs::read(tree.path().join("line_report.json")).unwrap())
                .unwrap();
        assert_eq!(report["status"], status, "{mode}");
        let findings = report["native_run"]["findings"].as_array().unwrap();
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0]["evidence"], "MEASURED");
        let advisory = findings[0]["tags"]
            .as_array()
            .is_some_and(|tags| tags.iter().any(|tag| tag == "legacy-policy-advisory"));
        assert_eq!(advisory, exit == 0);
    }
}

#[test]
fn explicit_fail_on_new_keeps_warning_actionable_without_failing_ordinary_gate() {
    let tree = project("cpp");
    let path = tree.path().join("ici.toml");
    let mut config = fs::read_to_string(&path).unwrap();
    for descriptor in ici_domain::builtin_catalog().legacy {
        if descriptor.name != "line" {
            config.push_str(&format!("\n[engines.{}]\nenabled=false\n", descriptor.name));
        }
    }
    fs::write(path, config).unwrap();
    fs::write(tree.path().join("src/a.cpp"), "int a;\nint b;\n").unwrap();
    let first = dispatch(
        &parse(["ici", "verify", "--write-baseline", "baseline.json"]).unwrap(),
        tree.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(first.exit_code, 0, "{}", first.stdout);
    let baseline = fs::read(tree.path().join("baseline.json")).unwrap();
    fs::write(tree.path().join("src/a.cpp"), "int a;\nint b;\nint c;\n").unwrap();
    let second = dispatch(
        &parse([
            "ici",
            "verify",
            "--baseline",
            "baseline.json",
            "--fail-on-new",
        ])
        .unwrap(),
        tree.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(second.exit_code, 1, "{}", second.stdout);
    let result: Value =
        serde_json::from_slice(&fs::read(tree.path().join(ici_cli::DEFAULT_RESULT)).unwrap())
            .unwrap();
    assert_eq!(result["gate"]["selected"], "PASS");
    assert_eq!(result["baseline"]["state"], "comparable");
    assert_eq!(result["baseline"]["new"].as_array().unwrap().len(), 1);
    assert_eq!(
        fs::read(tree.path().join("baseline.json")).unwrap(),
        baseline
    );
}
