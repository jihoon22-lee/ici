use ici_application::snapshot::{InputLimits, InputSnapshot};
use std::{fs, os::unix::fs::symlink};

#[test]
fn binary_allowance_preserves_source_aggregate_and_no_follow_limits() {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir(dir.path().join("generated")).unwrap();
    fs::write(dir.path().join("user.py"), "1234").unwrap();
    fs::write(dir.path().join("generated/app"), "12345678").unwrap();
    let limits = InputLimits {
        file_bytes: 4,
        total_bytes: 12,
    };
    let allowances = std::collections::BTreeMap::from([("generated/app".into(), 8)]);
    let before = InputSnapshot::take(dir.path(), &["user.py".into()], &[], limits).unwrap();
    let capture = || {
        InputSnapshot::take_with_path_limits(
            dir.path(),
            &["user.py".into()],
            &["generated/app".into()],
            limits,
            &allowances,
        )
    };
    let after = capture().unwrap();
    let (merged, drift) = before
        .after_preparation_with_path_limits(&after, &["generated".into()], limits, &allowances)
        .unwrap();
    assert!(drift.stable());
    assert_eq!(merged.total_bytes, 12);
    assert_eq!(merged.source(None, false).files, ["user.py"]);
    fs::write(dir.path().join("user.py"), "12345").unwrap();
    assert!(capture().is_err());
    fs::write(dir.path().join("user.py"), "1234").unwrap();
    fs::write(dir.path().join("generated/app"), "123456789").unwrap();
    assert!(capture().is_err());
    fs::remove_file(dir.path().join("generated/app")).unwrap();
    symlink("../user.py", dir.path().join("generated/app")).unwrap();
    assert!(capture().is_err());
    for allowances in [
        std::collections::BTreeMap::from([("../escape".into(), 8)]),
        std::collections::BTreeMap::from([("generated/app".into(), 0)]),
        std::collections::BTreeMap::from([("generated/app".into(), 13)]),
    ] {
        assert!(
            InputSnapshot::take_with_path_limits(dir.path(), &[], &[], limits, &allowances)
                .is_err()
        );
    }
}

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
fn declared_generated_effects_never_hide_changed_or_new_user_inputs() {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir_all(dir.path().join("generated")).unwrap();
    fs::write(dir.path().join("user.py"), "original").unwrap();
    fs::write(dir.path().join("generated/old.cpp"), "old generated").unwrap();
    let before = InputSnapshot::take(
        dir.path(),
        &["user.py".into(), "generated/old.cpp".into()],
        &[],
        InputLimits::default(),
    )
    .unwrap();
    fs::write(dir.path().join("user.py"), "changed").unwrap();
    fs::remove_file(dir.path().join("generated/old.cpp")).unwrap();
    fs::write(dir.path().join("generated/new.cpp"), "new generated").unwrap();
    fs::write(dir.path().join("new-user.py"), "new user").unwrap();
    let after = InputSnapshot::take(
        dir.path(),
        &[
            "user.py".into(),
            "generated/new.cpp".into(),
            "new-user.py".into(),
        ],
        &[],
        InputLimits::default(),
    )
    .unwrap();
    let (merged, drift) = before
        .after_preparation(&after, &["generated".into()], InputLimits::default())
        .unwrap();
    assert_eq!(drift.changed, ["user.py"]);
    assert_eq!(drift.added, ["new-user.py"]);
    assert!(drift.removed.is_empty());
    assert_eq!(merged.files["user.py"].input.bytes, b"original");
    assert_eq!(
        merged.files["generated/new.cpp"].input.bytes,
        b"new generated"
    );
    assert!(!merged.files.contains_key("new-user.py"));
    assert!(!merged.files.contains_key("generated/old.cpp"));
    assert!(
        before
            .after_preparation(&after, &[".".into()], InputLimits::default())
            .is_err()
    );
    assert!(
        before
            .after_preparation(
                &after,
                &["generated".into()],
                InputLimits {
                    file_bytes: 128,
                    total_bytes: 8
                }
            )
            .is_err()
    );
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
#[test]
fn explicit_regular_configuration_link_is_captured_and_target_changes_are_visible() {
    let root = tempfile::tempdir().unwrap();
    let policy = tempfile::NamedTempFile::new().unwrap();
    fs::write(policy.path(), "schema_version=2\n").unwrap();
    symlink(policy.path(), root.path().join("ici.toml")).unwrap();
    let before = InputSnapshot::take(
        root.path(),
        &[],
        &["ici.toml".into()],
        InputLimits::default(),
    )
    .unwrap();
    fs::write(policy.path(), "schema_version=2\nprofile='fast'\n").unwrap();
    let after = InputSnapshot::take(
        root.path(),
        &[],
        &["ici.toml".into()],
        InputLimits::default(),
    )
    .unwrap();
    assert_eq!(before.difference(&after).changed, ["ici.toml"]);
    assert!(before.sources.is_empty());
}
