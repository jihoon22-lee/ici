use ici_application::identity::{InvocationIdentity, ScopeIdentity, policy_digest};
use ici_domain::{TaskSpec, sha256};
use serde_json::json;
use std::{collections::BTreeMap, fs, path::Path};
#[test]
fn native_policy_ignores_display_cache_tool_paths_and_suppression_reason_but_keeps_thresholds() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("main.py"), "x=1").unwrap();
    let mut project =
        ici_config::resolve_with_environment(dir.path(), None, None, &BTreeMap::new()).unwrap();
    let original = policy_digest(&project).unwrap();
    project.effective["display"] = json!({"color":"never"});
    project.effective["cache"] = json!({"path":"/other/cache"});
    project.tools.insert(
        "ruff".into(),
        ici_config::ToolSetting {
            id: "ruff".into(),
            source: None,
            path: Some("/other/ruff".into()),
            config: None,
            settings: json!({}),
        },
    );
    assert_eq!(policy_digest(&project).unwrap(), original);
    project.components[0].settings["engines"]["complexity"]["fail_cc"] = json!(100);
    assert_ne!(policy_digest(&project).unwrap(), original);
}
#[test]
fn scope_changes_for_configuration_and_source_selection_not_source_contents() {
    let mut scope = ScopeIdentity {
        check_id: "javascript.lint".into(),
        component_id: "app".into(),
        language: "javascript".into(),
        files: vec!["main.js".into()],
        source_roots: vec![".".into()],
        native_configuration: BTreeMap::from([("eslint.config.js".into(), sha256("rule enabled"))]),
        context: json!({}),
        invocation: None,
    };
    let original = scope.digest().unwrap();
    // Source content deliberately has no scope-identity field: a changed file is rechecked via
    // content-addressed cache inputs, while a complete matching scope may prove a fix.
    scope
        .native_configuration
        .insert("eslint.config.js".into(), sha256("rule disabled"));
    assert_ne!(scope.digest().unwrap(), original);
    scope
        .native_configuration
        .insert("eslint.config.js".into(), sha256("rule enabled"));
    scope.files.push("another.js".into());
    assert_ne!(scope.digest().unwrap(), original);
}
#[test]
fn invocation_relocation_keeps_options_quoted_paths_and_external_prefix_boundaries() {
    let mut task = TaskSpec::command(
        "task",
        "compiler",
        vec![
            "/old/project/.venv/bin/tool".into(),
            "-I/old/project/include".into(),
            "--config='/old/project/tool.toml'".into(),
            "@/old/project/args.rsp".into(),
            "/old/project-other/a.cpp".into(),
        ],
        "/old/project/src",
    );
    let first = InvocationIdentity::new(
        &task,
        &BTreeMap::from([("PATH".into(), "/old/project/bin:/usr/bin".into())]),
        Path::new("/old/project"),
    );
    assert_eq!(first.argv[1], "-I${workspace}/include");
    assert_eq!(first.argv[2], "--config='${workspace}/tool.toml'");
    assert_eq!(first.argv[4], "/old/project-other/a.cpp");
    assert_eq!(first.environment["PATH"], "${workspace}/bin:/usr/bin");
    task.argv = task
        .argv
        .iter()
        .map(|value| value.replace("/old/project/", "/new/project/"))
        .collect();
    task.cwd = "/new/project/src".into();
    let second = InvocationIdentity::new(
        &task,
        &BTreeMap::from([("PATH".into(), "/new/project/bin:/usr/bin".into())]),
        Path::new("/new/project"),
    );
    assert_eq!(
        serde_json::to_value(first).unwrap(),
        serde_json::to_value(second).unwrap()
    );
}
