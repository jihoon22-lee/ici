use ici_domain::{TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::*;
use std::{collections::BTreeMap, path::Path};
fn request(root: &Path) -> ValidationRequest {
    let tool = ResolvedTool {
        id: "python".into(),
        path: "/project/python".into(),
        source: ToolSource::Explicit,
        digest: sha256("fixture"),
        version: None,
        configuration: vec![],
    };
    ValidationRequest {
        kind: ValidationKind::PythonRuntime,
        task_id: "component.python.runtime".into(),
        component_id: "component".into(),
        root: root.into(),
        cwd: root.into(),
        owned_run_dir: root.join("run"),
        targets: vec![],
        source_files: vec!["main.py".into()],
        functions: vec![],
        options: ValidationOptions {
            runtimes: vec![
                RuntimeSpec {
                    configured: "new".into(),
                    tool: Some(tool.clone()),
                    required: true,
                },
                RuntimeSpec {
                    configured: "missing-optional".into(),
                    tool: None,
                    required: false,
                },
            ],
            ..Default::default()
        },
        tool,
    }
}
fn metadata(id: &str) -> TaskOutcome {
    let mut result = TaskOutcome::empty(id);
    result.state = TaskState::Succeeded;
    result.exit_code = Some(0);
    result.stdout=br#"{"version":[3,14,7],"implementation":"cpython","platform":"linux","machine":"x86_64","soabi":"cpython-314-x86_64-linux-gnu","ext_suffix":".cpython-314-x86_64-linux-gnu.so"}"#.to_vec();
    result
}
#[test]
fn runtime_matrix_keeps_missing_optional_and_required_subset_without_fallback() {
    let root = tempfile::tempdir().unwrap();
    let request = request(root.path());
    let planned = plan(&request).unwrap();
    assert_eq!(planned.tasks.len(), 1);
    assert_eq!(planned.runtime_plans.len(), 2);
    assert_eq!(planned.tasks[0].argv[0], "/project/python");
    let output = observe(
        &planned,
        &BTreeMap::from([(planned.tasks[0].id.clone(), metadata(&planned.tasks[0].id))]),
        &CapturedArtifacts::default(),
    );
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert!(
        output.targets.iter().any(
            |target| target.target_name.contains("missing-optional") && target.status == "WARN"
        )
    );
    let mut required = request.clone();
    required.options.required_interpreters = vec!["missing-optional".into()];
    let planned = plan(&required).unwrap();
    let output = observe(
        &planned,
        &BTreeMap::from([(planned.tasks[0].id.clone(), metadata(&planned.tasks[0].id))]),
        &CapturedArtifacts::default(),
    );
    assert_eq!(output.observation.state, TaskState::Failed);
    assert!(output.observation.findings.is_empty());
    required.options.required_interpreters = vec!["unconfigured".into()];
    assert!(plan(&required).is_err());
}
#[test]
fn optional_runtime_violations_remain_measured_advisory_and_required_metadata_is_enriched() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path());
    request.options.runtimes[0].required = false;
    request.options.runtimes[1].tool = Some(request.tool.clone());
    request.options.runtimes[1].required = true;
    let mut planned = plan(&request).unwrap();
    planned.context.options.requires_python = Some(">=3.12".into());
    let mut old = metadata(&planned.tasks[0].id);
    old.stdout = String::from_utf8(old.stdout)
        .unwrap()
        .replace("3,14,7", "3,10,21")
        .into_bytes();
    let outcomes = BTreeMap::from([
        (planned.tasks[0].id.clone(), old),
        (planned.tasks[1].id.clone(), metadata(&planned.tasks[1].id)),
    ]);
    let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert_eq!(output.observation.findings.len(), 1);
    assert!(
        output.observation.findings[0]
            .tags
            .iter()
            .any(|tag| tag == "advisory")
    );
    assert_eq!(
        output.observation.findings[0].rule_id,
        "python.compat.runtime-version"
    );
}
#[test]
fn compileall_source_lines_and_sorry_diagnostics_remain_when_runtime_metadata_is_unavailable() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path());
    request.options.runtimes.clear();
    request.options.compile_sources = true;
    let planned = plan(&request).unwrap();
    let metadata_id = planned.tasks[0].id.clone();
    let compile_id = planned.tasks[1].id.clone();
    let mut invalid = metadata(&metadata_id);
    invalid.stdout = b"not metadata".to_vec();
    let mut compilation = metadata(&compile_id);
    compilation.exit_code = Some(1);
    compilation.stdout=format!("*** Error compiling '{}'...\n  File \"{}\", line 3\n    def broken(:\nSyntaxError: invalid syntax\n",root.path().join("main.py").display(),root.path().join("main.py").display()).into_bytes();
    let output = observe(
        &planned,
        &BTreeMap::from([
            (metadata_id.clone(), invalid),
            (compile_id.clone(), compilation),
        ]),
        &CapturedArtifacts::default(),
    );
    assert_eq!(output.observation.state, TaskState::Failed);
    assert_eq!(output.observation.findings.len(), 1);
    assert_eq!(
        output.observation.findings[0].primary_location.start_line,
        3
    );
    assert!(
        output.observation.findings[0]
            .message
            .starts_with("SyntaxError:")
    );
    let mut sorry = metadata(&compile_id);
    sorry.exit_code = Some(1);
    sorry.stdout = b"*** Sorry: IndentationError: unexpected indent (main.py, line 7)\n".to_vec();
    let output = observe(
        &planned,
        &BTreeMap::from([
            (metadata_id.clone(), metadata(&metadata_id)),
            (compile_id, sorry),
        ]),
        &CapturedArtifacts::default(),
    );
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert_eq!(
        output.observation.findings[0].primary_location.start_line,
        7
    );
    assert!(
        output.observation.findings[0]
            .message
            .starts_with("IndentationError:")
    );
}
