//! Frozen legacy `ici verify` on baseline paths, through the real CLI against
//! `tests/rust-parity/python/capture_baseline_paths.py`. Native refused every `--write-baseline`
//! naming its `--baseline`, which is how a baseline is updated in place; it now refuses only what
//! frozen refuses, with frozen's words, plus what would destroy a project input (decisions.md
//! "legacy baseline 경로 오류").
use serde_json::Value;
use std::{collections::BTreeMap, fs, process::Command};

const ORACLE: &str = include_str!("../../../tests/rust-parity/python/baseline-paths-oracle.json");
/// The OS or JSON parser detail after these words is each product's own.
const DETAIL_AFTER: [(&str, &str); 3] = [
    (
        "baseline-bad-json",
        "could not parse baseline <root>/b.json: ",
    ),
    (
        "write-is-directory",
        "could not write baseline <root>/bdir: ",
    ),
    (
        "write-parent-is-file",
        "could not write baseline <root>/afile/b.json: ",
    ),
];
/// Frozen overwrites these project files. Native refuses, keeping the original.
const INPUTS_KEPT: [&str; 3] = [
    "write-over-config",
    "write-over-source",
    "write-over-foreign-file",
];

fn outcome(oracle: &Value, case: &Value) -> (i64, Vec<String>, Vec<String>) {
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap();
    fs::write(base.join(".hg"), "").unwrap();
    let root = base.join("project");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(root.join("src/a.py"), oracle["clean"].as_str().unwrap()).unwrap();
    fs::write(root.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
    let run = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(["verify", "--no-cache"])
            .args(args)
            .current_dir(&root)
            .env_clear()
            .env("HOME", &base)
            .env("PATH", "/usr/bin:/bin")
            .env("GIT_CEILING_DIRECTORIES", &base)
            .output()
            .unwrap()
    };
    if let Some(seed) = case["seed"].as_str() {
        run(&["--write-baseline", "b.json"]);
        if seed == "regressed" {
            fs::write(root.join("src/a.py"), oracle["regressed"].as_str().unwrap()).unwrap();
        }
    }
    for name in case["directories"].as_array().into_iter().flatten() {
        fs::create_dir(root.join(name.as_str().unwrap())).unwrap();
    }
    for (name, text) in case["files"].as_object().into_iter().flatten() {
        fs::write(root.join(name), text.as_str().unwrap()).unwrap();
    }
    let watched = [
        "b.json",
        "c.json",
        "ici.toml",
        "src/a.py",
        "new/sub/b.json",
        "verify_report.json",
        "notes.txt",
    ];
    let before: BTreeMap<_, _> = watched
        .iter()
        .filter_map(|name| fs::read(root.join(name)).ok().map(|bytes| (*name, bytes)))
        .collect();
    let args: Vec<_> = case["args"]
        .as_array()
        .unwrap()
        .iter()
        .map(|value| value.as_str().unwrap())
        .collect();
    let done = run(&args);
    let written = watched
        .iter()
        .filter(|name| {
            fs::read(root.join(name))
                .is_ok_and(|bytes| before.get(*name).is_none_or(|old| *old != bytes))
        })
        .map(ToString::to_string)
        .collect();
    let stderr = String::from_utf8_lossy(&done.stderr);
    let error = stderr
        .lines()
        .filter(|line| line.starts_with("Baseline error: "))
        .map(|line| line.replace(&root.to_string_lossy().into_owned(), "<root>"))
        .collect();
    (i64::from(done.status.code().unwrap()), written, error)
}

fn strings(value: &Value) -> Vec<String> {
    serde_json::from_value(value.clone()).unwrap()
}

#[test]
fn baseline_paths_are_accepted_and_refused_like_frozen() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let (code, written, error) = outcome(&oracle, case);
        let name = name.as_str();
        if INPUTS_KEPT.contains(&name) {
            assert_eq!(
                strings(&case["written"]).len(),
                1,
                "{name}: frozen overwrites"
            );
            assert_eq!((code, written), (2, vec![]), "{name}");
            continue;
        }
        // A completed run with a required engine left NOT_RUN: decisions.md "legacy 종료 코드 3의
        // 범위". A refusal is 2 in both.
        let frozen_exit = case["exit"].as_i64().unwrap();
        let expected = if frozen_exit == 1 { 3 } else { frozen_exit };
        assert_eq!(code, expected, "{name}: {error:?}");
        assert_eq!(written, strings(&case["written"]), "{name}");
        let frozen_error = strings(&case["error"]);
        if let Some((_, words)) = DETAIL_AFTER.iter().find(|(case, _)| *case == name) {
            assert_eq!(error.len(), 1, "{name}: {error:?}");
            assert!(frozen_error[0].contains(words), "{name}: {frozen_error:?}");
            assert!(error[0].contains(words), "{name}: {error:?}");
        } else {
            assert_eq!(error, frozen_error, "{name}");
        }
    }
}
