use ici_cli::{commands, registry};
use std::collections::BTreeMap;
fn call(args: &[&str]) -> registry::Invocation {
    registry::parse(args.iter().copied()).unwrap()
}
#[test]
fn explicit_setup_surface_does_not_replace_the_frozen_command_union() {
    assert_eq!(registry::surface().commands.len(), 37);
    let parsed = call(&[
        "ici",
        "setup",
        "--archive",
        "candidate.tar.gz",
        "--sha256",
        &"0".repeat(64),
        "--prefix",
        "local",
        "--json",
    ]);
    assert_eq!(parsed.command(), "setup");
    assert!(parsed.flag("json_mode"));
    registry::command().debug_assert();
}
#[test]
fn setup_rejects_ambiguous_actions_missing_or_invalid_digest_before_writing() {
    let directory = tempfile::tempdir().unwrap();
    for arguments in [
        vec![
            "ici",
            "setup",
            "--archive",
            "candidate.tar.gz",
            "--rollback",
            "--prefix",
            "local",
        ],
        vec![
            "ici",
            "setup",
            "--archive",
            "candidate.tar.gz",
            "--prefix",
            "local",
        ],
        vec![
            "ici",
            "setup",
            "--archive",
            "candidate.tar.gz",
            "--sha256",
            "invalid",
            "--prefix",
            "local",
        ],
        vec![
            "ici",
            "setup",
            "--activate",
            "v1.0",
            "--sha256",
            "invalid",
            "--prefix",
            "local",
        ],
    ] {
        assert!(commands::dispatch(&call(&arguments), directory.path(), &BTreeMap::new()).is_err());
        assert!(!directory.path().join("local").exists());
    }
}
#[test]
fn explicit_download_refuses_plain_http_without_creating_an_installation() {
    let directory = tempfile::tempdir().unwrap();
    let arguments = call(&[
        "ici",
        "setup",
        "--url",
        "http://127.0.0.1:9/must-not-contact",
        "--sha256",
        &"0".repeat(64),
        "--prefix",
        "local",
    ]);
    let result = commands::dispatch(&arguments, directory.path(), &BTreeMap::new());
    assert!(result.is_err());
    assert!(!directory.path().join("local").exists());
}
