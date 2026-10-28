use ici_providers::resolver::*;
use std::{fs, os::unix::fs::PermissionsExt, path::Path};
fn executable(path: &Path, body: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, body).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
}
fn request(root: &Path) -> ToolRequest {
    ToolRequest {
        id: "ruff".into(),
        explicit: None,
        project_root: root.into(),
        pack_root: None,
        path_env: None,
    }
}
#[test]
fn explicit_broken_tool_never_falls_back() {
    let dir = tempfile::tempdir().unwrap();
    let fallback = dir.path().join(".venv/bin/ruff");
    executable(&fallback, "available");
    let mut req = request(dir.path());
    req.explicit = Some("missing".into());
    assert!(
        req.resolve()
            .unwrap_err()
            .reason
            .contains("selected executable")
    );
}
#[test]
fn project_tool_wins_and_same_path_replacement_changes_identity() {
    for id in ["ruff", "mutmut", "cosmic-ray", "mutpy"] {
        let dir = tempfile::tempdir().unwrap();
        let bin = dir.path().join(".venv/bin").join(id);
        executable(&bin, "first");
        let fallback = dir.path().join("global").join(id);
        executable(&fallback, "global tool");
        let mut req = request(dir.path());
        req.id = id.into();
        req.path_env = Some(fallback.parent().unwrap().to_string_lossy().into_owned());
        let first = req.resolve().unwrap();
        assert_eq!(first.source, ToolSource::Project);
        assert!(first.version.is_none());
        executable(&bin, "second");
        assert_ne!(first.digest, req.resolve().unwrap().digest);
    }
}
#[test]
fn pack_manifest_is_verified_and_corruption_is_loud() {
    let dir = tempfile::tempdir().unwrap();
    let pack = dir.path().join("pack");
    let bin = pack.join("bin/ruff");
    executable(&bin, "native fixture");
    let digest = executable_digest(&bin).unwrap();
    let document = serde_json::json!({"schema_id":"ici.native-tools","schema_version":1,"tools":{"ruff":{"path":"bin/ruff","sha256":digest,"version":"0.16.4","license":"MIT","target":"x86_64-unknown-linux-musl"}}});
    fs::write(pack.join("manifest.json"), document.to_string()).unwrap();
    let mut req = request(dir.path());
    req.pack_root = Some(pack);
    let resolved = req.resolve().unwrap();
    assert_eq!(resolved.source, ToolSource::Pack);
    assert_eq!(resolved.version.as_deref(), Some("0.16.4"));
    executable(&bin, "changed");
    assert!(req.resolve().unwrap_err().reason.contains("checksum"));
}
#[test]
fn missing_tool_is_not_a_clean_check() {
    let dir = tempfile::tempdir().unwrap();
    assert!(request(dir.path()).resolve().is_err());
}
#[test]
fn native_type_configuration_is_respected() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("pyproject.toml");
    assert_eq!(type_provider(dir.path(), None).unwrap(), TypeProvider::Ty);
    fs::write(&path, "[tool.\"mypy\"]\nstrict=true\n").unwrap();
    assert_eq!(type_provider(dir.path(), None).unwrap(), TypeProvider::Mypy);
    fs::write(&path, "[tool.mypy]\n[tool.ty]\n").unwrap();
    assert!(type_provider(dir.path(), None).is_err());
    assert_eq!(
        type_provider(dir.path(), Some("mypy")).unwrap(),
        TypeProvider::Mypy
    );
    fs::write(&path, "[invalid").unwrap();
    assert!(type_provider(dir.path(), None).is_err());
}
#[test]
fn symlink_invocation_name_is_preserved_for_multicall_dispatch() {
    use ici_domain::TaskSpec;
    use ici_execution::{Cancellation, Runner};
    let dir = tempfile::tempdir().unwrap();
    let alias = dir.path().join("chosen-shell-name");
    std::os::unix::fs::symlink("/bin/sh", &alias).unwrap();
    let mut req = request(dir.path());
    req.explicit = Some(alias.clone());
    let tool = req.resolve().unwrap();
    assert_eq!(tool.path, alias);
    let mut task = TaskSpec::command(
        "alias",
        "fixture",
        vec![
            tool.path.to_string_lossy().into_owned(),
            "-c".into(),
            "printf '%s' \"$0\"".into(),
        ],
        dir.path().to_string_lossy(),
    );
    task.tool_digest = Some(tool.digest);
    let outcome = Runner::default().run(&task, &Cancellation::default());
    assert!(outcome.completed(), "{outcome:?}");
    assert_eq!(
        String::from_utf8(outcome.stdout).unwrap(),
        alias.to_string_lossy()
    );
}
