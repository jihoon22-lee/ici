use ici_application::snapshot::{InputLimits, InputSnapshot};
use std::{fs, os::unix::fs::symlink};

#[test]
fn captured_bytes_are_immutable_and_added_removed_changed_inputs_are_distinct() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("a.py"), "first\n").unwrap();
    fs::write(dir.path().join("b.py"), "keep\n").unwrap();
    fs::write(dir.path().join("ici.toml"), "schema_version=2\n").unwrap();
    let paths = vec!["a.py".into(), "b.py".into(), "a.py".into()];
    let config = vec!["ici.toml".into()];
    let before = InputSnapshot::take(dir.path(), &paths, &config, InputLimits::default()).unwrap();
    assert_eq!(before.files.len(), 3);
    fs::write(dir.path().join("a.py"), "later\n").unwrap();
    fs::remove_file(dir.path().join("b.py")).unwrap();
    fs::write(dir.path().join("c.py"), "new\n").unwrap();
    let after = InputSnapshot::take(
        dir.path(),
        &["a.py".into(), "c.py".into()],
        &config,
        InputLimits::default(),
    )
    .unwrap();
    let drift = before.difference(&after);
    assert_eq!(drift.added, ["c.py"]);
    assert_eq!(drift.removed, ["b.py"]);
    assert_eq!(drift.changed, ["a.py"]);
    assert_eq!(before.files["a.py"].input.bytes, b"first\n");
    assert_ne!(before.digest, after.digest);
    assert_eq!(before.source(None, false).files, ["a.py", "b.py"]);
}
#[test]
fn oversized_aggregate_symlink_parent_and_fifo_are_refused() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("a.py"), "1234").unwrap();
    fs::write(dir.path().join("b.py"), "5678").unwrap();
    let limits = InputLimits {
        file_bytes: 4,
        total_bytes: 7,
    };
    assert!(
        InputSnapshot::take(dir.path(), &["a.py".into(), "b.py".into()], &[], limits)
            .unwrap_err()
            .to_string()
            .contains("limit")
    );
    symlink("a.py", dir.path().join("link.py")).unwrap();
    assert!(
        InputSnapshot::take(dir.path(), &["link.py".into()], &[], InputLimits::default()).is_err()
    );
    let outside = tempfile::tempdir().unwrap();
    fs::write(outside.path().join("other.py"), "outside").unwrap();
    symlink(outside.path(), dir.path().join("parent")).unwrap();
    assert!(
        InputSnapshot::take(
            dir.path(),
            &["parent/other.py".into()],
            &[],
            InputLimits::default()
        )
        .is_err()
    );
    nix::unistd::mkfifo(&dir.path().join("fifo.py"), nix::sys::stat::Mode::S_IRUSR).unwrap();
    assert!(
        InputSnapshot::take(dir.path(), &["fifo.py".into()], &[], InputLimits::default())
            .unwrap_err()
            .to_string()
            .contains("regular")
    );
    assert!(
        InputSnapshot::take(
            dir.path(),
            &["../other.py".into()],
            &[],
            InputLimits::default()
        )
        .is_err()
    );
}
#[test]
fn order_is_irrelevant_and_native_configuration_bytes_change_identity() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("a.py"), "x=1").unwrap();
    fs::write(dir.path().join("ruff.toml"), "select=[]").unwrap();
    let before = InputSnapshot::take(
        dir.path(),
        &["a.py".into()],
        &["ruff.toml".into()],
        InputLimits::default(),
    )
    .unwrap();
    let duplicate = InputSnapshot::take(
        dir.path(),
        &["a.py".into(), "a.py".into()],
        &["ruff.toml".into()],
        InputLimits::default(),
    )
    .unwrap();
    assert!(before.difference(&duplicate).stable());
    assert_eq!(before.digest, duplicate.digest);
    fs::write(dir.path().join("ruff.toml"), "select=['F']").unwrap();
    let after = InputSnapshot::take(
        dir.path(),
        &["a.py".into()],
        &["ruff.toml".into()],
        InputLimits::default(),
    )
    .unwrap();
    assert_eq!(before.difference(&after).changed, ["ruff.toml"]);
}
