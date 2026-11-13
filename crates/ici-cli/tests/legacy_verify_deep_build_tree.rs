//! Legacy `ici verify --profile deep` with a broken release tree against the frozen v0.13.0 runs
//! in `tests/rust-parity/cpp/verify-deep-build-tree-oracle.json`
//! (`capture_verify_deep_build_tree.py`): frozen `BuildEngine.run` raises from
//! `_prepare_output_tree` under verify exactly as under `ici build` (root ruling 2026-11-13), so
//! the build engine is one `BuildError` at "." (ERROR NOT_RUN), nothing is compiled into the
//! tree, and the not-required build leaves a WARN suite with exit 0.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};

const PRIVATE: [&str; 3] = [".ici", "__pycache__", ".git"];

fn copy(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

fn listing(root: &Path) -> BTreeSet<String> {
    let mut names = BTreeSet::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory).unwrap() {
            let entry = entry.unwrap();
            let name = entry.file_name().to_string_lossy().into_owned();
            let relative = entry
                .path()
                .strip_prefix(root)
                .unwrap()
                .display()
                .to_string();
            if entry.file_type().unwrap().is_dir() {
                if PRIVATE.contains(&name.as_str()) {
                    continue;
                }
                names.insert(format!("{relative}/"));
                pending.push(entry.path());
            } else {
                names.insert(relative);
            }
        }
    }
    names
}

/// Failure: a version path that is a file and a `lib` symlink each stop the build engine with
/// frozen's `Build metadata or setup error: output path …` before anything is built.
/// Negative: the directories frozen made before the failing component are the only new paths.
#[test]
fn legacy_verify_deep_refuses_a_broken_release_tree_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/verify-deep-build-tree-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 2);
    for (name, case) in cases {
        let work = tempfile::tempdir().unwrap();
        let base = work.path().canonicalize().unwrap();
        let root = base.join("project");
        copy(
            &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity-fixtures/cpp/package-direct"),
            &root,
        );
        fs::write(root.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
        for (path, value) in case["changes"].as_object().unwrap() {
            let path = root.join(path);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            match value.as_str() {
                Some(text) => fs::write(&path, text).unwrap(),
                None => {
                    std::os::unix::fs::symlink(value["symlink"].as_str().unwrap(), &path).unwrap()
                }
            }
        }
        let before = listing(&root);
        let home = base.join("home");
        fs::create_dir(&home).unwrap();
        let environment = BTreeMap::from([
            ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
            ("HOME".to_owned(), home.display().to_string()),
            ("LANG".to_owned(), "C.UTF-8".to_owned()),
            ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        ]);
        let invocation = registry::parse([
            "ici",
            "verify",
            "--profile",
            "deep",
            "--no-cache",
            "--report",
        ])
        .unwrap();
        let output = commands::dispatch(&invocation, &root, &environment).unwrap();
        let created: Vec<String> = listing(&root)
            .difference(&before)
            .filter(|path| path.as_str() != "verify_report.json")
            .cloned()
            .collect();
        let text = fs::read_to_string(root.join("verify_report.json"))
            .unwrap()
            .replace(&root.display().to_string(), "{root}");
        let report: Value = serde_json::from_str(&text).unwrap();
        let engine = report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == "build")
            .unwrap_or_else(|| panic!("{name}: no build engine"));
        let actual = json!({
            "exit_code": output.exit_code,
            "suite_status": report["suite_status"],
            "status": engine["status"],
            "evidence": engine["evidence"],
            "summary": engine["summary"],
            "targets": engine["targets"].as_array().unwrap().iter().map(|target| json!({
                "target_name": target["target_name"],
                "status": target["status"],
                "message": target["message"],
                "start_line": target["start_line"],
                "file_path": target["file_path"],
            })).collect::<Vec<_>>(),
            "created": created,
        });
        assert_eq!(
            actual, case["frozen"],
            "{name}\nnative: {actual:#}\nfrozen: {:#}",
            case["frozen"]
        );
    }
}
