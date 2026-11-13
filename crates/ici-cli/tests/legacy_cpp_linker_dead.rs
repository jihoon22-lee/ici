//! Legacy `ici dead` GNU ELF section-GC projection (frozen `_cpp_linker_dead_symbols` and
//! `DeadCodeEngine._linker_findings`/`_scoped_findings`) against frozen v0.13.0 runs
//! (`tests/rust-parity/cpp/linker-dead-oracle.json`, `capture_linker_dead.py`) over
//! parity-fixtures/cpp/linker-dead: the discarded hidden function's WARN target, its exact
//! `ici.dead.gnu-elf-discarded-function` finding and `cpp_linker_details`, the exact `GNU ld
//! reachability scan` PASS rows (only sources the accepted links linked), the disabled compiler
//! scope, the configure/build failures, the required stop at an excluded archive or LTO link
//! target, the skipped `-shared` target, the standalone automatic scope frozen leaves without a
//! probed cmake (`archive-auto`, and appended to the compiler scope in `unused-auto-linker`) and
//! the compiler and linker scopes merged into one result, the stops before configure (`unity`,
//! `no-gcc`), the LLD linker identity (`gnu-ld`), the required coverage stop after the identity
//! (`coverage-required`), the source drift during the scan (`source-drift`) and the automatic
//! coverage warning with and without `doctor.required_tools` (`coverage-doctor`,
//! `doctor-required`), the failed or non-EXEC relink (`relink-failure`, `non-exec`)
//! and the automatic and required scopes under `ici verify` (`verify-auto`, `verify-required`).
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

/// The capture's PATH for one case (`capture_linker_dead.py::tools_path`): wrapper cases write
/// their tools into `tools` beside the project.
fn tools_path(name: &str, tools: &Path, project: &Path) -> String {
    use std::os::unix::fs::{PermissionsExt, symlink};
    let script = |tool: &str, text: &str| {
        fs::write(tools.join(tool), text).unwrap();
        fs::set_permissions(tools.join(tool), fs::Permissions::from_mode(0o755)).unwrap();
    };
    match name {
        name if name.contains("unprobed-driver") => {
            // Named exactly like a GCC driver, so only the probe set can exclude it.
            fs::create_dir(tools).unwrap();
            script("g++", "#!/bin/sh\nexec /usr/bin/g++ \"$@\"\n");
            "/usr/bin:/bin".to_owned()
        }
        name if name.contains("unused-replay-fails") => {
            // The project builds, but the compiler scope's own -Wunused-function replay exits 1.
            fs::create_dir(tools).unwrap();
            script(
                "g++",
                "#!/bin/sh\ncase \" $* \" in *\" -Wunused-function \"*) exit 1;; esac\nexec /usr/bin/g++ \"$@\"\n",
            );
            symlink("g++", tools.join("c++")).unwrap();
            format!("{}:/usr/bin:/bin", tools.display())
        }
        "symlinked-driver" => {
            // The PATH drivers resolve to a real GCC whose own file name is not a GCC driver name.
            fs::create_dir_all(tools.join("real")).unwrap();
            script("real/my-compiler", "#!/bin/sh\nexec /usr/bin/g++ \"$@\"\n");
            symlink("real/my-compiler", tools.join("g++")).unwrap();
            symlink("real/my-compiler", tools.join("c++")).unwrap();
            format!("{}:/usr/bin:/bin", tools.display())
        }
        "no-gcc" => {
            fs::create_dir(tools).unwrap();
            for tool in ["cmake", "readelf", "addr2line"] {
                symlink(format!("/usr/bin/{tool}"), tools.join(tool)).unwrap();
            }
            tools.display().to_string()
        }
        name if name.ends_with("gnu-ld") => {
            fs::create_dir(tools).unwrap();
            script(
                "g++",
                "#!/bin/sh\ncase \"$*\" in *-Wl,--version*) echo 'LLD 18.1.3 (compatible with GNU linkers)'; exit 0;; esac\nexec /usr/bin/g++ \"$@\"\n",
            );
            symlink("g++", tools.join("c++")).unwrap();
            format!("{}:/usr/bin:/bin", tools.display())
        }
        "non-exec" | "relink-failure" => {
            // `capture_linker_dead.py` RELINK: a `-Wl,--print-gc-sections` link over an existing
            // `-o` output is a relink; it is replaced by the PIE /usr/bin/true or fails.
            let relink = concat!(
                "#!/bin/sh\n",
                "case \" $* \" in *\" -c \"*|*\" -E \"*|*\" --version \"*|*\" -Wl,--version \"*) exec /usr/bin/g++ \"$@\";; esac\n",
                "case \" $* \" in *\" -Wl,--print-gc-sections \"*) ;; *) exec /usr/bin/g++ \"$@\";; esac\n",
                "out=; prev=; for a in \"$@\"; do [ \"$prev\" = -o ] && out=$a; prev=$a; done\n",
                "[ -e \"$out\" ] || exec /usr/bin/g++ \"$@\"\n",
            );
            let tail = if name == "non-exec" {
                "/usr/bin/g++ \"$@\" || exit $?\ncp /usr/bin/true \"$out\"\n"
            } else {
                "echo 'relink refused' >&2\nexit 1\n"
            };
            fs::create_dir(tools).unwrap();
            script("g++", &format!("{relink}{tail}"));
            symlink("g++", tools.join("c++")).unwrap();
            format!("{}:/usr/bin:/bin", tools.display())
        }
        "source-drift" => {
            fs::create_dir(tools).unwrap();
            script(
                "addr2line",
                &format!(
                    "#!/bin/sh\ncase \"$*\" in *--version*) ;; *) echo '// drift' >> '{}';; esac\nexec /usr/bin/addr2line \"$@\"\n",
                    project.join("src/common.cpp").display()
                ),
            );
            format!("{}:/usr/bin:/bin", tools.display())
        }
        _ => "/usr/bin:/bin".to_owned(),
    }
}

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
    let tools = work.path().canonicalize().unwrap().join("tools");
    let path = tools_path(name, &tools, &root);
    let mut environment = BTreeMap::from([
        ("PATH".to_owned(), path),
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
    if name.contains("unprobed-driver") {
        // CMake takes this compiler, so the link driver is a real GCC no capability probe named.
        environment.insert("CXX".to_owned(), tools.join("g++").display().to_string());
    }
    // `verify-*` trees run `ici verify` (every engine but dead disabled) and keep its dead result.
    let command = if name.starts_with("verify-") {
        "verify"
    } else {
        "dead"
    };
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
    let text = fs::read_to_string(root.join(format!("{command}_report.json")))
        .unwrap()
        .replace(&root.display().to_string(), "<root>")
        .replace(&tools.display().to_string(), "<tools>");
    let mut report: Value = serde_json::from_str(&text).unwrap();
    if command == "verify" {
        let results = report["results"].as_array().unwrap();
        let dead: Vec<&Value> = results
            .iter()
            .filter(|result| result["engine_name"] == "dead")
            .collect();
        assert_eq!(dead.len(), 1, "{name}: one dead result");
        let mut engine = dead[0].clone();
        engine["native_run"] = report["native_run"].clone();
        report = engine;
    }
    (output.exit_code, report)
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
            (path.starts_with("/usr/bin/") || path.starts_with("<tools>/"))
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

/// Cases whose link command names a path of the run directory: an absolute rpath to a project
/// shared library, or a PATH driver wrapper under the run's `tools`. `link_command_digest` hashes
/// that absolute directory and the capture and this test run in different ones, so masking it
/// here hides no product behaviour.
const RUN_PATH_CASES: [&str; 2] = ["shared", "symlinked-driver"];

/// Compares one case with frozen; `exact` keeps every captured field (the captured toolchain),
/// otherwise only what the fixture decides.
fn compare(name: &str, exact: bool) -> bool {
    // A link against a project shared library carries `-Wl,-rpath,<absolute shadow>`, so its
    // command digest names the directory the run used.
    let rpath = RUN_PATH_CASES.contains(&name);
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
        }
        if let Some(details) = picked
            .get_mut("cpp_linker_details")
            .and_then(Value::as_array_mut)
        {
            for detail in details {
                if !exact {
                    detail["tool_version"] = Value::Null;
                }
                if !exact || rpath {
                    detail["link_command_digest"] = Value::Null;
                }
            }
        }
        if let Some(details) = picked
            .get_mut("cpp_unused_details")
            .and_then(Value::as_array_mut)
        {
            for detail in details {
                // The configuration digest covers the absolute build directory of the run.
                detail["configurations"] = Value::Null;
                if !exact {
                    detail["tool_versions"] = Value::Null;
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
                    if (!exact || rpath) && picked["metrics"].get("link_command_digest").is_some() {
                        picked["metrics"]["link_command_digest"] = Value::Null;
                    }
                    picked
                })
                .collect::<Vec<_>>(),
            "findings": project(&report["findings"], native),
            "extra": extra(&report["extra"]),
        })
    };
    // decisions.md "분석 중 소스가 바뀐 legacy `ici dead` 링커 범위": a project source that changed
    // during the run also fails the native run's own input-stability gate, so the legacy view
    // keeps the incomplete exit 3 where frozen exits 1; the scope result is frozen's.
    let exit_code = if name == "source-drift"
        && exit_code == 3
        && expected["exit_code"] == 1
        && report["native_run"]["gate"]
            == json!({"has_violations":false,"reasons":["inputs changed while running; the saved findings are partial evidence"],"selected":"INCOMPLETE","workspace":"NOT_EVALUATED"})
    {
        1
    } else {
        exit_code
    };
    // decisions.md "legacy 종료 코드 3의 범위" under `verify` only: the required dead engine stays
    // ERROR/NOT_RUN on both sides, frozen ended exactly 1, and the saved gate names only the
    // failed replay. Standalone `ici dead` has no such allowance and is compared as is.
    let exit_code = if name == "verify-unused-replay-fails" && exit_code == 3 {
        assert_eq!(expected["exit_code"], 1, "{name}: frozen exit");
        for (side, engine) in [("frozen", expected), ("native", &report)] {
            assert_eq!(
                (&engine["required"], &engine["status"], &engine["evidence"]),
                (&json!(true), &json!("ERROR"), &json!("NOT_RUN")),
                "{name}: {side} dead engine"
            );
        }
        1
    } else {
        exit_code
    };
    // A failed replay exits like frozen, but the saved native gate still says what happened.
    if name.contains("unused-replay-fails") {
        assert_eq!(
            report["native_run"]["gate"],
            json!({"has_violations":false,"reasons":["project.cpp.dead: src/main.cpp: incomplete-unused-probe: Every selected compile configuration must finish once before unused diagnostics can be compared"],"selected":"INCOMPLETE","workspace":"NOT_EVALUATED"}),
            "{name}"
        );
    }
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

/// Required archive and LTO targets: frozen excludes the multi-line static-archive link.txt and
/// the `-flto` link with its message, and the required scope stops at the first exclusion after
/// the shadow configure/build (ERROR, exit 1).
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_stops_at_an_excluded_link_target_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("archive", true));
    assert!(compare("lto", true));
}

/// A `-shared` target is skipped without a message and the executable relinked; only sources
/// the accepted link linked get a pass row.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_skips_a_shared_target_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("shared", true));
}

/// A standalone automatic linker scope beside the disabled compiler scope: frozen never probed
/// cmake, so the scope is unavailable before any configure (SKIP, exit 2, no tool evidence).
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_leaves_a_standalone_automatic_scope_unprobed_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("archive-auto", true));
}

/// Standalone automatic scopes: the compiler scope runs on the analysis database and frozen
/// appends the unprobed linker scope's `C++LinkerReachabilityUnavailable` SKIP to its result.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_appends_the_unprobed_linker_scope_to_the_compiler_scope_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("unused-auto-linker", true));
}

/// Both C++ scopes required: one merged WARN result with both scopes' targets, findings, tool
/// evidence and extras, the compiler details naming the probed g++ capability version.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_merges_the_compiler_and_linker_scopes_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("unused-and-linker", true));
}

/// Frozen stops before any shadow configure: a unity build, and no GCC driver on PATH (only
/// cmake/readelf/addr2line), each the required scope's `C++LinkerReachabilityUnavailable`
/// ERROR at the first owned source with no tool evidence.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_stops_before_configure_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("unity", true));
    assert!(compare("no-gcc", true));
}

/// Frozen `_parse_link_command`: only a driver a PATH-probed gcc/g++ capability named is relinked.
/// A real GCC reached through `CXX` outside PATH leaves no eligible target, so the required scope
/// stops after the configure and build with no identity or relink process.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_skips_a_link_driver_no_probe_named_like_frozen() {
    assert_captured_toolchain();
    for case in [
        "unprobed-driver",
        "verify-unprobed-driver-auto",
        "unprobed-driver-with-unused",
    ] {
        assert!(compare(case, true), "{case}");
    }
}

/// Frozen compares the resolved driver with the resolved capability paths, not its file name: a
/// PATH g++/c++ link to a real GCC under another file name is relinked.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_relinks_a_probed_driver_under_any_file_name_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("symlinked-driver", true));
}

/// Nothing to relink (only shared libraries) is the unavailable linker scope beside the compiler
/// scope: the required one is the engine's analysis error, the automatic one a SKIP target.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_reports_no_relinkable_target_beside_the_compiler_scope_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("shared-only-with-unused", true));
    assert!(compare("verify-shared-only-auto", true));
}

/// `_eligible_commands` after the identity check: an automatic policy drops the targets without
/// complete function-section coverage, and with none retained is unavailable with every warning
/// and the identity process recorded. A required policy stops at the first such target.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_applies_function_section_coverage_by_policy_like_frozen() {
    assert_captured_toolchain();
    for case in [
        "verify-coverage-auto",
        "verify-coverage-none-auto",
        "verify-coverage-none-gnu-ld",
        "coverage-required-with-unused",
    ] {
        assert!(compare(case, true), "{case}");
    }
}

/// Frozen `_verify_gnu_linkers` before `_eligible_commands`: an LLD driver banner stops the
/// required scope at `CMakeLists.txt`, and a required coverage exclusion is reported only after
/// the GNU ld identity ran, both with the configure, build and identity processes only.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_verifies_the_linker_identity_before_coverage_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("gnu-ld", true));
    assert!(compare("coverage-required", true));
}

/// Frozen `_sources_unchanged`: a project source rewritten while addr2line ran fails the
/// required scope at the project root with the inspection counters kept.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_fails_on_a_source_changed_during_the_scan_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("source-drift", true));
}

/// Frozen `_collect_removals`: the first relink that fails (`GNU ELF relink failed for target
/// <t>`) or whose output `readelf -Wh` does not show as an ELF EXEC (`Target <t> did not produce
/// an exact ELF executable`) stops the required scope at `CMakeLists.txt` with the configure,
/// build, identity and that target's relink (and read) only.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_stops_at_a_failed_or_non_executable_relink_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("non-exec", true));
    assert!(compare("relink-failure", true));
}

/// `ici verify` probes every tool, so frozen runs the GNU ELF scope under `cpp_linker = "auto"`
/// exactly like `required` (`verify-auto`, `verify-required` over the `discarded` tree).
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_runs_the_automatic_scope_under_verify_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("verify-auto", true));
    assert!(compare("verify-required", true));
}

/// Automatic policies stay WARN (exit 0): a coverage exclusion is a warning, and
/// `doctor.required_tools` naming cmake/readelf/addr2line does not make the scope required.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_keeps_automatic_scopes_advisory_like_frozen() {
    assert_captured_toolchain();
    assert!(compare("coverage-doctor", true));
    assert!(compare("doctor-required", true));
}

/// The reported result of the compiler-first order (the real process order is
/// `legacy_cpp_linker_order.rs`): an analysis error, from the replay, a source the build also
/// fails on or a required compiler scope with no database, reports no linker scope; a shadow that
/// fails after a compiler scope that ended without one is reported beside it.
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_runs_the_compiler_scope_before_the_linker_scope_like_frozen() {
    assert_captured_toolchain();
    for case in [
        "unused-replay-fails",
        "unused-replay-fails-with-linker",
        "verify-unused-replay-fails",
        "unused-broken-with-linker",
        "unused-link-fails-with-linker",
        "broken-unused-required",
        "broken-unused-auto",
    ] {
        assert!(compare(case, true), "{case}");
    }
}

/// Frozen `_discover_links` bound (`_MAX_LINK_FILES` = 256): 257 CMake link.txt files (256
/// EXCLUDE_FROM_ALL executables CMake generates but never builds) stop the scope with one
/// `C++LinkerReachabilityError` 'CMake link-target count exceeds the bounded limit' at
/// CMakeLists.txt after the shadow configure and build, ERROR NOT_RUN, exit 1; at exactly 256
/// the bound passes and the required policy stops at the first unbuilt target instead
/// ('Link target extra_1 was excluded: link target references an unsafe or missing direct
/// object').
#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_stops_at_the_link_target_bound_like_frozen() {
    assert_captured_toolchain();
    let oracle = oracle();
    // Independent literals: the frozen bound message and the edge's exclusion.
    assert_eq!(
        oracle["cases"]["link-target-limit"]["summary"],
        "CMake link-target count exceeds the bounded limit"
    );
    assert_eq!(
        oracle["cases"]["link-target-limit-edge"]["summary"],
        "Link target extra_1 was excluded: link target references an unsafe or missing direct object"
    );
    for case in ["link-target-limit", "link-target-limit-edge"] {
        assert_eq!(oracle["cases"][case]["exit_code"], 1, "{case}");
        assert!(compare(case, true), "{case}");
    }
}

/// Every captured tree with the host's `/usr/bin` cmake/g++/binutils (any version, as on the CI
/// runner): verdicts, targets, findings, locations and extras except tool versions, link and
/// configuration digests (both cover absolute paths) and the toolchain-dependent
/// discarded/ambiguous/kept section counters.
#[test]
fn legacy_cpp_linker_dead_matches_frozen_with_the_host_toolchain() {
    let oracle = oracle();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 40);
    let failed: Vec<&String> = cases.keys().filter(|name| !compare(name, false)).collect();
    assert!(failed.is_empty(), "{failed:?}");
}
