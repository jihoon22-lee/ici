//! A next baseline after an `ici.toml` edit, through the real CLI's saved results, against frozen
//! (`tests/rust-parity/python/capture_policy_identity.py`). Native used to take the file's bytes
//! into three Python scopes, so a comment left a fixed test `carried` where frozen has `resolved`.
//! The scope now takes the resolved settings: comment and layout edits match frozen, and any edit
//! that does resolve differently stays unresolved (decisions.md "ESLint rule off/config/plugin/
//! lockfile 변경", "next baseline의 결과 identity").
use serde_json::Value;
use std::{collections::BTreeMap, fs, os::unix::fs::symlink, path::Path, process::Command};

const ORACLE: &str = include_str!("../../../tests/rust-parity/python/policy-identity-oracle.json");
/// Same project, same settings: nothing of the comparison may move.
const SAME_SETTINGS: [&str; 6] = [
    "same",
    "comment",
    "layout",
    "moved",
    "child-same",
    "child-comment",
];
/// Frozen resolves these while its scope ignores the edit; native refuses to call the old finding
/// fixed under different settings, `carried` being its unconfirmed outcome.
const NOT_RESOLVED_UNLIKE_FROZEN: [&str; 3] = ["profile", "selection", "dotted-path"];
/// Same in both products: the scope or the policy digest differs.
const SAME_AS_FROZEN: [&str; 5] = [
    "test-paths",
    "child-test-paths",
    "check-required",
    "check-disabled",
    "check-no-effect",
];

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON").expect("set explicit ICI_TEST_PYTHON with pytest/coverage")
}

fn verify(root: &Path, base: &Path, args: &[&str]) -> i32 {
    Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["next", "verify"])
        .args(args)
        .current_dir(root)
        .env_clear()
        .env("HOME", base)
        .env("PATH", "/usr/bin:/bin")
        .env("GIT_CEILING_DIRECTORIES", base)
        .status()
        .unwrap()
        .code()
        .unwrap()
}

fn saved(path: &Path) -> Value {
    serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
}

fn scopes(result: &Value) -> BTreeMap<String, String> {
    result["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|check| {
            (
                check["check_id"].as_str().unwrap().to_string(),
                check["scope_digest"].as_str().unwrap().to_string(),
            )
        })
        .collect()
}

fn outcome(oracle: &Value, name: &str) -> (i32, String, Vec<String>, usize) {
    let case = &oracle["cases"][name];
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap();
    fs::write(base.join(".hg"), "").unwrap();
    let python = python();
    let interpreter = Path::new(&python);
    let parent = interpreter.parent().unwrap();
    let link = base.join("elsewhere/python");
    fs::create_dir_all(link.parent().unwrap()).unwrap();
    symlink(interpreter, &link).unwrap();
    let dotted = parent.join("..").join(parent.file_name().unwrap());
    let fill = |text: &str| {
        text.replace("{link}", &link.to_string_lossy())
            .replace("{dotted_dir}", &dotted.to_string_lossy())
            .replace("{python}", &python)
    };
    let mut root = base.join("project");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::create_dir_all(root.join("tests")).unwrap();
    let text = |key: &str| oracle[key].as_str().unwrap().to_string();
    fs::write(root.join("src/app.py"), text("source")).unwrap();
    fs::write(root.join("tests/test_app.py"), text("failing")).unwrap();
    fs::write(root.join("pyproject.toml"), text("pyproject")).unwrap();
    let write = |root: &Path, files: &Value| {
        for (path, content) in files.as_object().unwrap() {
            fs::write(root.join(path), fill(content.as_str().unwrap())).unwrap();
        }
    };
    write(&root, &case["before"]);
    verify(&root, &base, &["--result", "first.json"]);
    fs::write(root.join("tests/test_app.py"), text("passing")).unwrap();
    if name == "moved" {
        let moved = base.join("moved");
        fs::rename(&root, &moved).unwrap();
        root = moved;
    }
    write(&root, &case["after"]);
    let code = verify(
        &root,
        &base,
        &["--result", "now.json", "--baseline", "first.json"],
    );
    let first = saved(&root.join("first.json"));
    let now = saved(&root.join("now.json"));
    let failed: Vec<_> = first["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|finding| finding["rule_id"] == "pytest.failed")
        .map(|finding| finding["fingerprint"].clone())
        .collect();
    assert_eq!(failed.len(), 1, "{name}");
    let baseline = &now["baseline"];
    let fixed = ["carried", "new", "resolved", "unchanged"]
        .into_iter()
        .filter(|key| {
            let listed = baseline[*key].as_array().unwrap();
            failed
                .iter()
                .any(|fingerprint| listed.contains(fingerprint))
        })
        .map(String::from)
        .collect();
    let before = scopes(&first);
    let moved = scopes(&now)
        .iter()
        .filter(|(check, digest)| before.get(*check) != Some(*digest))
        .count();
    (
        code,
        baseline["state"].as_str().unwrap().to_string(),
        fixed,
        moved,
    )
}

fn frozen(oracle: &Value, name: &str) -> (i32, String, Vec<String>) {
    let case = &oracle["cases"][name];
    (
        i32::try_from(case["exit"].as_i64().unwrap()).unwrap(),
        case["state"].as_str().unwrap().to_string(),
        serde_json::from_value(case["fixed_test_is"].clone()).unwrap(),
    )
}

#[test]
#[ignore = "explicit project Python runtime+pytest+coverage acceptance"]
fn only_an_edit_that_resolves_differently_keeps_a_fixed_test_from_resolving() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let carried = vec!["carried".to_string()];
    for name in SAME_SETTINGS {
        let (code, state, fixed, moved) = outcome(&oracle, name);
        assert_eq!((code, state, fixed), frozen(&oracle, name), "{name}");
        assert_eq!(moved, 0, "{name}: no check's scope may move");
    }
    for name in SAME_AS_FROZEN {
        let (code, state, fixed, moved) = outcome(&oracle, name);
        assert_eq!((code, state, fixed), frozen(&oracle, name), "{name}");
        assert!(moved > 0, "{name}: the comparison scope must move");
    }
    for name in NOT_RESOLVED_UNLIKE_FROZEN {
        let (code, state, fixed, moved) = outcome(&oracle, name);
        let (frozen_code, frozen_state, frozen_fixed) = frozen(&oracle, name);
        assert_eq!(frozen_fixed, vec!["resolved".to_string()], "{name}");
        assert_eq!(
            (code, state, &fixed),
            (frozen_code, frozen_state, &carried),
            "{name}"
        );
        assert!(moved > 0, "{name}");
    }
    // Frozen hashes the tool path into its toolchain digest and stops comparing. Native sees the
    // same interpreter through the link: still comparable, with the moved scope unconfirmed.
    let (code, state, fixed, moved) = outcome(&oracle, "tool-path");
    assert_eq!(frozen(&oracle, "tool-path").1, "incompatible");
    assert_eq!((code, state.as_str(), &fixed), (3, "comparable", &carried));
    assert!(moved > 0);
}

#[test]
#[ignore = "explicit project Python runtime+pytest+coverage acceptance"]
fn a_comment_written_during_the_run_is_still_a_changed_input() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap();
    fs::write(base.join(".hg"), "").unwrap();
    let root = base.join("project");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::create_dir_all(root.join("tests")).unwrap();
    let text = |key: &str| oracle[key].as_str().unwrap().to_string();
    fs::write(root.join("src/app.py"), text("source")).unwrap();
    fs::write(root.join("tests/test_app.py"), text("failing")).unwrap();
    fs::write(root.join("pyproject.toml"), text("pyproject")).unwrap();
    let config = oracle["cases"]["same"]["before"]["ici.toml"]
        .as_str()
        .unwrap()
        .replace("{python}", &python());
    fs::write(root.join("ici.toml"), &config).unwrap();
    verify(&root, &base, &["--result", "first.json"]);
    fs::write(root.join("tests/test_app.py"), text("passing")).unwrap();
    fs::write(
        root.join("tests/conftest.py"),
        "from pathlib import Path\n\n\ndef pytest_sessionstart(session):\n    \
         path = Path(session.config.rootpath, 'ici.toml')\n    \
         path.write_text(path.read_text() + '# written during the run\\n')\n",
    )
    .unwrap();
    verify(
        &root,
        &base,
        &["--result", "now.json", "--baseline", "first.json"],
    );
    assert_ne!(fs::read_to_string(root.join("ici.toml")).unwrap(), config);
    let now = saved(&root.join("now.json"));
    let test = now["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["check_id"] == "python.test")
        .unwrap();
    assert_eq!(test["evidence_complete"], false, "{test}");
    let first = saved(&root.join("first.json"));
    let failed = first["findings"]
        .as_array()
        .unwrap()
        .iter()
        .find(|finding| finding["rule_id"] == "pytest.failed")
        .unwrap();
    let resolved = now["baseline"]["resolved"].as_array().unwrap();
    assert!(!resolved.contains(&failed["fingerprint"]), "{resolved:?}");
}

#[test]
fn a_comment_in_the_settings_file_moves_no_cpp_scope() {
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap();
    fs::write(base.join(".hg"), "").unwrap();
    let root = base.join("project");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(root.join("src/a.cpp"), "int a() { return 0; }\n").unwrap();
    let database = serde_json::json!([{
        "directory": root,
        "arguments": ["/usr/bin/g++", "-std=c++17", "-c", "src/a.cpp"],
        "file": "src/a.cpp",
    }]);
    fs::write(root.join("compile_commands.json"), database.to_string()).unwrap();
    let config = "schema_version=1\n[workspace]\nname='p'\n[[components]]\nid='app'\nroot='.'\n\
                  languages=['cpp']\nsources=['src/**/*.cpp']\n";
    fs::write(root.join("ici.toml"), config).unwrap();
    verify(&root, &base, &["--result", "first.json"]);
    fs::write(root.join("ici.toml"), format!("{config}# a note\n")).unwrap();
    verify(&root, &base, &["--result", "now.json"]);
    let first = scopes(&saved(&root.join("first.json")));
    assert!(first.len() > 5, "{first:?}");
    assert_eq!(first, scopes(&saved(&root.join("now.json"))));
}
