use ici_application::{
    graph::TaskGraph,
    plan::{Work, plan},
    request::RunRequest,
};
use ici_config::{ResolvedProject, resolve_with_environment};
use ici_domain::{Profile, TaskKind, TaskSpec};
use std::{collections::BTreeMap, fs};

fn project(config: Option<&str>) -> (tempfile::TempDir, ResolvedProject) {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("main.py"), "x = 1\n").unwrap();
    if let Some(text) = config {
        fs::write(dir.path().join("ici.toml"), text).unwrap();
    }
    let project = resolve_with_environment(dir.path(), None, None, &BTreeMap::new()).unwrap();
    (dir, project)
}

#[test]
fn frozen_static_selection_and_minimal_init_are_equivalent() {
    let (_a, automatic) = project(None);
    let (_b, minimal) = project(Some("schema_version = 2\n"));
    let request = RunRequest {
        static_only: true,
        profile: Some(Profile::Fast),
        ..Default::default()
    };
    let first = plan(&automatic, &request, |_, _, _| Ok(Work::Internal)).unwrap();
    let second = plan(&minimal, &request, |_, _, _| Ok(Work::Internal)).unwrap();
    let ids: Vec<_> = first.checks.iter().map(|p| p.check.id.as_str()).collect();
    // Fixed published v0.13 oracle plan, preserving every static Python check.
    assert_eq!(
        ids,
        [
            "python.line",
            "python.lint",
            "python.format",
            "python.type",
            "python.complexity",
            "python.cognitive",
            "python.cycle",
            "python.dup",
            "python.security",
            "python.resource",
            "python.exception",
            "python.dead",
            "python.compat"
        ]
    );
    assert_eq!(
        ids,
        second
            .checks
            .iter()
            .map(|p| p.check.id.as_str())
            .collect::<Vec<_>>()
    );
    assert!(first.checks.iter().all(|p| p.check.required));
}

#[test]
fn language_filter_keeps_hybrid_model_and_reports_required_gap() {
    let (_dir, mut project) = project(None);
    project.components[0].languages.push("cpp".into());
    project.components[0].files.push("main.cpp".into());
    let request = RunRequest {
        languages: vec!["python".into()],
        require_full: true,
        static_only: true,
        ..Default::default()
    };
    let selected = plan(&project, &request, |_, _, _| Ok(Work::Internal)).unwrap();
    assert_eq!(project.components[0].languages, ["python", "cpp"]);
    assert_eq!(selected.languages, ["python"]);
    assert!(selected.checks.iter().all(|c| c.check.language == "python"));
    assert!(selected.coverage_gaps.iter().any(|g| g.contains("cpp")));
    let unknown = RunRequest {
        components: vec!["ghost".into()],
        ..Default::default()
    };
    assert!(
        plan(&project, &unknown, |_, _, _| panic!(
            "invalid selection must precede binding"
        ))
        .is_err()
    );
}

#[test]
fn disabled_check_never_reaches_tool_binding_and_one_value_override_preserves_required() {
    let (_dir, project) = project(Some(
        "schema_version = 2\n[checks.\"python.lint\"]\nenabled = false\n[checks.\"python.type\"]\nenabled = true\n",
    ));
    let selected = plan(
        &project,
        &RunRequest {
            static_only: true,
            ..Default::default()
        },
        |_, c, _| {
            assert_ne!(c.id, "python.lint");
            if c.id == "python.type" {
                Ok(Work::Blocked {
                    reason: "type tool is not available".into(),
                })
            } else {
                Ok(Work::Internal)
            }
        },
    )
    .unwrap();
    assert!(
        selected
            .omitted
            .iter()
            .any(|o| o.check_id == "python.lint" && o.reason == "disabled")
    );
    let type_check = selected
        .checks
        .iter()
        .find(|p| p.check.id == "python.type")
        .unwrap();
    assert!(type_check.check.required);
    assert_eq!(type_check.blocked(), Some("type tool is not available"));
}

#[test]
fn prepare_under_fast_is_blocked_and_empty_source_does_not_bind() {
    let (_dir, mut project) = project(None);
    let request = RunRequest {
        profile: Some(Profile::Fast),
        static_only: true,
        ..Default::default()
    };
    let selected = plan(&project, &request, |_, c, id| {
        if c.id == "python.lint" {
            let mut task = TaskSpec::command(id, "synthetic", vec!["tool".into()], "/project");
            task.kind = TaskKind::Prepare;
            Ok(Work::process(task))
        } else {
            Ok(Work::Internal)
        }
    })
    .unwrap();
    assert!(
        selected
            .checks
            .iter()
            .find(|c| c.check.id == "python.lint")
            .unwrap()
            .blocked()
            .unwrap()
            .contains("fast")
    );
    project.components[0].files.clear();
    let empty = plan(&project, &request, |_, _, _| {
        panic!("no source must precede binding")
    })
    .unwrap();
    assert!(
        empty
            .checks
            .iter()
            .all(|c| c.blocked().unwrap().contains("no sources"))
    );
    let none = RunRequest {
        languages: vec!["rust".into()],
        ..Default::default()
    };
    assert!(plan(&project, &none, |_, _, _| panic!("wrong language")).is_err());
}

fn graph_fixture() -> Vec<ici_application::plan::PlannedCheck> {
    let (_dir, project) = project(None);
    plan(&project, &RunRequest::default(), |_, _, _| {
        Ok(Work::Internal)
    })
    .unwrap()
    .checks
}

#[test]
fn capabilities_order_work_and_missing_or_blocked_producer_propagates() {
    let mut checks = graph_fixture();
    checks.retain(|c| matches!(c.check.id.as_str(), "python.test" | "python.coverage"));
    let graph = TaskGraph::build(&checks).unwrap();
    assert_eq!(
        graph.layers,
        [vec!["project.python.test"], vec!["project.python.coverage"]]
    );
    checks[0].work = Work::Blocked {
        reason: "pytest missing".into(),
    };
    let graph = TaskGraph::build(&checks).unwrap();
    assert!(graph.layers.is_empty());
    assert!(graph.blocked["project.python.coverage"].contains("prerequisite project.python.test"));
    checks.remove(0);
    assert!(
        TaskGraph::build(&checks).unwrap().blocked["project.python.coverage"]
            .contains("no selected check produces")
    );
}

#[test]
fn invalid_graph_is_rejected_before_execution_even_when_blocked() {
    let mut checks = graph_fixture();
    checks.truncate(2);
    checks[0].check.provides = vec!["a".into()];
    checks[0].check.needs = vec!["b".into()];
    checks[1].check.provides = vec!["b".into()];
    checks[1].check.needs = vec!["a".into()];
    checks[0].work = Work::Blocked {
        reason: "tool missing".into(),
    };
    assert!(
        TaskGraph::build(&checks)
            .unwrap_err()
            .to_string()
            .contains("cycle")
    );
    checks[0].check.needs.clear();
    checks[1].check.provides = vec!["a".into()];
    assert!(
        TaskGraph::build(&checks)
            .unwrap_err()
            .to_string()
            .contains("produced by both")
    );
}

#[test]
fn task_identity_and_unknown_explicit_dependency_are_rejected() {
    let (_dir, project) = project(None);
    let request = RunRequest {
        static_only: true,
        ..Default::default()
    };
    let mismatch = plan(&project, &request, |_, _, _| {
        Ok(Work::Process {
            advisory: false,
            task: Box::new(TaskSpec::command(
                "wrong",
                "tool",
                vec!["tool".into()],
                "/project",
            )),
        })
    });
    assert!(mismatch.unwrap_err().to_string().contains("identity"));
    let mut checks = graph_fixture();
    let mut task = TaskSpec::command(&checks[0].task_id, "tool", vec!["tool".into()], "/project");
    task.depends_on = vec!["not-in-plan".into()];
    checks[0].work = Work::process(task);
    assert!(
        TaskGraph::build(&checks)
            .unwrap_err()
            .to_string()
            .contains("unknown task")
    );
}
