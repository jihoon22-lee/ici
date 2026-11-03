//! Legacy `ici dead` GNU ELF section-GC projection (frozen `_cpp_linker_dead_symbols` and
//! `DeadCodeEngine._linker_findings`/`_scoped_findings`) against frozen v0.13.0 runs
//! (`tests/rust-parity/cpp/linker-dead-oracle.json`, `capture_linker_dead.py`) over
//! parity-fixtures/cpp/linker-dead: the discarded hidden function's WARN target, its exact
//! `ici.dead.gnu-elf-discarded-function` finding and `cpp_linker_details`, the exact `GNU ld
//! reachability scan` PASS rows (only sources the accepted links linked), the disabled compiler
//! scope, the configure/build failures, the required stop at an excluded archive or LTO link
//! target, the skipped `-shared` target, the standalone automatic scope frozen leaves without a
//! probed cmake (`archive-auto`) and the compiler and linker scopes merged into one result.
//! Compared: exit code, status, evidence, required flag, summary, targets, findings, every frozen
//! engine extra key and the tool evidence names. Approved difference (decisions.md
//! "compiler·linker·binutils 실제 실행 증거"): the tool evidence records the processes native
//! actually ran, the PATH `/usr/bin` tools; native reads each direct object's three readelf
//! tables once before any discarded section is known (frozen reads them only for an object with
//! a discarded section), so a case without a discarded section lists exactly those three names
//! more, in the tool evidence and in the `+`-joined scope `tool_name`. Nothing else may differ.
//!
//! The `#[ignore]` replays pin the captured g++/cmake/binutils versions; the non-ignored twin
//! runs the same trees with whatever `/usr/bin` toolchain the host has and compares only the
//! facts the fixture decides (no tool version, link digest or toolchain-dependent section count).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
    process::Command,
};

const OBJECT_READS: [&str; 3] = ["readelf -WS", "readelf -Ws", "readelf --section-groups"];

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

fn banner(executable: &str) -> Option<String> {
    let output = Command::new(executable).arg("--version").output().unwrap();
    String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .next()
        .map(str::to_owned)
}

fn names(items: &Value) -> BTreeSet<String> {
    items
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item["name"].as_str().unwrap().to_owned())
        .collect()
}

/// The names a `+`-joined scope `tool_name` lists (tool names themselves contain `+`, as in
/// `g++ GNU ELF relink`), proven by rebuilding the sorted join from the known names.
fn split(tool: &str, known: &BTreeSet<String>) -> BTreeSet<String> {
    let delimited = format!("+{tool}+");
    let listed: BTreeSet<String> = known
        .iter()
        .filter(|name| delimited.contains(&format!("+{name}+")))
        .cloned()
        .collect();
    assert_eq!(
        listed.iter().cloned().collect::<Vec<_>>().join("+"),
        tool,
        "sorted tool names"
    );
    listed
}

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/linker-dead-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

fn pick(value: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .map(|key| ((*key).to_owned(), value[*key].clone()))
            .collect(),
    )
}

const FINDING_FIELDS: [&str; 11] = [
    "rule_id",
    "severity",
    "confidence",
    "message",
    "explanation",
    "remediation",
    "tool_rule_id",
    "tool_name",
    "tool_version",
    "primary_location",
    "metrics",
];
const TARGET_FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "file_path",
    "start_line",
    "metrics",
];

/// One `ici dead --report` run of a linker-dead fixture tree: (exit code, saved report).
fn run(name: &str) -> (i32, Value) {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../parity-fixtures/cpp/linker-dead")
        .canonicalize()
        .unwrap();
    let work = tempfile::tempdir().unwrap();
    let root = work.path().canonicalize().unwrap().join("project");
    copy(&fixtures.join(name), &root);
    let home = work.path().join("home");
    fs::create_dir(&home).unwrap();
    let environment = BTreeMap::from([
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
        (
            "XDG_CONFIG_HOME".to_owned(),
            home.join(".config").display().to_string(),
        ),
        (
            "XDG_CACHE_HOME".to_owned(),
            home.join(".cache").display().to_string(),
        ),
        ("LANG".to_owned(), "C.UTF-8".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
    ]);
    let invocation = registry::parse(["ici", "dead", "--report"]).unwrap();
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
    let text = fs::read_to_string(root.join("dead_report.json"))
        .unwrap()
        .replace(&root.display().to_string(), "<root>");
    (output.exit_code, serde_json::from_str(&text).unwrap())
}

/// The approved tool-evidence difference, checked exactly: every record is a `/usr/bin` tool
/// found on PATH (never a resolved `x86_64-linux-gnu-*` capability path), and native ran
/// frozen's processes plus, only when frozen read no object table, the three object reads.
fn assert_tool_evidence(name: &str, report: &Value, expected: &Value) -> Option<bool> {
    let native = &report["tool_evidence"];
    for record in native.as_array().unwrap() {
        let path = record["path"].as_str().unwrap_or("");
        let base = Path::new(path).file_name().and_then(|f| f.to_str());
        assert!(
            path.starts_with("/usr/bin/")
                && matches!(
                    base,
                    Some("cmake" | "c++" | "g++" | "readelf" | "addr2line")
                ),
            "{name}: {record}"
        );
        assert_eq!(record["argv"][0].as_str(), Some(path), "{name}: {record}");
    }
    let frozen = names(&expected["tool_evidence"]);
    let reads: BTreeSet<String> = OBJECT_READS.iter().map(|read| (*read).to_owned()).collect();
    let extra = frozen.is_disjoint(&reads) && names(native).is_superset(&reads);
    let mut wanted = frozen.clone();
    if extra {
        wanted.extend(reads);
    }
    if names(native) != wanted {
        eprintln!(
            "== {name}: tool evidence names {:?}, wanted {wanted:?}",
            names(native)
        );
        return None;
    }
    Some(extra)
}

/// Compares one case with frozen; `exact` keeps every captured field (the captured toolchain),
/// otherwise only what the fixture decides.
fn compare(name: &str, exact: bool) -> bool {
    let oracle = oracle();
    let expected = &oracle["cases"][name];
    let (exit_code, report) = run(name);
    let Some(extra_reads) = assert_tool_evidence(name, &report, expected) else {
        return false;
    };
    let reads: BTreeSet<String> = OBJECT_READS.iter().map(|read| (*read).to_owned()).collect();
    let mut known = names(&report["tool_evidence"]);
    known.extend(names(&expected["tool_evidence"]));
    let project = |findings: &Value, native: bool| -> Vec<Value> {
        let mut findings: Vec<Value> = findings
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                let mut finding = pick(finding, &FINDING_FIELDS);
                if native
                    && extra_reads
                    && let Some(tool) = finding["tool_name"].as_str()
                    && !tool.is_empty()
                {
                    // Exactly frozen's `+`-joined names with the three object reads inserted.
                    let all = split(tool, &known);
                    assert!(all.is_superset(&reads), "{name}: {tool}");
                    let kept: Vec<String> = all.difference(&reads).cloned().collect();
                    finding["tool_name"] = json!(kept.join("+"));
                }
                if !exact {
                    for key in ["tool_version", "tool_name"] {
                        finding[key] = Value::Null;
                    }
                }
                finding
            })
            .collect();
        findings.sort_by_key(|finding| serde_json::to_string(finding).unwrap());
        findings
    };
    let extra = |extra: &Value| -> Value {
        let mut picked: serde_json::Map<String, Value> = expected["extra"]
            .as_object()
            .unwrap()
            .keys()
            .map(|key| (key.clone(), extra[key].clone()))
            .collect();
        if !exact {
            for key in [
                "cpp_linker_discarded_sections_observed",
                "cpp_linker_ambiguous_sections_excluded",
                "cpp_linker_sections_kept_by_another_target",
            ] {
                picked.remove(key);
            }
            if let Some(details) = picked
                .get_mut("cpp_linker_details")
                .and_then(Value::as_array_mut)
            {
                for detail in details {
                    for key in ["tool_version", "link_command_digest"] {
                        detail[key] = Value::Null;
                    }
                }
            }
            // The compiler scope's configuration digest covers the absolute build directory.
            if let Some(details) = picked
                .get_mut("cpp_unused_details")
                .and_then(Value::as_array_mut)
            {
                for detail in details {
                    for key in ["tool_versions", "configurations"] {
                        detail[key] = Value::Null;
                    }
                }
            }
        }
        Value::Object(picked)
    };
    let shape = |report: &Value, exit_code: &Value, native: bool| {
        json!({
            "exit_code": exit_code,
            "status": report["status"],
            "evidence": report["evidence"],
            "required": report["required"],
            "summary": report["summary"],
            "targets": report["targets"].as_array().unwrap().iter()
                .map(|target| {
                    let mut picked = pick(target, &TARGET_FIELDS);
                    // The digest covers the link argv, whose `-Wl,-rpath,<shadow>` names the
                    // absolute project path the capture ran in.
                    if !exact && picked["metrics"].get("link_command_digest").is_some() {
                        picked["metrics"]["link_command_digest"] = Value::Null;
                    }
                    picked
                })
                .collect::<Vec<_>>(),
            "findings": project(&report["findings"], native),
            "extra": extra(&report["extra"]),
        })
    };
    let actual = shape(&report, &json!(exit_code), true);
    let wanted = shape(expected, &expected["exit_code"], false);
    if actual != wanted {
        eprintln!("== {name}\nnative: {actual:#}\nfrozen: {wanted:#}");
        return false;
    }
    true
}

fn assert_captured_toolchain() {
    let oracle = oracle();
    assert_eq!(banner("/usr/bin/g++").as_deref(), oracle["gxx"].as_str());
    assert_eq!(
        banner("/usr/bin/cmake").as_deref(),
        oracle["cmake"].as_str()
    );
}

/// Positive: two executables share one object, GNU ld discards one hidden function in both
/// (the WARN target and its exact finding), another discarded function is kept by the other
/// target and excluded.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_reports_the_discarded_function_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("discarded", true));
}

/// Negative: every hidden function is used, every source is an exact `GNU ld reachability scan`
/// PASS row (frozen `_record_pass_targets`).
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_passes_every_linked_source_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("clean", true));
}

/// Failure: the CMake configure of the shadow fails, the required scope is an ERROR (exit 1).
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_stops_at_a_failed_configure_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("broken", true));
}

/// Failure: the shadow configures and then its build fails; the required scope is an ERROR
/// (exit 1) with no relink.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_stops_at_a_failed_build_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("build-failure", true));
}

/// Every captured tree with the host's `/usr/bin` cmake/g++/binutils (any version, as on the CI
/// runner): verdicts, targets, findings, locations and extras except tool versions, link and
/// configuration digests (both cover absolute paths) and the toolchain-dependent
/// discarded/ambiguous/kept section counters.
#[test]
fn legacy_cpp_linker_dead_matches_frozen_with_the_host_toolchain() {
    let oracle = oracle();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 9);
    let failed: Vec<&String> = cases.keys().filter(|name| !compare(name, false)).collect();
    assert!(failed.is_empty(), "{failed:?}");
}
