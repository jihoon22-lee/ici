//! The real cmake and g++ invocation order of legacy `ici dead` with a linker scope, from wrapper
//! marker logs, against frozen (`capture_linker_order.py`): the compiler scope runs first and an
//! analysis error means the shadow is never configured or built. Reports are compared in
//! `legacy_cpp_linker_dead.rs`; these tests read what actually ran.
use serde_json::Value;
use std::{
    fs,
    os::unix::fs::{PermissionsExt, symlink},
    path::Path,
    process::{Command, Stdio},
};

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

/// `capture_linker_order.py::label`, the events both sides are compared on.
fn label(line: &str) -> Option<String> {
    let (tool, args) = line.split_once('\t')?;
    let tokens: Vec<&str> = args.split_whitespace().collect();
    let has = |token: &str| tokens.contains(&token);
    if tool == "cmake" {
        if has("--build") {
            return Some("BUILD".into());
        }
        if has("--version") || has("-E") {
            return None;
        }
        return tokens
            .iter()
            .any(|token| token.starts_with("-S") || token.starts_with("-B"))
            .then(|| "CONFIGURE".to_owned());
    }
    if ["--version", "-dumpversion", "-dumpmachine"]
        .iter()
        .any(|flag| args.contains(flag))
    {
        return None;
    }
    let base = |path: &str| path.rsplit('/').next().unwrap_or(path).to_owned();
    if has("-Wunused-function") {
        let source = tokens.iter().rev().find(|token| token.ends_with(".cpp"))?;
        return Some(format!("UNUSED:{}", base(source)));
    }
    if ["CMakeCXXCompiler", "CMakeTmp", "CMakeScratch", "TryCompile"]
        .iter()
        .any(|mark| args.contains(mark))
    {
        return None;
    }
    if let Some(index) = tokens.iter().position(|token| *token == "-c")
        && let Some(source) = tokens
            .get(index + 1)
            .filter(|token| token.ends_with(".cpp"))
    {
        return Some(format!("compile:{}", base(source)));
    }
    (args.contains(".o") && has("-o")).then(|| "link".to_owned())
}

struct Run {
    exit: Option<i32>,
    events: Vec<String>,
    custom_ran: bool,
    report: Option<Value>,
    _work: tempfile::TempDir,
}

/// One real `ici <command> --report`; `gxx` is extra shell run by the g++ wrapper before it execs.
fn run(family: &str, name: &str, command: &str, gxx: impl Fn(&Path) -> String) -> Run {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity-fixtures/cpp");
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let (project, tools, home) = (base.join("project"), base.join("tools"), base.join("home"));
    copy(&fixtures.join(family).join(name), &project);
    fs::create_dir_all(&tools).unwrap();
    fs::create_dir_all(&home).unwrap();
    let marker = base.join("marker.log");
    for (tool, extra) in [("cmake", String::new()), ("g++", gxx(&project))] {
        let path = tools.join(tool);
        fs::write(
            &path,
            format!(
                "#!/bin/sh\nprintf '%s\\t%s\\n' {tool} \"$*\" >> '{}'\n{extra}exec /usr/bin/{tool} \"$@\"\n",
                marker.display()
            ),
        )
        .unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
    }
    symlink("g++", tools.join("c++")).unwrap();
    let status = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args([command, "--report"])
        .current_dir(&project)
        .env_clear()
        .env("PATH", format!("{}:/usr/bin:/bin", tools.display()))
        .env("HOME", &home)
        .env("LANG", "C.UTF-8")
        .env("LC_ALL", "C.UTF-8")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .unwrap();
    let events = fs::read_to_string(&marker)
        .unwrap_or_default()
        .lines()
        .filter_map(label)
        .collect();
    let report = fs::read_to_string(project.join(format!("{command}_report.json")))
        .ok()
        .map(|text| serde_json::from_str(&text).unwrap());
    Run {
        exit: status.code(),
        events,
        custom_ran: base.join("custom-ran").exists(),
        report,
        _work: work,
    }
}

fn replay_failure(name: &str) -> String {
    if name.contains("replay-fails") {
        "case \" $* \" in *\" -Wunused-function \"*) exit 1;; esac\n".to_owned()
    } else {
        String::new()
    }
}

/// Runs extra shell in the g++ wrapper only for the last unused-function replay of this fixture.
fn on_last_replay(action: &str) -> String {
    format!(
        "case \" $* \" in *\" -Wunused-function \"*) case \" $* \" in *util.cpp*) {action};; esac;; esac\n"
    )
}

/// The compiles inside one `cmake --build` run in the build tool's parallel order.
fn with_build_compiles_sorted(mut events: Vec<String>) -> Vec<String> {
    let mut start = 0;
    while start < events.len() {
        let length = events[start..]
            .iter()
            .take_while(|event| event.starts_with("compile:"))
            .count();
        events[start..start + length].sort();
        start += length.max(1);
    }
    events
}

fn built(events: &[String]) -> bool {
    events.iter().any(|event| event == "BUILD")
}

#[test]
#[ignore = "needs /usr/bin/cmake and /usr/bin/g++"]
fn the_compiler_scope_and_shadow_run_in_frozen_order_with_the_same_custom_commands() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/linker-order-oracle.json"
    ))
    .unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 9);
    let mut differences = vec![];
    for (name, frozen) in cases {
        let family = if name.starts_with("custom-command") {
            "linker-order"
        } else {
            "linker-dead"
        };
        let actual = run(family, name, "dead", |_| replay_failure(name));
        let wanted: Vec<String> = serde_json::from_value(frozen["events"].clone()).unwrap();
        if with_build_compiles_sorted(actual.events.clone()) != with_build_compiles_sorted(wanted)
            || actual.custom_ran != frozen["custom_ran"]
            || actual.exit.map(i64::from) != frozen["exit_code"].as_i64()
        {
            differences.push(format!(
                "{name}:\n native {:?} custom={} exit={:?}\n frozen {frozen}",
                actual.events, actual.custom_ran, actual.exit
            ));
        }
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    // Guards against a vacuous comparison: the control really builds, the error path never.
    assert_eq!(cases["custom-command-control"]["custom_ran"], true);
    assert_eq!(cases["custom-command-broken"]["custom_ran"], false);
}

/// A cancel arriving during the compiler scope starts no shadow build and keeps exit 130.
#[test]
#[ignore = "needs /usr/bin/cmake and /usr/bin/g++"]
fn a_cancel_during_the_compiler_scope_starts_no_shadow_build() {
    let actual = run("linker-dead", "unused-and-linker", "dead", |_| {
        on_last_replay("kill -INT $PPID")
    });
    assert_eq!(actual.exit, Some(130), "{:?}", actual.events);
    assert!(!built(&actual.events), "{:?}", actual.events);
}

/// A source changing during the compiler scope starts no shadow build and keeps exit 3.
#[test]
#[ignore = "needs /usr/bin/cmake and /usr/bin/g++"]
fn a_source_changing_during_the_compiler_scope_starts_no_shadow_build() {
    let actual = run("linker-dead", "unused-and-linker", "dead", |project| {
        on_last_replay(&format!(
            "echo '// drift' >> '{}'",
            project.join("src/main.cpp").display()
        ))
    });
    assert_eq!(actual.exit, Some(3), "{:?}", actual.events);
    assert!(!built(&actual.events), "{:?}", actual.events);
    let reasons = &actual.report.expect("report")["native_run"]["gate"]["reasons"];
    assert!(
        reasons
            .as_array()
            .unwrap()
            .iter()
            .any(|reason| reason.as_str().unwrap().contains("inputs changed")),
        "{reasons}"
    );
}

/// `ici verify` keeps building its shadow during preparation: a compiler error there must still
/// report the CMake processes that really ran.
#[test]
#[ignore = "needs /usr/bin/cmake and /usr/bin/g++"]
fn verify_reports_the_shadow_build_it_ran_before_a_compiler_error() {
    let actual = run("linker-order", "verify-replay-fails", "verify", |_| {
        replay_failure("replay-fails")
    });
    assert!(built(&actual.events), "{:?}", actual.events);
    let report = actual.report.expect("report");
    let dead = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "dead")
        .unwrap();
    let names: Vec<&str> = dead["tool_evidence"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|record| record["name"].as_str())
        .collect();
    for name in [
        "cmake linker-reachability configure",
        "cmake linker-reachability build",
    ] {
        assert!(names.contains(&name), "{name}: {names:?}");
    }
}
