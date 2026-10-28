use ici_application::cache::{CacheIdentity, ObservationCache};
use ici_domain::{Observation, TaskKind, TaskSpec, sha256};
use std::{fs, os::unix::fs::symlink};

fn identity() -> CacheIdentity {
    CacheIdentity {
        core_digest: sha256("native executable bytes"),
        policy_digest: sha256("policy"),
        environment_digest: sha256("actual environment"),
        interpretation_digest: sha256("provider options"),
        input_digests: vec![("a.py".into(), sha256("source"))],
        inputs_complete: true,
    }
}
fn task() -> TaskSpec {
    let mut task = TaskSpec::command("task", "ruff", vec!["/tool/ruff".into()], "/project");
    task.cacheable = true;
    task.tool_digest = Some(sha256("ruff"));
    task.input_refs = vec!["a.py".into()];
    task
}
#[test]
fn executable_core_options_environment_and_every_input_participate_in_reuse() {
    let task = task();
    let first = identity().key(&task).unwrap().unwrap();
    for selector in 0..5 {
        let mut changed = identity();
        match selector {
            0 => changed.core_digest = sha256("new core"),
            1 => changed.policy_digest = sha256("new policy"),
            2 => changed.environment_digest = sha256("new environment"),
            3 => changed.interpretation_digest = sha256("new options"),
            _ => changed.input_digests[0].1 = sha256("changed file"),
        }
        assert_ne!(changed.key(&task).unwrap().unwrap(), first);
    }
    let mut replaced = task.clone();
    replaced.tool_digest = Some(sha256("replacement at same executable path"));
    assert_ne!(identity().key(&replaced).unwrap().unwrap(), first);
    let mut incomplete = identity();
    incomplete.inputs_complete = false;
    assert!(incomplete.key(&task).unwrap().is_none());
    incomplete.inputs_complete = true;
    incomplete.input_digests.clear();
    assert!(incomplete.key(&task).unwrap().is_none());
    let mut prepare = task.clone();
    prepare.kind = TaskKind::Prepare;
    assert!(identity().key(&prepare).unwrap().is_none());
    replaced.tool_digest = None;
    assert!(identity().key(&replaced).unwrap().is_none());
}
#[test]
fn cached_completion_is_validated_and_corruption_or_unknown_identity_is_not_a_hit() {
    let directory = tempfile::tempdir().unwrap();
    let cache = ObservationCache {
        directory: directory.path().join("native/v1/observations"),
        core_digest: identity().core_digest,
    };
    let key = identity().key(&task()).unwrap().unwrap();
    assert!(cache.get(&key, "new-task").unwrap().is_none());
    let observation = Observation::new("old-task", "ruff");
    assert!(cache.put(&key, &observation).unwrap());
    assert_eq!(
        cache.get(&key, "new-task").unwrap().unwrap().task_id,
        "new-task"
    );
    let different = ObservationCache {
        directory: cache.directory.clone(),
        core_digest: sha256("different core"),
    };
    assert!(different.get(&key, "task").is_err());
    let mut truncated = observation;
    truncated.truncated = true;
    assert!(!cache.put(&key, &truncated).unwrap());
    assert!(
        cache
            .get(&key, "task")
            .unwrap()
            .unwrap()
            .evidence_is_complete()
    );
    let file = cache
        .directory
        .join(format!("{}.json", key.strip_prefix("sha256:").unwrap()));
    fs::write(&file, "{truncated").unwrap();
    assert!(
        cache
            .get(&key, "task")
            .unwrap_err()
            .to_string()
            .contains("invalid cache")
    );
}
#[test]
fn clear_is_confined_to_exact_regular_native_entry_names() {
    let directory = tempfile::tempdir().unwrap();
    let cache = ObservationCache {
        directory: directory.path().join("observations"),
        core_digest: identity().core_digest,
    };
    let key = identity().key(&task()).unwrap().unwrap();
    cache.put(&key, &Observation::new("task", "ruff")).unwrap();
    fs::write(cache.directory.join("user-note.json"), "preserve").unwrap();
    fs::create_dir(cache.directory.join(format!("{}.json", "f".repeat(64)))).unwrap();
    let external = tempfile::NamedTempFile::new().unwrap();
    fs::write(external.path(), "outside").unwrap();
    symlink(
        external.path(),
        cache.directory.join(format!("{}.json", "e".repeat(64))),
    )
    .unwrap();
    let stats = cache.inventory(true).unwrap();
    assert_eq!(stats.entries, 1);
    assert_eq!(stats.unrecognized, 3);
    assert_eq!(fs::read_to_string(external.path()).unwrap(), "outside");
    assert_eq!(
        fs::read_to_string(cache.directory.join("user-note.json")).unwrap(),
        "preserve"
    );
    assert_eq!(cache.inventory(false).unwrap().entries, 0);
}
#[test]
fn cache_symlink_ancestor_and_fifo_are_refused_without_following_or_blocking() {
    let root = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    symlink(outside.path(), root.path().join("link")).unwrap();
    let linked = ObservationCache {
        directory: root.path().join("link"),
        core_digest: identity().core_digest.clone(),
    };
    let key = identity().key(&task()).unwrap().unwrap();
    assert!(linked.inventory(true).is_err());
    assert!(linked.get(&key, "task").is_err());
    assert!(linked.put(&key, &Observation::new("task", "ruff")).is_err());
    let cache = ObservationCache {
        directory: root.path().join("cache"),
        core_digest: identity().core_digest,
    };
    fs::create_dir(&cache.directory).unwrap();
    nix::unistd::mkfifo(
        &cache
            .directory
            .join(format!("{}.json", key.strip_prefix("sha256:").unwrap())),
        nix::sys::stat::Mode::S_IRUSR,
    )
    .unwrap();
    assert!(
        cache
            .get(&key, "task")
            .unwrap_err()
            .to_string()
            .contains("regular")
    );
    assert_eq!(cache.inventory(true).unwrap().unrecognized, 1);
}

#[test]
fn rich_cache_retains_inspected_targets_and_never_invents_them_from_old_observations() {
    use ici_domain::{AnalysisOutput, InspectionTarget};
    let directory = tempfile::tempdir().unwrap();
    let cache = ObservationCache {
        directory: directory.path().join("cache"),
        core_digest: identity().core_digest,
    };
    let key = identity().key(&task()).unwrap().unwrap();
    let mut output = AnalysisOutput::new("original", "ici");
    let mut target = InspectionTarget::at("a.py", 2);
    target.target_name = "café".into();
    target.metrics.insert("lines".into(), serde_json::json!(3));
    output.targets.push(target);
    output.extra = serde_json::json!({"denominator":5,"covered":3});
    assert!(cache.put(&key, &output.observation).unwrap());
    assert!(cache.get_output(&key, "logical").unwrap().is_none());
    assert!(cache.put_output(&key, &output).unwrap());
    let stored = cache.get_output(&key, "logical").unwrap().unwrap();
    assert_eq!(stored.observation.task_id, "logical");
    assert_eq!(stored.targets[0].target_name, "café");
    assert_eq!(stored.extra, output.extra);
    let entry = cache
        .directory
        .join(format!("{}.json", key.trim_start_matches("sha256:")));
    let mut corrupted: serde_json::Value =
        serde_json::from_slice(&fs::read(&entry).unwrap()).unwrap();
    corrupted["analysis"]["observation"]["provider"] = serde_json::json!("unrelated");
    fs::write(&entry, serde_json::to_vec(&corrupted).unwrap()).unwrap();
    assert!(
        cache
            .get_output(&key, "logical")
            .unwrap_err()
            .to_string()
            .contains("disagrees")
    );
    output.targets[0].start_line = 0;
    assert!(cache.put_output(&key, &output).is_err());
}
