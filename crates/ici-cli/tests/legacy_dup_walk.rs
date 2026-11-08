//! Approved difference (decisions.md "legacy dup 탐색 범위"): the legacy `ici dup` source walk
//! applies its ignored-directory names (`v0.*`/`v1.*` release directories among them) and the
//! internal-state directories (`.ici`, `.ici-native`) only to paths inside the workspace, never
//! to the directories above the project. Frozen `_should_ignore_path` read every part of the
//! absolute path, so a checkout below a `v0.*`-named directory scanned nothing; native gives the
//! same result wherever the project sits.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const BODY: &str = "int shared(int value) {\n    int total = 0;\n    for (int i = 0; i < value; ++i) {\n        total += i * 2;\n        total -= i / 3;\n        total ^= i;\n    }\n    return total + value;\n}\n";

fn write(root: &Path, files: &[(&str, String)]) {
    for (path, text) in files {
        let path = root.join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }
}

/// The legacy dup engine of one run, without its duration.
fn dup(parent: &str) -> Value {
    let work = tempfile::tempdir().unwrap();
    let root = work
        .path()
        .canonicalize()
        .unwrap()
        .join(parent)
        .join("project");
    write(
        &root,
        &[
            (
                "ici.toml",
                "[project]\ntype='cpp'\nsource_dirs=['.']\n".into(),
            ),
            ("src/a.cpp", BODY.into()),
            ("src/b.cpp", BODY.replace("shared", "copied")),
            // Internal state at the workspace root and a release directory inside the
            // declared input (the whole workspace): neither is scanned.
            (".ici/rust-v1/d.cpp", BODY.into()),
            (".ici-native/e.cpp", BODY.into()),
            ("src/v0.9/f.cpp", BODY.into()),
        ],
    );
    let home = work.path().join("home");
    fs::create_dir(&home).unwrap();
    let environment = BTreeMap::from([
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            work.path().display().to_string(),
        ),
    ]);
    let invocation = registry::parse(["ici", "dup", "--report"]).unwrap();
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
    let mut report: Value =
        serde_json::from_str(&fs::read_to_string(root.join("dup_report.json")).unwrap()).unwrap();
    scrub(&mut report);
    let text = serde_json::to_string(&json!({"exit_code": output.exit_code, "report": report}))
        .unwrap()
        .replace(&root.display().to_string(), "<root>");
    serde_json::from_str(&text).unwrap()
}

/// Wall-clock values (durations, times and the time-stamped run id) differ between any two runs.
fn scrub(value: &mut Value) {
    match value {
        Value::Object(object) => {
            object.retain(|key, _| {
                !(key.contains("duration")
                    || key.contains("timestamp")
                    || key.ends_with("_at")
                    || key == "run_id")
            });
            object.values_mut().for_each(scrub);
        }
        Value::Array(items) => items.iter_mut().for_each(scrub),
        _ => {}
    }
}

#[test]
fn legacy_dup_scans_the_same_sources_under_a_release_named_parent() {
    let plain = dup("plain");
    let release = dup("v0.9.1");
    assert_eq!(plain, release);
    // The scan found the clone pair and nothing under the internal-state or release directories.
    let text = plain.to_string();
    assert!(
        text.contains("src/a.cpp") && text.contains("src/b.cpp"),
        "{text}"
    );
    for skipped in [".ici/rust-v1/d.cpp", ".ici-native/e.cpp", "src/v0.9/f.cpp"] {
        assert!(!text.contains(skipped), "{skipped} scanned: {text}");
    }
}
