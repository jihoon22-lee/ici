//! Legacy build-session artifact manifests and their discovery bounds against the frozen CLI
//! (`tests/rust-parity/cpp/capture_artifact_manifest_bounds.py`): the qmake release session
//! manifest of the deep BuildEngine (producer `qmake.build`, variant release), and frozen
//! `core/cmake._linked_artifact_paths` bounds (cmake.py:643 entries, cmake.py:667 records) that
//! fail a session like a failed build. Compared per session engine: the verdict, the first
//! target (name, status, message, location) and the manifests; the engines' other targets,
//! summaries and findings are compared by the CMake/qmake verify tests. Written by cpp31 and not
//! run before the batch run.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, process::Command, sync::Mutex};

#[path = "support/host_tools.rs"]
mod host_tools;

/// The entry-bound hook is process-wide.
static HOOK: Mutex<()> = Mutex::new(());

const TARGET_FIELDS: [&str; 4] = ["target_name", "status", "message", "file_path"];
const ENGINES: [&str; 5] = [
    "build",
    "test",
    "sanitize",
    "thread_sanitize",
    "binary_compat",
];

/// Needs ruling (cpp31): frozen's qmake release session fails whenever the analysis capture ran
/// first. The capture configured the shared release shadow (build/ici-qmake-build) with
/// `-recursive` and a temporary compiler wrapper, and the BuildEngine's non-recursive configure
/// leaves `tests/Makefile` naming the deleted wrapper ('qmake build failed: make[1]:
/// /tmp/ici-qmake-capture-<random>/compiler-wrapper: No such file or directory'). Native keeps
/// its release session; the build engine and the release manifest are therefore not compared
/// in this case until root rules.
const QMAKE_STALE_WRAPPER: [&str; 1] = ["qmake-deep-build"];

/// The qmake capture re-enters the ici binary, so qmake cases run the real CLI, which the
/// in-process entry-bound hook cannot reach: `qmake-entry-limit` stays a frozen capture (its
/// literals are asserted) and is not replayed natively (open in the ledger).
const NOT_REPLAYED: [&str; 1] = ["qmake-entry-limit"];

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

fn pick(value: &Value, fields: &[&str]) -> Value {
    json!(
        fields
            .iter()
            .map(|field| (
                (*field).to_owned(),
                value.get(*field).cloned().unwrap_or(Value::Null)
            ))
            .collect::<serde_json::Map<_, _>>()
    )
}

fn digest(value: &Value) -> bool {
    value.as_str().is_some_and(|text| {
        text == "<sha256>"
            || text.strip_prefix("sha256:").is_some_and(|hex| {
                hex.len() == 64 && hex.bytes().all(|byte| byte.is_ascii_hexdigit())
            })
    })
}

/// Run-identity values are checked for their shape and removed. Recorded differences: native
/// shadow trees live under `.ici/rust-v1/build/` (frozen `build/ici-<backend>*`, decisions.md
/// 'native C++ shadow build 위치'), the producer command's job count and directory differ, and
/// artifact bytes are the native build's.
fn manifests(value: &Value, commit: &str) -> Value {
    let Some(items) = value.as_array() else {
        return value.clone();
    };
    json!(
        items
            .iter()
            .map(|manifest| {
                assert_eq!(manifest["source_commit"], commit);
                for key in ["config_digest", "toolchain_digest"] {
                    assert!(digest(&manifest[key]), "{key}");
                }
                let artifacts: Vec<Value> = manifest["artifacts"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|artifact| {
                        assert!(digest(&artifact["sha256"]));
                        assert!(artifact["size"].as_u64().unwrap() > 0);
                        pick(
                            artifact,
                            &["path", "scope", "kind", "mode", "producer", "id", "target"],
                        )
                    })
                    .collect();
                json!({"schema_version": manifest["schema_version"],
                    "project_root": manifest["project_root"], "variant": manifest["variant"],
                    "artifacts": artifacts})
            })
            .collect::<Vec<_>>()
    )
}

fn engine_view(name: &str, engine: &Value, commit: &str) -> Value {
    json!({
        "status": engine["status"],
        // A test session that never ran carries frozen's unrun estimates
        // (decisions.md '실행되지 않은 legacy C++ test의 추정 coverage'); its verdict and
        // first target stay compared.
        "evidence": if name == "test" { Value::Null } else { engine["evidence"].clone() },
        "first_target": engine["targets"].get(0).map(|target| pick(target, &TARGET_FIELDS)),
        "artifact_manifests": manifests(&engine["artifact_manifests"], commit),
    })
}

/// The comparable view of one run (frozen's saved `engines` map or native's report).
fn view(name: &str, exit_code: Value, engines: &Value, context: &Value, commit: &str) -> Value {
    let mut engines: serde_json::Map<String, Value> = engines
        .as_object()
        .unwrap()
        .iter()
        .map(|(key, engine)| (key.clone(), engine_view(key, engine, commit)))
        .collect();
    let mut context = manifests(context, commit);
    if QMAKE_STALE_WRAPPER.contains(&name) {
        engines.remove("build");
        if let Some(items) = context.as_array_mut() {
            items.retain(|manifest| manifest["variant"] != "release");
        }
    }
    json!({"exit_code": exit_code, "engines": engines, "context": context})
}

fn run(oracle: &Value, name: &str, case: &Value, path: &str) -> Value {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    match case["fixture"].as_str() {
        Some(fixture) => copy_tree(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../parity-fixtures/cpp")
                .join(fixture),
            &root,
        ),
        None => {
            for (relative, text) in oracle["generated"].as_object().unwrap() {
                let file = root.join(relative);
                fs::create_dir_all(file.parent().unwrap()).unwrap();
                fs::write(file, text.as_str().unwrap()).unwrap();
            }
        }
    }
    fs::write(
        root.join("ici.toml"),
        format!(
            "{}{}",
            case["base"].as_str().unwrap(),
            case["config"].as_str().unwrap()
        ),
    )
    .unwrap();
    let git = |args: &[&str]| {
        let output = Command::new("git")
            .args([
                "-c",
                "user.email=ici@example.invalid",
                "-c",
                "user.name=ici",
            ])
            .args(args)
            .current_dir(&root)
            .output()
            .unwrap();
        assert!(output.status.success());
        String::from_utf8(output.stdout).unwrap().trim().to_owned()
    };
    git(&["init", "-q", "."]);
    git(&["add", "-A"]);
    git(&["commit", "-qm", "fixture"]);
    let commit = git(&["rev-parse", "HEAD"]);
    let command = case["command"].as_str().unwrap();
    let args: Vec<&str> = case["args"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(Value::as_str)
        .collect();
    let exit_code = if case["fixture"] == "test-qmake" {
        // The qmake capture re-enters the ici binary (`__qmake-configure-capture`,
        // `__qmake-record`), so the real CLI runs.
        assert!(
            case["entry_bound"].is_null(),
            "{name}: the hook cannot reach the real CLI"
        );
        let output = Command::new(env!("CARGO_BIN_EXE_ici"))
            .arg(command)
            .args(&args)
            .arg("--report")
            .current_dir(&root)
            .env_clear()
            .env("PATH", path)
            .env("HOME", work.path())
            .env("LC_CTYPE", "C.UTF-8")
            .output()
            .unwrap();
        output.status.code().unwrap()
    } else {
        let invocation =
            registry::parse(["ici", command].into_iter().chain(args).chain(["--report"])).unwrap();
        let environment = BTreeMap::from([
            ("PATH".to_string(), path.to_string()),
            ("HOME".to_string(), work.path().display().to_string()),
            ("LC_CTYPE".to_string(), "C.UTF-8".to_string()),
        ]);
        let bound = case["entry_bound"].as_u64().map(|bound| bound as usize);
        ici_cli::cpp::set_legacy_artifact_discovery_entry_limit_for_tests(bound);
        let output = commands::dispatch(&invocation, &root, &environment);
        ici_cli::cpp::set_legacy_artifact_discovery_entry_limit_for_tests(None);
        output.unwrap().exit_code
    };
    let text = fs::read_to_string(root.join(format!("{command}_report.json")))
        .unwrap()
        .replace(&root.display().to_string(), "<ROOT>");
    let report: Value = serde_json::from_str(&text).unwrap();
    let engines: serde_json::Map<String, Value> = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|engine| ENGINES.contains(&engine["engine_name"].as_str().unwrap()))
        .map(|engine| {
            (
                engine["engine_name"].as_str().unwrap().to_owned(),
                engine.clone(),
            )
        })
        .collect();
    let frozen = &case["frozen"];
    let native = view(
        name,
        json!(exit_code),
        &json!(engines),
        &report["analysis_context"]["artifact_manifests"],
        &commit,
    );
    let frozen_commit = frozen["context_manifests"]
        .as_array()
        .and_then(|items| items.first())
        .map(|manifest| manifest["source_commit"].as_str().unwrap().to_owned());
    // Frozen manifests name the capture checkout's commit; compare them as this run's.
    let frozen_text = match &frozen_commit {
        Some(old) => serde_json::to_string(frozen).unwrap().replace(old, &commit),
        None => serde_json::to_string(frozen).unwrap(),
    };
    let frozen: Value = serde_json::from_str(&frozen_text).unwrap();
    let expected = view(
        name,
        frozen["exit_code"].clone(),
        &frozen["engines"],
        &frozen["context_manifests"],
        &commit,
    );
    if native != expected {
        eprintln!(
            "== {name}\nfrozen: {}\nnative: {}",
            serde_json::to_string_pretty(&expected).unwrap(),
            serde_json::to_string_pretty(&native).unwrap()
        );
        return json!(name);
    }
    Value::Null
}

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/artifact-manifest-bounds-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

/// Independent literals of the frozen capture: the release session manifest of an uncaptured
/// qmake project, no release session under the standard profile, and the session failures of
/// each bound on every session engine (the build engine's BuildAdapter FAIL, the coverage
/// session's `[C++] build` FAIL, the sanitizer sessions' `C++Sanitizer` ERROR NOT_RUN, all at the
/// root descriptor, no manifest anywhere).
fn assert_frozen_literals(oracle: &Value) {
    let cases = &oracle["cases"];
    let release = &cases["qmake-deep-build-uncaptured"]["frozen"]["engines"]["build"];
    assert_eq!(release["status"], "PASS");
    let artifact = &release["artifact_manifests"][0]["artifacts"][0];
    assert_eq!(release["artifact_manifests"][0]["variant"], "release");
    assert_eq!(artifact["id"], "release:shadow:test_unit");
    assert_eq!(artifact["producer"], "qmake.build");
    assert_eq!(artifact["kind"], "executable");
    assert!(
        cases["qmake-verify-standard"]["frozen"]["engines"]
            .get("build")
            .is_none()
    );
    for (case, reason, descriptor) in [
        (
            "cmake-record-limit",
            "artifact discovery exceeds the 512 record limit",
            "CMakeLists.txt",
        ),
        (
            "cmake-entry-limit",
            "artifact discovery exceeds the 40 entry limit",
            "CMakeLists.txt",
        ),
    ] {
        let message = format!("artifact manifest validation failed: {reason}");
        let engines = &cases[case]["frozen"]["engines"];
        assert_eq!(cases[case]["frozen"]["exit_code"], 1, "{case}");
        assert_eq!(
            cases[case]["frozen"]["context_manifests"],
            json!([]),
            "{case}"
        );
        for (engine, name, status) in [
            ("build", "BuildAdapter", "FAIL"),
            ("test", "[C++] build", "FAIL"),
            ("sanitize", "C++Sanitizer", "ERROR"),
            ("thread_sanitize", "C++Sanitizer", "ERROR"),
        ] {
            let target = &engines[engine]["targets"][0];
            assert_eq!(target["target_name"], name, "{case} {engine}");
            assert_eq!(target["status"], status, "{case} {engine}");
            assert_eq!(target["message"], message.as_str(), "{case} {engine}");
            assert_eq!(target["file_path"], descriptor, "{case} {engine}");
            assert!(
                engines[engine]["artifact_manifests"].is_null(),
                "{case} {engine}"
            );
        }
        assert_eq!(engines["sanitize"]["summary"], message.as_str(), "{case}");
    }
    // The pinned qmake entry bound (3): every qmake session fails its walk, at project.pro.
    let qmake = &cases["qmake-entry-limit"]["frozen"]["engines"];
    for (engine, name, status) in [
        ("test", "[C++] build", "FAIL"),
        ("sanitize", "C++Sanitizer", "ERROR"),
        ("thread_sanitize", "C++Sanitizer", "ERROR"),
    ] {
        let target = &qmake[engine]["targets"][0];
        assert_eq!(target["target_name"], name, "qmake {engine}");
        assert_eq!(target["status"], status, "qmake {engine}");
        assert_eq!(
            target["message"],
            "artifact manifest validation failed: artifact discovery exceeds the 3 entry limit",
            "qmake {engine}"
        );
        assert_eq!(target["file_path"], "project.pro", "qmake {engine}");
    }
    let stale = &cases["qmake-deep-build"]["frozen"]["engines"]["build"]["targets"][0]["message"];
    assert!(
        stale
            .as_str()
            .unwrap()
            .contains("/compiler-wrapper: No such file or directory"),
        "{stale}"
    );
}

/// Positive: the deep BuildEngine's qmake release session publishes its `qmake.build` manifest
/// (qmake-deep-build-uncaptured) beside the coverage/sanitize/thread-sanitize sessions'.
/// Negative: the standard profile has no release session (qmake-verify-standard). Failure: a
/// session past frozen's 512-record bound (520 static libraries, unpatched) or past the entry
/// bound (lowered for the CMake case through the test hook exactly as the capture patched
/// frozen) fails like a failed build on every session engine and publishes nothing. The qmake
/// cases run the real CLI on the host's /usr/bin tools (mirrored without clang/clazy).
#[test]
fn legacy_build_session_manifests_and_discovery_bounds_match_frozen() {
    let _guard = HOOK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let oracle = oracle();
    assert_frozen_literals(&oracle);
    let host = host_tools::mirror(&host_tools::NO_LLVM);
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 6);
    // A missing adapter tool is reported, and is a failure under ICI_REQUIRE_BUILD_ADAPTERS=1.
    let required = std::env::var("ICI_REQUIRE_BUILD_ADAPTERS").as_deref() == Ok("1");
    let available = |tool: &str| host_tools::version(&Path::new("/usr/bin").join(tool)).is_some();
    let mut mismatches = vec![];
    for (name, case) in cases {
        if NOT_REPLAYED.contains(&name.as_str()) {
            continue;
        }
        let tool = if case["fixture"] == "test-qmake" {
            "qmake6"
        } else {
            "cmake"
        };
        if !available(tool) {
            assert!(!required, "{name}: /usr/bin/{tool} is required");
            println!("{name}: no /usr/bin/{tool} on this host; this case did not run");
            continue;
        }
        let mismatch = run(&oracle, name, case, &host.path);
        if !mismatch.is_null() {
            mismatches.push(mismatch);
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
