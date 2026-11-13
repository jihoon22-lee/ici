//! The legacy `verify` preparation's `project.cpp_pkg_config` probe (root ruling 2026-11-14):
//! frozen `get_cpp_pkg_config_flags` bounds (300 s, 1,000,000 characters of output), and no
//! package that adds no flags is a silent omission: every native refusal (an expression starting
//! with `-`, more than 128 packages, more than 4,096 flags) and
//! every other probe failure (no pkg-config, a non-zero exit, a signal, too much output,
//! non-UTF-8 output) is an explicit limitation of the run. A fake pkg-config on `PATH` answers
//! per package and logs every call beside itself (the probe inherits no other variable). The 300 s bound is not exercised (it would take 300 s), and a
//! control character never reaches the probe: the configuration loader refuses it first
//! ("list items must be nonempty, bounded printable strings").
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt};

/// Every engine but `complexity` (a run must check something; it runs no tool).
const ENGINES: [&str; 18] = [
    "line",
    "lint",
    "compile_db",
    "test",
    "type",
    "python_compat",
    "sanitize",
    "thread_sanitize",
    "dead",
    "dup",
    "exception",
    "cognitive",
    "security",
    "resource",
    "build",
    "binary_compat",
    "integration",
    "cycle",
];

/// Prints `-DGOOD -DGOOD=2` for `good`, 1,000,000 characters of short flags for `exact`,
/// one character more for `flood`, 4,097 flags for `many`, non-UTF-8 bytes for `binary`, kills
/// itself for `signal`, and exits 1 for anything else.
const FAKE: &str = r#"#!/bin/sh
printf '%s\n' "$*" >> "$(dirname "$0")/calls.log"
case "$2" in
  good) echo '-DGOOD -DGOOD=2' ;;
  exact) x=$(head -c 247 /dev/zero | tr '\0' x); i=0
    while [ $i -lt 4000 ]; do printf -- '-D%s ' "$x"; i=$((i+1)); done ;;
  flood) head -c 1000001 /dev/zero | tr '\0' 'x' | fold -w 200 ;;
  many) i=0; while [ $i -le 4096 ]; do printf -- '-DF%s ' $i; i=$((i+1)); done ;;
  binary) printf '\377\376\n' ;;
  signal) kill -KILL $$ ;;
  *) exit 1 ;;
esac
"#;

struct Run {
    flags: Vec<String>,
    limitations: Vec<String>,
    calls: Vec<String>,
}

fn run(packages: &[&str], with_tool: bool) -> Run {
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let root = base.join("project");
    let tools = base.join("tools");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::create_dir_all(&tools).unwrap();
    fs::write(root.join("src/a.cpp"), "int a() { return 0; }\n").unwrap();
    let mut config = format!(
        "[project]\ntype='cpp'\nsource_dirs=['src']\ncpp_pkg_config={}\n",
        serde_json::to_string(packages).unwrap()
    );
    for engine in ENGINES {
        config.push_str(&format!("[engines.{engine}]\nenabled=false\n"));
    }
    fs::write(root.join("ici.toml"), config).unwrap();
    if with_tool {
        let fake = tools.join("pkg-config");
        fs::write(&fake, FAKE).unwrap();
        fs::set_permissions(&fake, fs::Permissions::from_mode(0o755)).unwrap();
    }
    let log = tools.join("calls.log");
    let home = base.join("home");
    fs::create_dir(&home).unwrap();
    let environment = BTreeMap::from([
        // Without the fake, no directory on PATH holds a pkg-config (the host's is hidden).
        (
            "PATH".to_owned(),
            if with_tool {
                format!("{}:/usr/bin:/bin", tools.display())
            } else {
                tools.display().to_string()
            },
        ),
        ("HOME".to_owned(), home.display().to_string()),
        ("LANG".to_owned(), "C.UTF-8".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            base.display().to_string(),
        ),
    ]);
    let invocation = registry::parse(["ici", "verify", "--report"]).unwrap();
    commands::dispatch(&invocation, &root, &environment).unwrap();
    let report: Value =
        serde_json::from_str(&fs::read_to_string(root.join("verify_report.json")).unwrap())
            .unwrap();
    let strings = |value: &Value| -> Vec<String> {
        value
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item.as_str().unwrap().to_owned())
            .collect()
    };
    // The capability inventory's own pkg-config probes (`--version`, `--modversion`) are not
    // the package probe.
    let calls = fs::read_to_string(&log)
        .unwrap_or_default()
        .lines()
        .filter(|call| call.starts_with("--cflags "))
        .map(str::to_owned)
        .collect();
    Run {
        // No include directory exists, so the include flags are the package flags alone.
        flags: strings(&report["analysis_context"]["project"]["cpp_include_flags"]),
        limitations: strings(&report["native_run"]["limitations"])
            .into_iter()
            .filter(|limitation| {
                limitation.contains("pkg-config") || limitation.contains("cpp_pkg_config")
            })
            .collect(),
        calls,
    }
}

fn exactly(limitations: &[String], expected: &[&str]) {
    assert_eq!(limitations, expected, "{limitations:#?}");
}

#[test]
fn every_package_without_flags_is_an_explicit_limitation() {
    let run = run(&["good", "-bad", "absent", "binary", "signal"], true);
    // Positive: the package pkg-config answers keeps its flags, in order.
    assert_eq!(run.flags, ["-DGOOD", "-DGOOD=2"]);
    // The refused expressions never reach pkg-config; the others run in order.
    assert_eq!(
        run.calls,
        [
            "--cflags good",
            "--cflags absent",
            "--cflags binary",
            "--cflags signal"
        ]
    );
    exactly(
        &run.limitations,
        &[
            "project.cpp_pkg_config package \"-bad\" starts with '-' and would be read as a pkg-config option; it was not passed to pkg-config and added no flags",
            "pkg-config --cflags absent ended with exit 1; no flags were added for it",
            "pkg-config --cflags binary printed non-UTF-8 or NUL output; no flags were added for it",
            "pkg-config --cflags signal did not complete (terminated by signal 9); no flags were added for it",
        ],
    );
}

#[test]
fn the_output_bound_is_frozen_one_million_characters() {
    // Exactly 1,000,000 characters (4,000 flags of 250 characters with their separators) are
    // read; one character more is the output bound, not a silent truncation.
    let accepted = run(&["exact"], true);
    assert_eq!(accepted.flags.len(), 4000, "{:?}", accepted.limitations);
    assert!(
        accepted.limitations.is_empty(),
        "{:?}",
        accepted.limitations
    );
    let refused = run(&["flood", "good"], true);
    assert_eq!(refused.flags, ["-DGOOD", "-DGOOD=2"]);
    exactly(
        &refused.limitations,
        &[
            "pkg-config --cflags flood printed more than 1000000 characters of output; no flags were added for it",
        ],
    );
}

#[test]
fn the_native_package_and_flag_bounds_and_a_missing_tool_are_explicit() {
    let names: Vec<String> = (0..129).map(|index| format!("p{index}")).collect();
    let names: Vec<&str> = names.iter().map(String::as_str).collect();
    let packages = run(&names, true);
    assert!(packages.flags.is_empty());
    assert!(packages.calls.is_empty(), "{:?}", packages.calls);
    exactly(
        &packages.limitations,
        &[
            "project.cpp_pkg_config lists 129 packages, more than 128; pkg-config was not run and no package flags were added",
        ],
    );
    let flags = run(&["good", "many"], true);
    assert!(flags.flags.is_empty(), "{:?}", flags.flags);
    exactly(
        &flags.limitations,
        &[
            "project.cpp_pkg_config flags exceed 4096 flags or 8192 bytes per flag; no package flags were added",
        ],
    );
    let missing = run(&["good"], false);
    assert!(missing.flags.is_empty());
    assert_eq!(missing.limitations.len(), 1, "{:?}", missing.limitations);
    assert!(
        missing.limitations[0]
            .starts_with("pkg-config is unavailable; no project.cpp_pkg_config flags were added: "),
        "{:?}",
        missing.limitations
    );
    // Negative: no configured package runs nothing and records nothing.
    let none = run(&[], true);
    assert!(none.flags.is_empty() && none.limitations.is_empty() && none.calls.is_empty());
}
