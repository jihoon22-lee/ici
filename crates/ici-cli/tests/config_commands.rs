use ici_cli::{commands::dispatch, registry::parse};
use std::{collections::BTreeMap, fs, os::unix::fs::symlink};

#[test]
fn init_preview_is_read_only_and_written_defaults_keep_discovered_scope() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"), "x=1\n").unwrap();
    let before =
        ici_config::resolve_with_environment(root.path(), None, None, &BTreeMap::new()).unwrap();
    let preview = dispatch(
        &parse(["ici", "init", "--preview"]).unwrap(),
        root.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert!(preview.stdout.contains("schema_version = 2"));
    assert!(!root.path().join("ici.toml").exists());
    dispatch(
        &parse(["ici", "init"]).unwrap(),
        root.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    let after =
        ici_config::resolve_with_environment(root.path(), None, None, &BTreeMap::new()).unwrap();
    assert_eq!(before.components[0].files, after.components[0].files);
    assert!(
        dispatch(
            &parse(["ici", "init"]).unwrap(),
            root.path(),
            &BTreeMap::new()
        )
        .is_err()
    );
    let old = fs::read(root.path().join("ici.toml")).unwrap();
    dispatch(
        &parse(["ici", "init", "--force"]).unwrap(),
        root.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(
        fs::read(root.path().join("ici.toml.before-init")).unwrap(),
        old
    );
    assert!(
        dispatch(
            &parse(["ici", "init", "--force"]).unwrap(),
            root.path(),
            &BTreeMap::new()
        )
        .is_err()
    );
    assert_eq!(
        fs::read(root.path().join("ici.toml.before-init")).unwrap(),
        old
    );
}
#[test]
fn migration_preview_and_new_file_preserve_original_and_explicit_write_keeps_backup() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("src")).unwrap();
    fs::write(root.path().join("src/main.py"), "x=1\n").unwrap();
    let old = "# retain comment\n[project]\nsource_dirs = [\"src\"]\n";
    fs::write(root.path().join("ici.toml"), old).unwrap();
    let preview = dispatch(
        &parse(["ici", "next", "migrate"]).unwrap(),
        root.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert!(preview.stdout.contains("# retain comment"));
    assert_eq!(
        fs::read_to_string(root.path().join("ici.toml")).unwrap(),
        old
    );
    dispatch(
        &parse(["ici", "next", "migrate", "--output", "converted.toml"]).unwrap(),
        root.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert!(root.path().join("converted.toml").is_file());
    assert!(
        dispatch(
            &parse(["ici", "next", "migrate", "--output", "converted.toml"]).unwrap(),
            root.path(),
            &BTreeMap::new()
        )
        .is_err()
    );
    dispatch(
        &parse(["ici", "next", "migrate", "--write"]).unwrap(),
        root.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(
        fs::read_to_string(root.path().join("ici.toml.stable")).unwrap(),
        old
    );
    assert!(
        fs::read_to_string(root.path().join("ici.toml"))
            .unwrap()
            .contains("schema_version = 2")
    );
}
#[test]
fn cache_clear_refuses_symlink_ancestors_and_preserves_legacy_or_user_files() {
    let root = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    fs::create_dir(outside.path().join("native")).unwrap();
    symlink(outside.path(), root.path().join(".ici")).unwrap();
    assert!(
        dispatch(
            &parse(["ici", "cache", "--clear"]).unwrap(),
            root.path(),
            &BTreeMap::new()
        )
        .is_err()
    );
    assert!(outside.path().join("native").exists());
}
#[test]
fn native_binary_can_init_with_python_and_tools_absent_from_path() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"), "x=1\n").unwrap();
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_ici"))
        .current_dir(root.path())
        .env_clear()
        .env("PATH", "")
        .args(["init", "--preview"])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        String::from_utf8(output.stdout)
            .unwrap()
            .contains("schema_version = 2")
    );
    assert!(!root.path().join("ici.toml").exists());
}
#[test]
fn native_default_artifacts_do_not_replace_previous_python_results() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join(".ici/next")).unwrap();
    let old = root.path().join(".ici/next/result.json");
    fs::write(&old, "old user result, preserved").unwrap();
    let request = parse(["ici", "analyze"]).unwrap();
    assert_eq!(request.value("result"), Some(".ici/rust-v1/result.json"));
    let request = parse(["ici", "next", "report"]).unwrap();
    assert_eq!(request.value("result"), Some(".ici/rust-v1/result.json"));
    assert_eq!(request.value("page"), Some(".ici/rust-v1/result.html"));
    assert_eq!(
        fs::read_to_string(old).unwrap(),
        "old user result, preserved"
    );
}

#[test]
fn cache_inventory_explains_corruption_and_clear_preserves_unknown_files() {
    let root = tempfile::tempdir().unwrap();
    let cache = root.path().join(".ici/rust-v1/observations");
    fs::create_dir_all(&cache).unwrap();
    let corrupt = cache.join(format!("{}.json", "a".repeat(64)));
    fs::write(&corrupt, "{truncated").unwrap();
    fs::write(cache.join("user-note.json"), "user data").unwrap();
    let before = dispatch(
        &parse(["ici", "cache"]).unwrap(),
        root.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert!(before.stdout.contains("0 valid, 1 corrupt"));
    assert!(
        before
            .stdout
            .contains("Invalidated by captured input/configuration bytes")
    );
    assert!(corrupt.exists());
    let cleared = dispatch(
        &parse(["ici", "cache", "--clear"]).unwrap(),
        root.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert!(cleared.stdout.contains("removed 1"));
    assert!(!corrupt.exists());
    assert_eq!(
        fs::read_to_string(cache.join("user-note.json")).unwrap(),
        "user data"
    );
}
