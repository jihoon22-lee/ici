//! Event task/component identity guard (frozen `ici.domain.events.RunEvent.__post_init__`
//! requires an identifier whenever the id is present).
use ici_domain::{EVENT_SCHEMA_ID, EVENT_SCHEMA_VERSION, RunEvent, validation::Validate};

fn event(kind: &str, task: Option<&str>, component: Option<&str>) -> RunEvent {
    RunEvent {
        schema_id: EVENT_SCHEMA_ID.into(),
        schema_version: EVENT_SCHEMA_VERSION,
        run_id: "run-1".into(),
        seq: 1,
        event_type: kind.into(),
        timestamp: "2026-10-31T00:00:00.000Z".into(),
        task_id: task.map(str::to_owned),
        component_id: component.map(str::to_owned),
        message: String::new(),
    }
}

#[test]
fn present_event_ids_are_nonempty_unpadded_printable_and_bounded() {
    // Positive: frozen-style ids, and native monorepo/case-preserving ids (intended difference).
    for (task, component) in [
        ("app.python.line", "app"),
        ("svc/api.python.lint", "svc/api"),
        ("Pkg.cpp.compile", "Pkg"),
    ] {
        assert!(
            event("task.started", Some(task), Some(component))
                .validate()
                .is_ok()
        );
    }
    // Negative: absent ids stay absent for run-level events.
    assert!(event("run.started", None, None).validate().is_ok());
    assert!(event("diagnostic", None, None).validate().is_ok());
    // Failure: an empty, padded, control-bearing or unbounded id, or a task event without a task.
    let long = "x".repeat(257);
    for bad in ["", " app", "app ", "a\u{7}b", long.as_str()] {
        assert!(
            event("task.started", Some(bad), None).validate().is_err(),
            "{bad:?}"
        );
        assert!(
            event("diagnostic", None, Some(bad)).validate().is_err(),
            "{bad:?}"
        );
    }
    assert!(
        event("task.completed", None, Some("app"))
            .validate()
            .is_err()
    );
}
