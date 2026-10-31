use ici_domain::{RunEvent, read_events};
use ici_execution::{
    redaction::{REDACTED, redact_text, redact_value},
    storage::{WriteMode, append_events, atomic_write, atomic_write_with},
};
use std::{
    fs,
    io::{self, Write},
    os::unix::fs::symlink,
};
fn event(seq: u64) -> RunEvent {
    RunEvent {
        schema_id: "ici.next.event".into(),
        schema_version: 1,
        run_id: "run-one".into(),
        seq,
        event_type: "task.progress".into(),
        timestamp: "2026-10-28T12:00:00Z".into(),
        task_id: Some("task1".into()),
        component_id: None,
        message: "safe".into(),
    }
}
#[test]
fn atomic_replace_keeps_existing_bytes_on_write_failure_and_removes_stage() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("result.json");
    fs::write(&path, b"old-complete").unwrap();
    let result = atomic_write_with(&path, WriteMode::ReplaceRegular, |file| {
        file.write_all(b"partial")?;
        Err(io::Error::other("injected disk failure"))
    });
    assert!(result.is_err());
    assert_eq!(fs::read(&path).unwrap(), b"old-complete");
    assert_eq!(fs::read_dir(root.path()).unwrap().count(), 1);
    atomic_write(&path, b"new-complete", WriteMode::ReplaceRegular).unwrap();
    assert_eq!(fs::read(&path).unwrap(), b"new-complete");
    assert!(atomic_write(&path, b"must-not-replace", WriteMode::CreateNew).is_err());
    assert_eq!(fs::read(&path).unwrap(), b"new-complete");
}
#[test]
fn refuses_destination_and_ancestor_symlinks_without_touching_targets() {
    let root = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    let target = outside.path().join("existing");
    fs::write(&target, b"keep").unwrap();
    let link = root.path().join("link");
    symlink(&target, &link).unwrap();
    assert!(atomic_write(&link, b"wrong", WriteMode::ReplaceRegular).is_err());
    assert!(append_events(&link, &[event(1)]).is_err());
    assert!(link.is_symlink());
    assert_eq!(fs::read(&target).unwrap(), b"keep");
    let parent_link = root.path().join("parent");
    symlink(outside.path(), &parent_link).unwrap();
    assert!(atomic_write(&parent_link.join("new"), b"wrong", WriteMode::CreateNew).is_err());
    assert!(!outside.path().join("new").exists());
}
#[test]
fn destination_created_during_staging_is_never_overwritten_in_create_new_mode() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("new");
    let result = atomic_write_with(&path, WriteMode::CreateNew, |file| {
        file.write_all(b"candidate")?;
        fs::write(&path, b"concurrent-user-data")
    });
    assert!(result.is_err());
    assert_eq!(fs::read(&path).unwrap(), b"concurrent-user-data");
    assert_eq!(fs::read_dir(root.path()).unwrap().count(), 1);
}
#[test]
fn symlink_created_during_staging_is_refused_and_preserved() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("new");
    let target = root.path().join("target");
    fs::write(&target, b"keep").unwrap();
    let result = atomic_write_with(&path, WriteMode::ReplaceRegular, |file| {
        file.write_all(b"candidate")?;
        symlink(&target, &path)
    });
    assert!(result.is_err());
    assert!(path.is_symlink());
    assert_eq!(fs::read(&target).unwrap(), b"keep");
}
#[test]
fn event_stream_validates_run_and_sequence_before_appending_and_redacts_message() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("events.jsonl");
    let mut first = event(1);
    first.message = "token=fixture-secret".into();
    append_events(&path, &[first]).unwrap();
    append_events(&path, &[event(2), event(3)]).unwrap();
    let original = fs::read_to_string(&path).unwrap();
    let (events, notes) = read_events(&original).unwrap();
    assert_eq!(events.len(), 3);
    assert!(notes.is_empty());
    assert!(!original.contains("fixture-secret"));
    assert!(original.contains(REDACTED));
    assert!(append_events(&path, &[event(3)]).is_err());
    let mut different = event(4);
    different.run_id = "another-run".into();
    assert!(append_events(&path, &[different]).is_err());
    assert!(append_events(&path, &[event(4), event(6)]).is_err());
    assert_eq!(fs::read_to_string(&path).unwrap(), original);
    fs::write(&path, b"{truncated").unwrap();
    assert!(append_events(&path, &[event(4)]).is_err());
    assert_eq!(fs::read(&path).unwrap(), b"{truncated");
}
#[test]
fn redaction_preserves_structure_and_masks_quoted_values_keys_urls_and_private_keys() {
    let text = "password=abc secret='contains space' api_key=xyz Bearer BearerValue --token cli-secret https://user:pass@example.test/path";
    let redacted = redact_text(text);
    for secret in [
        "abc",
        "contains space",
        "xyz",
        "BearerValue",
        "cli-secret",
        "user:pass",
    ] {
        assert!(!redacted.contains(secret), "{redacted}");
    }
    assert!(redacted.contains("example.test/path"));
    let key = "-----BEGIN RSA PRIVATE KEY-----\nbody\n-----END RSA PRIVATE KEY-----";
    assert!(!redact_text(key).contains("body"));
    assert!(!redact_text("-----BEGIN PRIVATE KEY-----\ntruncated").contains("truncated"));
    let value = serde_json::json!({"token=aaa": ["token=one", 7], "token=bbb": {"normal": true}});
    let safe = redact_value(&value);
    assert_eq!(safe.as_object().unwrap().len(), 2);
    assert_eq!(safe[format!("token={REDACTED}")][1], 7);
    assert!(safe.get(format!("token={REDACTED}#2")).is_some());
    assert_eq!(
        redact_text("Unicode 경로: 파일.py:1"),
        "Unicode 경로: 파일.py:1"
    );
}
#[test]
fn event_append_refuses_locked_file_in_bounded_time_without_changing_bytes() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("events.jsonl");
    append_events(&path, &[event(1)]).unwrap();
    let original = fs::read(&path).unwrap();
    let held = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(&path)
        .unwrap();
    held.lock().unwrap();
    let started = std::time::Instant::now();
    let error = append_events(&path, &[event(2)]).unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::TimedOut);
    assert!(started.elapsed().as_secs_f64() < 3.0);
    assert_eq!(fs::read(path).unwrap(), original);
}
#[test]
fn saved_readers_distinguish_missing_invalid_and_truncated_results() {
    use ici_execution::storage::{read_event_stream, read_result};
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("result.json");
    assert!(read_result(&path).is_err());
    fs::write(&path, b"{}").unwrap();
    assert!(read_result(&path).is_err());
    fs::write(&path, b"\xff").unwrap();
    assert!(read_result(&path).is_err());
    let absent = root.path().join("events.jsonl");
    let (events, notes) = read_event_stream(&absent).unwrap();
    assert!(events.is_empty() && notes.len() == 1);
    let line = event(1).to_json().unwrap() + "\n{partial";
    fs::write(&absent, line).unwrap();
    let link = root.path().join("read-link");
    symlink(&absent, &link).unwrap();
    let (events, notes) = read_event_stream(&link).unwrap();
    assert_eq!(events.len(), 1);
    assert_eq!(notes.len(), 1);
}
#[test]
fn valid_result_roundtrip_and_invalid_result_do_not_replace_existing_file() {
    use ici_domain::RunResult;
    use ici_execution::storage::{read_result, write_result};
    let frozen = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../tests/rust-parity/domain/run-v2.json"
    ));
    let mut result = RunResult::from_json(frozen).unwrap();
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("result.json");
    write_result(&path, &result, WriteMode::CreateNew).unwrap();
    assert_eq!(read_result(&path).unwrap(), result);
    let old = fs::read(&path).unwrap();
    result.schema_version = 99;
    assert!(write_result(&path, &result, WriteMode::ReplaceRegular).is_err());
    assert_eq!(fs::read(path).unwrap(), old);
}
#[test]
fn bounded_regular_reader_rejects_fifo_and_large_file_without_hanging() {
    use ici_execution::storage::read_bounded_regular;
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("input");
    fs::write(&path, b"small input").unwrap();
    assert!(read_bounded_regular(&path, 3).is_err());
    assert_eq!(read_bounded_regular(&path, 11).unwrap(), b"small input");
    fs::remove_file(&path).unwrap();
    nix::unistd::mkfifo(
        &path,
        nix::sys::stat::Mode::S_IRUSR | nix::sys::stat::Mode::S_IWUSR,
    )
    .unwrap();
    let start = std::time::Instant::now();
    assert!(read_bounded_regular(&path, 100).is_err());
    assert!(start.elapsed().as_secs_f64() < 1.0);
}
