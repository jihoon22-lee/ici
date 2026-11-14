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
    stderr: String,
}

fn run(case: &Value, environment: &BTreeMap<String, String>) -> Value {
    run_with(case, environment, false)
}

/// Directories a run owns that are not the project's outcome (frozen keeps `build/`, the native
/// shadow lives under `.ici/rust-v1/build`: the approved shadow location difference).
const PRIVATE: [&str; 3] = [".ici", "__pycache__", ".git"];

/// Every project path below `root` (directories with a trailing `/`), as the capture lists them.
fn listing(root: &Path) -> std::collections::BTreeSet<String> {
    let mut names = std::collections::BTreeSet::new();
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

/// `run`, and with `detailed` also the engine `extra` and the project paths the run created.
fn run_with(case: &Value, environment: &BTreeMap<String, String>, detailed: bool) -> Value {
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
        if relative.starts_with('<') {
            continue;
        }
        let path = project.as_path().join(relative);
        if let Some(target) = text.get("symlink").and_then(Value::as_str) {
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::os::unix::fs::symlink(target, path).unwrap();
            continue;
        }
        if let Some(hex) = text.get("hex").and_then(Value::as_str) {
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            let bytes = (0..hex.len())
                .step_by(2)
                .map(|index| u8::from_str_radix(&hex[index..index + 2], 16).unwrap())
                .collect::<Vec<_>>();
            fs::write(path, bytes).unwrap();
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
    let modes = case["changes"]["<mode>"]
        .as_object()
        .cloned()
        .unwrap_or_default();
    for (relative, mode) in &modes {
        use std::os::unix::fs::PermissionsExt;
        let mode = u32::try_from(mode.as_u64().unwrap()).unwrap();
        fs::set_permissions(project.join(relative), fs::Permissions::from_mode(mode)).unwrap();
    }
    let before = listing(&project);
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
    let mut environment = environment.clone();
    if let Some(directory) = case["changes"]["<pkg-config-path>"].as_str() {
        environment.insert(
            "PKG_CONFIG_PATH".into(),
            project.join(directory).display().to_string(),
        );
    }
    let process = std::process::Command::new(env!("CARGO_BIN_EXE_ici"))
        .arg("build")
        .current_dir(&project)
        .env_clear()
        .envs(&environment)
        .output()
        .unwrap();
    let output = Output {
        exit_code: process.status.code().unwrap(),
        stdout: String::from_utf8_lossy(&process.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&process.stderr).into_owned(),
    };
    let created = listing(&project)
        .difference(&before)
        .cloned()
        .collect::<Vec<_>>();
    // Each mode-changed source as the run left it: its permission bits (read before they are
    // restored for cleanup) and whether its bytes are still the case's.
    let mut sources = serde_json::Map::new();
    for relative in modes.keys() {
        use std::os::unix::fs::PermissionsExt;
        let path = project.join(relative);
        let mode = fs::symlink_metadata(&path).unwrap().permissions().mode() & 0o7777;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
        let unchanged = case["changes"][relative.as_str()]
            .as_str()
            .is_some_and(|text| fs::read(&path).unwrap() == text.as_bytes());
        sources.insert(
            relative.clone(),
            json!({"mode": mode, "unchanged": unchanged}),
        );
    }
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
    let mut result = json!({
        "exit_code": output.exit_code,
        // The capture stores Python `stdout.strip()`.
        "stdout": output.stdout.trim(),
        "status": engine["status"],
        "summary": engine["summary"],
        "targets": targets,
    });
    if detailed {
        let work_path = work.path().display().to_string();
        result["created"] = json!(created);
        result["evidence"] = engine["evidence"].clone();
        result["extra"] = json!(
            [
                "project_name",
                "project_version",
                "target_path",
                "metrics_summary"
            ]
            .into_iter()
            .map(|key| (
                key.to_owned(),
                engine["extra"][key]
                    .as_str()
                    .map(|text| json!(text.replace(&work_path, "<work>")))
                    .unwrap_or(Value::Null)
            ))
            .collect::<serde_json::Map<_, _>>()
        );
        result["stdout"] = json!(output.stdout.trim().replace(&work_path, "<work>"));
        result["stderr"] = json!(output.stderr.trim().replace(&work_path, "<work>"));
        result["sources"] = Value::Object(sources);
        // The engine's frozen-shaped tool records (one per process it ran) and its manifests.
        result["tools"] = engine["tools"].clone();
        result["artifact_manifests"] = engine["extra"]["artifact_manifests"].clone();
        // The saved native run: its limitations and every check's task state.
        let saved: Value =
            serde_json::from_slice(&fs::read(project.join(".ici/rust-v1/result.json")).unwrap())
                .unwrap();
        result["native_limitations"] = json!(
            saved["limitations"]
                .as_array()
                .unwrap()
                .iter()
                .map(|item| item.as_str().unwrap().replace(&work_path, "<work>"))
                .collect::<Vec<_>>()
        );
        result["check_states"] = json!(
            saved["checks"]
                .as_array()
                .unwrap()
                .iter()
                .map(|check| check["state"].clone())
                .collect::<Vec<_>>()
        );
        result["execution"] = saved["execution"].clone();
        result["shadow_exists"] = json!(project.join(".ici/rust-v1/build").exists());
        result["summary"] = json!(
            engine["summary"]
                .as_str()
                .unwrap_or_default()
                .replace(&work_path, "<work>")
        );
    }
    result
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
    // No locale is set: the frozen interpreter coerced the C locale to LC_CTYPE=C.UTF-8 (PEP 538)
    // for its build children and `ici build` does the same (root ruling 2026-11-14 (b6(b))), so
    // legacy qmake6 never prints its non-UTF-8 locale warning into the excerpt.
    let environment = BTreeMap::from([("PATH".to_string(), path)]);
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 23);
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
                // Frozen order (root ruling R-1): the binary and loaders are compiled and
                // recorded first, then the declaration's BuildError. Every target equals frozen
                // but the BuildError message, which carries the real cause above.
                let (actual, frozen) = (
                    actual["targets"].as_array().unwrap(),
                    frozen["targets"].as_array().unwrap(),
                );
                assert_eq!(actual.len(), frozen.len(), "{name}: {actual:?}");
                let (last, head) = actual.split_last().unwrap();
                let (frozen_last, frozen_head) = frozen.split_last().unwrap();
                assert_eq!(head, frozen_head, "{name}");
                for key in ["target_name", "status", "start_line", "file_path"] {
                    assert_eq!(last[key], frozen_last[key], "{name} {key}");
                }
                assert_eq!(
                    last["message"],
                    "Declared artifacts could not be recorded: declared artifact glob matched nothing: report: reports/*.txt",
                    "{name}"
                );
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
            // CMake/qmake and direct g++ failures quote the first 200 characters of stderr. That
            // excerpt is cut at a host-path-length-dependent point and its build-tree-relative
            // paths differ (qmake), so beyond the frozen shape both excerpts must be prefixes of
            // one text. `legacy_build_failure_ladder.rs` compares the direct text whole, at a
            // fixed path length.
            "cmake-compile-failure"
            | "cmake-configure-failure"
            | "qmake-compile-failure"
            | "direct-compile-failure"
            | "direct-pkg-config-missing" => {
                for key in ["exit_code", "status"] {
                    assert_eq!(actual[key], frozen[key], "{name} {key}");
                }
                if name.starts_with("direct") {
                    assert_eq!(actual["stdout"], frozen["stdout"], "{name} stdout");
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

/// Rich wraps the frozen console line at spaces; the work directory's length differs between the
/// capture and this run, so the console text is compared with whitespace runs collapsed.
fn console(text: &Value) -> String {
    text.as_str()
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

/// The BuildEngine error literals of oracle engines/build.py that build-targets-oracle.json does
/// not reach (`tests/rust-parity/cpp/capture_build_literals.py`): unreadable C++ sources (:646),
/// the output tree guards (:87, :274), the loader write refusals (:107, :686), a refused name or
/// version before any target path (:142), the descriptor selection edges (dot-names, symlinks, a
/// directory named like a descriptor, a passed-over .pro: :628, :745) and the Python packaging
/// errors (:338, :373), also under a `git describe` version (the launcher and loader refusals
/// name the resolved tree), each with frozen's exit code, console line, status, summary, targets in
/// order, evidence (an ERROR is NOT_RUN in both), project metadata extras and the files the run
/// creates; frozen's `build/` adapter shadow is the approved native location.
#[test]
#[ignore = "explicit actual Make/g++/CMake/qmake legacy build literals (ICI_TEST_MAKE, ICI_TEST_CXX, ICI_TEST_CMAKE; qmake from /usr/bin)"]
fn legacy_build_literals_match_the_frozen_rendered_engine_result() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/build-literals-oracle.json"
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
    let environment = BTreeMap::from([
        ("PATH".to_string(), path),
        ("LC_ALL".to_string(), "C.UTF-8".to_string()),
    ]);
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 35);
    for (name, case) in cases {
        let frozen = &case["frozen"];
        // Frozen's configuration loader refuses an empty `build.python.entrypoint` (exit 2,
        // no engine); the native configuration refusal is compared on the config rows.
        if frozen["engine"].is_null() {
            assert_eq!(frozen["exit_code"], 2, "{name}");
            continue;
        }
        let actual = run_with(case, &environment, true);
        let engine = &frozen["engine"];
        if name == "inspect-unreadable" {
            // Parent ruling 2026-11-14 (decisions.md '읽을 수 없는 production source', extended
            // to legacy `ici build`): a production source the build cannot read for a real
            // permission denial refuses the run at the input snapshot: incomplete, exit 3, before
            // anything starts. Frozen instead creates v2.3.4/x86_64/{bin,lib}, then reports a
            // `BuildError` target and the error summary (ERROR, exit 1); the frozen golden is
            // kept as captured. Native SKIP/NOT_RUN here means only that refusal: never success.
            assert_eq!(engine["status"], "ERROR", "{name}");
            assert_eq!(engine["targets"][0]["target_name"], "BuildError", "{name}");
            assert_eq!(frozen["exit_code"], 1, "{name}");
            assert_eq!(
                frozen["created"],
                json!([
                    "v2.3.4/",
                    "v2.3.4/x86_64/",
                    "v2.3.4/x86_64/bin/",
                    "v2.3.4/x86_64/lib/"
                ]),
                "{name}"
            );
            assert_eq!(
                (
                    &actual["exit_code"],
                    &actual["status"],
                    &actual["evidence"],
                    &actual["summary"],
                    &actual["targets"],
                    &actual["created"],
                ),
                (
                    &json!(3),
                    &json!("SKIP"),
                    &json!("NOT_RUN"),
                    &json!("input snapshot failed"),
                    &json!([]),
                    &json!([]),
                ),
                "{name}: {actual}"
            );
            // The refusal names the source and the real permission-denied cause, in the user
            // diagnostic or the saved native limitations: a bare SKIP is outside the ruling.
            let named = |text: &str| {
                text.contains("src/locked.cpp")
                    && (text.contains("Permission denied") || text.contains("permission denied"))
            };
            assert!(
                [&actual["stdout"], &actual["stderr"]]
                    .into_iter()
                    .filter_map(Value::as_str)
                    .any(named)
                    || actual["native_limitations"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .filter_map(Value::as_str)
                        .any(named),
                "{name}: {actual}"
            );
            // The source is untouched: its bytes and its 0o000 mode.
            assert_eq!(
                actual["sources"],
                json!({"src/locked.cpp": {"mode": 0, "unchanged": true}}),
                "{name}"
            );
            // No process ran: the engine has no tool record, the saved run failed no task, and
            // every check stayed blocked behind the snapshot.
            assert!(
                actual["tools"].as_array().is_none_or(Vec::is_empty),
                "{name}: {actual}"
            );
            assert_eq!(
                actual["execution"]["required_complete"],
                json!(false),
                "{name}"
            );
            assert_eq!(actual["execution"]["failed_task_ids"], json!([]), "{name}");
            let states = actual["check_states"].as_array().unwrap();
            assert!(
                !states.is_empty() && states.iter().all(|state| state == "BLOCKED"),
                "{name}: {actual}"
            );
            // No success manifest and no native shadow build exist.
            assert!(
                actual["artifact_manifests"]
                    .as_array()
                    .is_none_or(Vec::is_empty),
                "{name}: {actual}"
            );
            assert_eq!(actual["shadow_exists"], json!(false), "{name}: {actual}");
            continue;
        }
        assert_eq!(actual["exit_code"], frozen["exit_code"], "{name}: {actual}");
        assert_eq!(
            console(&actual["stdout"]),
            console(&frozen["stdout"]),
            "{name}"
        );
        for key in ["status", "evidence", "summary", "targets"] {
            assert_eq!(actual[key], engine[key], "{name} {key}");
        }
        for key in [
            "project_name",
            "project_version",
            "target_path",
            "metrics_summary",
        ] {
            assert_eq!(
                actual["extra"][key], engine["extra"][key],
                "{name} extra {key}"
            );
        }
        let outcome = |paths: &Value| {
            paths
                .as_array()
                .unwrap()
                .iter()
                .filter_map(Value::as_str)
                .filter(|path| !path.starts_with("build/"))
                .map(str::to_owned)
                .collect::<Vec<_>>()
        };
        assert_eq!(
            outcome(&actual["created"]),
            outcome(&frozen["created"]),
            "{name} created"
        );
    }
}
