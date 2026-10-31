use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};

fn installation(directory: &str) -> (tempfile::TempDir, PathBuf) {
    let original = Path::new(env!("CARGO_BIN_EXE_ici"));
    let tree = tempfile::tempdir_in(original.parent().unwrap()).unwrap();
    let installed = tree.path().join(directory).join("ici");
    fs::create_dir_all(installed.parent().unwrap()).unwrap();
    fs::hard_link(original, &installed).unwrap();
    (tree, installed)
}

fn shell_roundtrip(shell: &Path, mode: &str) {
    let (tree, installed) = installation("path ' quote !bang $money `tick` (parent) \\slash");
    let output = Command::new(&installed)
        .args(["env", mode])
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let loader = tree.path().join("loader");
    let mut script = output.stdout;
    script.extend_from_slice(b"printf '%s\\n' \"$PATH\"\n");
    fs::write(&loader, script).unwrap();
    let output = Command::new(shell)
        .arg("-f")
        .arg(&loader)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8(output.stdout).unwrap(),
        format!("{}:/usr/bin:/bin\n", installed.parent().unwrap().display())
    );
    assert_eq!(fs::read_dir(tree.path()).unwrap().count(), 2);
}

#[test]
fn sh_loader_preserves_literal_installation_path_and_existing_path() {
    shell_roundtrip(Path::new("/bin/sh"), "--sh");
}

#[test]
fn unsafe_path_entries_and_ambiguous_shell_selection_are_refused() {
    for directory in ["colon:path", "line\nbreak"] {
        let (_tree, installed) = installation(directory);
        let output = Command::new(installed)
            .args(["env", "--csh"])
            .output()
            .unwrap();
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
        assert!(String::from_utf8_lossy(&output.stderr).contains("cannot be represented safely"));
    }
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["env", "--sh", "--csh"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
}

#[test]
#[ignore = "requires an explicitly supplied actual tcsh executable"]
fn tcsh_loader_preserves_quotes_history_characters_and_existing_path() {
    let shell = std::env::var_os("ICI_TEST_TCSH").expect("set ICI_TEST_TCSH to actual tcsh");
    shell_roundtrip(Path::new(&shell), "--csh");
}
