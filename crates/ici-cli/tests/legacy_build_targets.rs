//! Legacy `ici build` BuildEngine targets against the frozen CLI's own rendered engine result
//! (`tests/rust-parity/cpp/capture_build_targets.py`).
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

/// Compile durations are wall-clock values on both sides.
fn normalized(message: &str) -> String {
    let Some(start) = message.find("Compiled C++17 binary (") else {
        return message.to_owned();
    };
    let tail = &message[start + "Compiled C++17 binary (".len()..];
    let seconds = tail.strip_suffix("s)").expect("frozen duration suffix");
    let (whole, fraction) = seconds.split_once('.').expect("two decimals");
    assert!(
        whole.chars().all(|c| c.is_ascii_digit())
            && fraction.len() == 2
            && fraction.chars().all(|c| c.is_ascii_digit()),
        "{message}"
    );
    format!("{}Compiled C++17 binary (<seconds>s)", &message[..start])
}

struct Output {
    exit_code: i32,
    stdout: String,
}

fn run(case: &Value, environment: &BTreeMap<String, String>) -> Value {
    let work = tempfile::tempdir().unwrap();
    let project = work.path().join("project");
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp")
            .join(case["fixture"].as_str().unwrap()),
        project.as_path(),
    );
    fs::write(work.path().join("outside.txt"), "outside\n").unwrap();
    for (relative, text) in case["changes"].as_object().unwrap() {
        if relative == "<git-tag>" {
            continue;
        }
        let path = project.as_path().join(relative);
        if let Some(target) = text.get("symlink").and_then(Value::as_str) {
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::os::unix::fs::symlink(target, path).unwrap();
            continue;
        }
        match text.as_str() {
            None => fs::remove_file(path).unwrap(),
            Some(text) => {
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                fs::write(path, text).unwrap();
            }
        }
    }
    // A tagged commit: the release version comes from the visible `git describe` probe.
    if let Some(tag) = case["changes"]["<git-tag>"].as_str() {
        let git = |args: &[&str]| {
            let status = std::process::Command::new("git")
                .args([
                    "-c",
                    "user.email=ici@example.invalid",
                    "-c",
                    "user.name=ici",
                    "-C",
                ])
                .arg(&project)
                .args(args)
                .env_clear()
                .env("PATH", "/usr/bin:/bin")
                .status()
                .unwrap();
            assert!(status.success(), "git {args:?}");
        };
        git(&["init", "-q"]);
        git(&["add", "-A"]);
        git(&["commit", "-qm", "fixture"]);
        git(&["tag", tag]);
    }
    // The real executable: the qmake adapter re-enters `ici` as its compiler capture shim.
    let process = std::process::Command::new(env!("CARGO_BIN_EXE_ici"))
        .arg("build")
        .current_dir(&project)
        .env_clear()
        .envs(environment)
        .output()
        .unwrap();
    let output = Output {
        exit_code: process.status.code().unwrap(),
        stdout: String::from_utf8_lossy(&process.stdout).into_owned(),
    };
    let runs = project.as_path().join(".ici/rust-v1/details/result.json");
    let run = fs::read_dir(&runs)
        .unwrap_or_else(|_| panic!("no details: {}", output.stdout))
        .next()
        .unwrap()
        .unwrap()
        .path();
    let details: Value =
        serde_json::from_slice(&fs::read(run.join("details.json")).unwrap()).unwrap();
    let engine = details["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "build")
        .unwrap_or_else(|| panic!("no build engine: {}", output.stdout))
        .clone();
    let targets: Vec<Value> = engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            json!({
                "target_name": target["target_name"],
                "status": target["status"],
                "message": normalized(target["message"].as_str().unwrap())
                    .replace(&work.path().display().to_string(), "<work>"),
                "start_line": target["start_line"],
                "file_path": target["file_path"],
            })
        })
        .collect();
    json!({
        "exit_code": output.exit_code,
        // The capture stores Python `stdout.strip()`.
        "stdout": output.stdout.trim(),
        "status": engine["status"],
        "summary": engine["summary"],
        "targets": targets,
    })
}

#[test]
#[ignore = "explicit actual Make/g++/CMake/qmake legacy build targets (ICI_TEST_MAKE, ICI_TEST_CXX, ICI_TEST_CMAKE; qmake from /usr/bin)"]
fn legacy_build_engine_targets_match_the_frozen_rendered_engine_result() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/build-targets-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let tools = ["ICI_TEST_MAKE", "ICI_TEST_CXX", "ICI_TEST_CMAKE"]
        .map(|name| std::env::var(name).unwrap_or_else(|_| panic!("explicit {name}")));
    let path = tools
        .iter()
        .map(|tool| Path::new(tool).parent().unwrap().display().to_string())
        .chain(["/usr/bin".into(), "/bin".into()])
        .collect::<Vec<_>>()
        .join(":");
    // The frozen interpreter coerces the C locale to LC_CTYPE=C.UTF-8 (PEP 538) for its build
    // children, so legacy qmake6 never prints its non-UTF-8 locale warning into the excerpt.
    let environment = BTreeMap::from([
        ("PATH".to_string(), path),
        ("LC_CTYPE".to_string(), "C.UTF-8".to_string()),
    ]);
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 20);
    for (name, case) in cases {
        let frozen = &case["frozen"];
        let actual = run(case, &environment);
        match name.as_str() {
            // Intended difference (root decision for build.py:230): frozen `ici build` has no
            // analysis context, so every [build.artifacts] declaration is a blanket BuildError.
            // Rust records a kept declaration and still refuses a broken one with ERROR exit 1.
            "declared-artifacts" => {
                assert_eq!(frozen["status"], "ERROR");
                assert_eq!(actual["exit_code"], 0);
                assert_eq!(
                    actual["targets"].as_array().unwrap().last().unwrap()["target_name"],
                    "DeclaredArtifacts"
                );
                continue;
            }
            "declared-artifacts-missing" | "declared-artifacts-external" => {
                for key in ["exit_code", "status"] {
                    assert_eq!(actual[key], frozen[key], "{name} {key}");
                }
                assert_eq!(
                    actual["summary"],
                    "Declared artifacts could not be recorded: declared artifact glob matched nothing: report: reports/*.txt",
                    "{name}"
                );
                assert_eq!(actual["targets"][0]["target_name"], "BuildError", "{name}");
                continue;
            }
            // Intended difference (root decision for build.py:590, AGENTS §5.1): Rust keeps the
            // located per-source compiler diagnostic; frozen reports one CppBinary FAIL with the
            // first 200 characters of stderr (absolute host paths). Exit, status and summary
            // stay frozen's.
            "direct-compile-failure" => {
                for key in ["exit_code", "status", "summary", "stdout"] {
                    assert_eq!(actual[key], frozen[key], "{name} {key}");
                }
                assert_eq!(frozen["targets"][0]["target_name"], "CppBinary");
                assert_eq!(actual["targets"][0]["file_path"], "src/main.cpp");
                continue;
            }
            // `ici build` renders the legacy view only on the console; the saved native details
            // keep the blocked native check. Exit and console line are compared here, the shaped
            // engine in `sourceless_legacy_python_build_is_the_frozen_build_artifacts_fail`.
            "no-artifacts" => {
                for key in ["exit_code", "stdout"] {
                    assert_eq!(actual[key], frozen[key], "{name} {key}");
                }
                continue;
            }
            // CMake/qmake failures quote the first 200 characters of stderr. That excerpt is cut
            // at a host-path-length-dependent point and its build-tree-relative paths differ
            // (qmake), so beyond the frozen shape both excerpts must be prefixes of one text.
            "cmake-compile-failure" | "cmake-configure-failure" | "qmake-compile-failure" => {
                for key in ["exit_code", "status"] {
                    assert_eq!(actual[key], frozen[key], "{name} {key}");
                }
                if frozen["status"] != "ERROR" {
                    assert_eq!(actual["summary"], frozen["summary"], "{name}");
                }
                let (actual, frozen) = (&actual["targets"], &frozen["targets"]);
                assert_eq!(actual.as_array().unwrap().len(), 1, "{name}");
                for key in ["target_name", "status", "start_line", "file_path"] {
                    assert_eq!(actual[0][key], frozen[0][key], "{name} {key}");
                }
                let excerpt = |message: &Value| {
                    let message = message.as_str().unwrap();
                    let (head, stderr) = message.split_once(" failed: ").unwrap();
                    let stderr = stderr.replace(['\u{2018}', '\u{2019}'], "'");
                    (head.to_owned(), stderr)
                };
                let (actual_head, actual_text) = excerpt(&actual[0]["message"]);
                let (frozen_head, frozen_text) = excerpt(&frozen[0]["message"]);
                assert_eq!(actual_head, frozen_head, "{name}");
                if name.ends_with("compile-failure") {
                    assert!(actual_text.contains("error: "), "{name}: {actual_text}");
                }
                if name != "qmake-compile-failure" {
                    assert!(
                        actual_text.starts_with(&frozen_text)
                            || frozen_text.starts_with(&actual_text),
                        "{name}: {actual_text:?} / {frozen_text:?}"
                    );
                }
                continue;
            }
            _ => {}
        }
        // The frozen console (one rich-wrapped engine line) is compared byte for byte too.
        assert_eq!(&actual, frozen, "{name}");
    }
}

/// Frozen `BuildEngine.run` for a legacy `type='python'` project that selects no Python source:
/// no artifact is created, which is the measured `BuildArtifacts` FAIL. `ici build` exits 1 with
/// the frozen console line; deep `ici verify --report` records the frozen engine (status, summary,
/// evidence, required, target and legacy finding identity), and `pass_warn` keeps it a WARN.
/// It needs no compiler, so it runs in the default suite.
#[test]
fn sourceless_legacy_python_build_is_the_frozen_build_artifacts_fail() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/build-targets-oracle.json"
    ))
    .unwrap();
    let case = &oracle["cases"]["no-artifacts"];
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    let actual = run(case, &environment);
    for key in ["exit_code", "stdout"] {
        assert_eq!(actual[key], case["frozen"][key], "{key}");
    }
    let verify: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/validation/build-sourceless-oracle.json"
    ))
    .unwrap();
    for (mode, frozen) in verify["runs"].as_object().unwrap() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("src")).unwrap();
        fs::write(
            root.path().join("ici.toml"),
            frozen["config"].as_str().unwrap(),
        )
        .unwrap();
        fs::write(
            root.path().join("pyproject.toml"),
            "[project]\nname='pkg'\nversion='2.3.4'\n",
        )
        .unwrap();
        let process = std::process::Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(["verify", "--report", "--no-cache"])
            .current_dir(root.path())
            .env_clear()
            .envs(&environment)
            .output()
            .unwrap();
        assert_eq!(
            i64::from(process.status.code().unwrap()),
            frozen["exit_code"].as_i64().unwrap(),
            "{mode}"
        );
        let report: Value =
            serde_json::from_slice(&fs::read(root.path().join("verify_report.json")).unwrap())
                .unwrap();
        assert_eq!(report["suite_status"], frozen["suite_status"], "{mode}");
        let engine = report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == "build")
            .unwrap();
        for key in ["status", "summary", "evidence", "required", "targets"] {
            assert_eq!(engine[key], frozen["build"][key], "{mode} {key}");
        }
        let identity = |findings: &Value| {
            findings
                .as_array()
                .unwrap()
                .iter()
                .map(|finding| {
                    json!([
                        finding["rule_id"],
                        finding["fingerprint"],
                        finding["severity"],
                        finding["confidence"],
                        finding["message"],
                        finding["primary_location"]
                    ])
                })
                .collect::<Vec<_>>()
        };
        assert_eq!(
            identity(&engine["findings"]),
            identity(&frozen["build"]["findings"]),
            "{mode}"
        );
    }
}
