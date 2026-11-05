//! Legacy lint C++ syntax global time budget (frozen `_cpp_lint._GLOBAL_TIMEOUT_SECONDS`,
//! `_run_exact` and `_run_fallback`) against the frozen CLI with that constant replaced
//! (`tests/rust-parity/cpp/capture_lint_budget.py`): a budget spent before the first unit and one
//! spent while a fake `g++` sleeps on `slow.cpp`. The native 600 s budget is replaced through the
//! process-local test hook; nothing on the command line reaches it.
use ici_cli::{commands, cpp::set_legacy_syntax_budget_for_tests, registry};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};

#[path = "support/host_tools.rs"]
mod host_tools;

#[path = "support/lint_extra.rs"]
mod lint_extra;

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "start_column",
    "file_path",
];

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

fn run(oracle: &Value, case: &Value, system_path: &str) -> Value {
    use std::os::unix::fs::PermissionsExt;
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    let bin = work.path().join("bin");
    fs::create_dir_all(&bin).unwrap();
    fs::write(bin.join("g++"), oracle["fake"].as_str().unwrap()).unwrap();
    fs::set_permissions(bin.join("g++"), fs::Permissions::from_mode(0o755)).unwrap();
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity-fixtures/cpp/lint-branches"),
        &root,
    );
    fs::write(root.join("ici.toml"), oracle["base"].as_str().unwrap()).unwrap();
    for (name, text) in case["files"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(
            path,
            text.as_str()
                .unwrap()
                .replace("{root}", &root.display().to_string()),
        )
        .unwrap();
    }
    let generated = case["generated_sources"].as_u64().unwrap_or(0);
    if generated > 0 {
        fs::create_dir_all(root.join("src/gen")).unwrap();
    }
    for index in 0..generated {
        fs::write(
            root.join(format!("src/gen/s{index}.cpp")),
            format!("int s{index}();\n"),
        )
        .unwrap();
    }
    let command = case["command"].as_str().unwrap();
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    // The work directory bounds VCS discovery (the project sits directly inside it).
    let environment = BTreeMap::from([
        (
            "PATH".to_string(),
            format!("{}:{system_path}", bin.display()),
        ),
        (
            "GIT_CEILING_DIRECTORIES".to_string(),
            work.path().display().to_string(),
        ),
    ]);
    set_legacy_syntax_budget_for_tests(Some(Duration::from_secs_f64(
        case["budget_seconds"].as_f64().unwrap(),
    )));
    let output = commands::dispatch(&invocation, &root, &environment);
    set_legacy_syntax_budget_for_tests(None);
    let output = output.unwrap();
    let report: Value =
        serde_json::from_slice(&fs::read(root.join(format!("{command}_report.json"))).unwrap())
            .unwrap();
    let engine = if command == "verify" {
        report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == "lint")
            .unwrap()
            .clone()
    } else {
        report.clone()
    };
    let targets: Vec<Value> = engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            Value::Object(
                FIELDS
                    .iter()
                    .map(|key| ((*key).to_owned(), target[*key].clone()))
                    .collect(),
            )
        })
        .collect();
    let mut captured = json!({
        "status": engine["status"],
        "summary": engine["summary"],
        "evidence": engine["evidence"],
        "targets": targets,
        "findings": engine["findings"],
        "extra": engine["extra"],
        "tool_evidence": engine["tool_evidence"],
    });
    if command != "verify" {
        captured["exit_code"] = json!(output.exit_code);
    }
    let text = serde_json::to_string(&captured)
        .unwrap()
        .replace(&root.display().to_string(), "{root}")
        .replace(&bin.display().to_string(), "{bin}");
    serde_json::from_str(&text).unwrap()
}

/// Positive: a budget spent while `slow.cpp` runs times that unit out at the remaining budget
/// (not the 120 s unit limit) and stops before `warn1.cpp` ("left 1 unit(s) unexamined").
/// Failure: a budget spent before the first unit replays nothing ("left 8 unit(s)").
/// Failure: 2049 selected sources stop before any replay ('C++ source count exceeds the bounded
/// limit' at the project root).
/// Negative: the 600 s budget is not reached by the other lint tests in this crate, whose
/// frozen outcomes carry no budget target (`legacy_lint_branches.rs`, `legacy_lint.rs`).
#[test]
#[ignore = "explicit actual g++ behind a fake g++ driver: /usr/bin/g++ must be the oracle g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0 and /usr/bin must lack clang-tidy/clazy-standalone; the mid-run cases wait out a 20 s budget"]
fn legacy_cpp_lint_global_time_budget_matches_frozen_targets_summaries_and_evidence() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/lint-budget-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let version = std::process::Command::new("/usr/bin/g++")
        .arg("--version")
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8(version.stdout).unwrap().lines().next(),
        oracle["gxx"].as_str(),
        "the budget oracle was captured with another g++"
    );
    replay(&oracle, "/usr/bin:/bin", false);
}

/// The CI twin with the host's own g++ (any release, as on the GitHub runner) behind the same
/// fake driver and a mirror of `/usr/bin` and `/bin` without clang*/clazy* (the capture host had
/// none; `support/host_tools.rs`). Incidental masks (`host_view`), applied to both sides: only the
/// g++ version banner (`tool_evidence[].version` and the g++ findings' `tool_version`). Every
/// budget message, target, summary and record stays compared. The mid-run cases wait out the
/// 20 s budget here too.
#[test]
fn legacy_cpp_lint_global_time_budget_matches_frozen_targets_summaries_and_evidence_with_the_host_gxx()
 {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/lint-budget-oracle.json"
    ))
    .unwrap();
    let host = host_tools::mirror(&host_tools::NO_LLVM);
    replay(&oracle, &host.path, true);
}

/// The budget hook is process-wide, so the explicit test and its twin never run at once.
static BUDGET_HOOK: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn replay(oracle: &Value, system_path: &str, host: bool) {
    let _hook = BUDGET_HOOK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 5);
    // `dispatch` installs ici's SIGINT/SIGTERM handler, so a signal from outside the test (an
    // outer `timeout`, a tool-call limit killing the process group, Ctrl-C) does not end this
    // process: the case in flight finishes as "run cancelled" and would otherwise read as a budget
    // mismatch. Record such a signal and fail with that cause instead; a second signal takes
    // the default action and ends the process.
    let signalled = Arc::new(AtomicBool::new(false));
    for signal in [signal_hook::consts::SIGINT, signal_hook::consts::SIGTERM] {
        signal_hook::flag::register_conditional_default(signal, Arc::clone(&signalled)).unwrap();
        signal_hook::flag::register(signal, Arc::clone(&signalled)).unwrap();
    }
    let mut mismatches = vec![];
    for (name, case) in cases {
        let started = Instant::now();
        let mut actual = run(oracle, case, system_path);
        eprintln!("{name}: {:.1} s", started.elapsed().as_secs_f64());
        assert!(
            !signalled.load(Ordering::SeqCst),
            "the test process received SIGINT/SIGTERM from outside while running {name}; that \
             run was cancelled, so it is not a budget comparison"
        );
        let mut frozen = case["frozen"].clone();
        if host {
            host_view(&mut actual);
            host_view(&mut frozen);
        }
        let differences = lint_extra::split(&mut actual, &mut frozen);
        if !differences.is_empty() {
            eprintln!(
                "== {name} (extra/tool_evidence)\n{}",
                differences.join("\n")
            );
            mismatches.push(format!("{name}/extra"));
        }
        if actual != frozen {
            eprintln!(
                "== {name}\nactual: {}\nfrozen: {}",
                serde_json::to_string(&actual).unwrap(),
                serde_json::to_string(&frozen).unwrap()
            );
            mismatches.push(name.clone());
        }
    }
    for name in ["fallback-budget-mid", "exact-budget-mid"] {
        let targets = cases[name]["frozen"]["targets"].as_array().unwrap();
        assert!(targets.iter().any(|target| target["message"]
            == "C++ syntax global time budget left 1 unit(s) unexamined"
            && target["file_path"] == "src/warn1.cpp"));
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}

/// Mask only the g++ release banner (see the host twin's doc).
fn host_view(view: &mut Value) {
    for record in view["tool_evidence"].as_array_mut().into_iter().flatten() {
        if record["name"] == "g++" && record["version"] != "" {
            record["version"] = json!("<host>");
        }
    }
    for finding in view["findings"].as_array_mut().into_iter().flatten() {
        if finding["tool_name"] == "g++" && finding["tool_version"] != "" {
            finding["tool_version"] = json!("<host>");
        }
    }
}
