//! The real cmake and g++ invocation order of legacy `ici dead` with a linker scope, from wrapper
//! marker logs, against frozen (`capture_linker_order.py`): the compiler scope runs first and an
//! analysis error means the shadow is never configured or built. Reports are compared in
//! `legacy_cpp_linker_dead.rs`; these tests read what actually ran.
use serde_json::{Value, json};
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
    let (tool, rest) = line.split_once('\t')?;
    let (cwd, args) = rest.split_once('\t')?;
    let tokens: Vec<&str> = args.split_whitespace().collect();
    let has = |token: &str| tokens.contains(&token);
    let role = if cwd.contains("link-reachability") || args.contains("link-reachability") {
        "shadow"
    } else {
        "release"
    };
    if tool == "cmake" {
        if has("--build") {
            return Some(format!("BUILD:{role}"));
        }
        if has("--version") || has("-E") {
            return None;
        }
        return tokens
            .iter()
            .any(|token| token.starts_with("-S") || token.starts_with("-B"))
            .then(|| format!("CONFIGURE:{role}"));
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
        return Some(format!("compile:{}:{role}", base(source)));
    }
    (args.contains(".o") && has("-o")).then(|| format!("link:{role}"))
}

struct Run {
    exit: Option<i32>,
    events: Vec<String>,
    custom_runs: usize,
    output_files: Vec<Value>,
    work_root: std::path::PathBuf,
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
                "#!/bin/sh\nprintf '%s\\t%s\\t%s\\n' {tool} \"$PWD\" \"$*\" >> '{}'\n{extra}exec /usr/bin/{tool} \"$@\"\n",
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
        custom_runs: fs::read_to_string(base.join("custom-ran"))
            .map(|text| text.lines().count())
            .unwrap_or(0),
        output_files: output_files(&project),
        work_root: base.clone(),
        report,
        _work: work,
    }
}

/// The published release tree (`v<version>/`): relative path and content digest, sorted.
fn output_files(project: &Path) -> Vec<Value> {
    fn walk(path: &Path, out: &mut Vec<std::path::PathBuf>) {
        for entry in fs::read_dir(path).unwrap().flatten() {
            if entry.path().is_dir() {
                walk(&entry.path(), out);
            } else {
                out.push(entry.path());
            }
        }
    }
    let mut files = vec![];
    for entry in fs::read_dir(project).unwrap().flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if entry.path().is_dir()
            && name.starts_with('v')
            && name[1..].starts_with(|c: char| c.is_ascii_digit())
        {
            walk(&entry.path(), &mut files);
        }
    }
    let mut digests: Vec<Value> = files
        .iter()
        .map(|file| {
            let output = Command::new("/usr/bin/sha256sum")
                .arg(file)
                .output()
                .unwrap();
            let text = String::from_utf8(output.stdout).unwrap();
            let digest = text.split_whitespace().next().unwrap().to_owned();
            json!([
                file.strip_prefix(project).unwrap().to_str().unwrap(),
                digest
            ])
        })
        .collect();
    digests.sort_by_key(ToString::to_string);
    digests
}

fn sorted_values(mut values: Vec<Value>) -> Value {
    values.sort_by_key(ToString::to_string);
    json!(values)
}

/// `capture_linker_order.py::engine_digest`: everything an engine's result says, comparably.
fn engine_digest(engine: &Value, work: &Path) -> Value {
    let list = |key: &str| engine[key].as_array().unwrap().iter();
    // Temporary roots differ in length, which moves where a long message is truncated.
    let message = |text: &str| -> String {
        text.replace(work.to_str().unwrap(), "<WORK>")
            .chars()
            .take(120)
            .collect()
    };
    let mut tools: Vec<&str> = list("tool_evidence")
        .map(|tool| tool["name"].as_str().unwrap())
        .collect();
    tools.sort_unstable();
    json!([
        engine["engine_name"],
        engine["status"],
        engine["evidence"],
        engine["summary"],
        list("targets")
            .map(|target| json!([target["target_name"], target["status"]]))
            .collect::<Vec<_>>(),
        sorted_values(
            list("findings")
                .map(|finding| {
                    json!([
                        finding["rule_id"],
                        message(finding["message"].as_str().unwrap()),
                        finding["primary_location"]["path"],
                        finding["primary_location"]["start_line"]
                    ])
                })
                .collect()
        ),
        tools,
        sorted_values(
            list("artifact_manifests")
                .flat_map(|manifest| manifest["artifacts"].as_array().unwrap().iter())
                .map(|artifact| json!([artifact["id"], artifact["kind"], artifact["target"]]))
                .collect()
        ),
    ])
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

/// The native gate of a run is INCOMPLETE because of the dead check, and of nothing else.
fn dead_gate_only(report: &Value) -> bool {
    let gate = &report["native_run"]["gate"];
    let reasons = gate["reasons"].as_array().unwrap();
    gate["selected"] == "INCOMPLETE"
        && !reasons.is_empty()
        && reasons
            .iter()
            .all(|reason| reason.as_str().unwrap().starts_with("project.cpp.dead:"))
}

/// Sorted tool evidence names. Native reads each direct object's readelf tables once up front
/// where frozen reads only the objects with a discarded section (decisions.md "compiler·linker·
/// binutils 실제 실행 증거"), so those three names count once on both sides.
fn tool_names<'a>(names: impl Iterator<Item = &'a str>) -> Vec<String> {
    const OBJECT_READS: [&str; 3] = ["readelf --section-groups", "readelf -WS", "readelf -Ws"];
    let mut names: Vec<String> = names.map(str::to_owned).collect();
    names.sort();
    names.dedup_by(|a, b| a == b && OBJECT_READS.contains(&a.as_str()));
    names
}

/// The two commands whose dead engine defers its shadow behind the compiler scope.
const SHADOW_STOP_RUNS: [(&str, &str, &str); 3] = [
    ("linker-dead", "unused-and-linker", "dead"),
    ("linker-order", "verify-control", "verify"),
    ("linker-order", "verify-deep-control", "verify"),
];

fn built(events: &[String]) -> bool {
    events.iter().any(|event| event == "BUILD:shadow")
}

#[test]
#[ignore = "needs /usr/bin/cmake and /usr/bin/g++"]
fn the_compiler_scope_and_shadow_run_in_frozen_order_with_the_same_custom_commands() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/linker-order-oracle.json"
    ))
    .unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 20);
    let mut differences = vec![];
    let mut used_exit_row = vec![];
    for (name, frozen) in cases {
        let family = if name.starts_with("custom-command") || name.starts_with("verify-") {
            "linker-order"
        } else {
            "linker-dead"
        };
        let command = if name.starts_with("verify-") {
            "verify"
        } else {
            "dead"
        };
        let actual = run(family, name, command, |_| replay_failure(name));
        let report = actual.report.as_ref().expect("report");
        let engine = if command == "verify" {
            report["results"]
                .as_array()
                .unwrap()
                .iter()
                .find(|engine| engine["engine_name"] == "dead")
                .unwrap()
        } else {
            report
        };
        let mut findings: Vec<Value> = engine["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                json!([
                    finding["rule_id"],
                    finding["message"],
                    finding["primary_location"]["path"],
                    finding["primary_location"]["start_line"]
                ])
            })
            .collect();
        findings.sort_by_key(ToString::to_string);
        let mut frozen_findings = frozen["findings"].as_array().unwrap().clone();
        frozen_findings.sort_by_key(ToString::to_string);
        let verdict = json!({
            "status": engine["status"], "evidence": engine["evidence"],
            "required": engine["required"], "summary": engine["summary"],
            "targets": engine["targets"].as_array().unwrap().iter()
                .map(|target| json!([target["target_name"], target["status"]]))
                .collect::<Vec<_>>(),
            "findings": findings,
        });
        let frozen_verdict = json!({
            "status": frozen["status"], "evidence": frozen["evidence"],
            "required": frozen["required"], "summary": frozen["summary"],
            "targets": frozen["targets"],
            "findings": frozen_findings,
        });
        let native_tools = tool_names(
            engine["tool_evidence"]
                .as_array()
                .unwrap()
                .iter()
                .filter_map(|tool| tool["name"].as_str()),
        );
        let frozen_tools = tool_names(
            frozen["tool_names"]
                .as_array()
                .unwrap()
                .iter()
                .filter_map(Value::as_str),
        );
        let other_engines: Vec<Value> = report["results"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|engine| engine["engine_name"] != "dead")
            .map(|engine| engine_digest(engine, &actual.work_root))
            .collect();
        // decisions.md "legacy 종료 코드 3의 범위", `verify` only: a required dead engine that
        // stays ERROR/NOT_RUN where frozen ends 1.
        let exit_within_row = command == "verify"
            && actual.exit == Some(3)
            && frozen["exit_code"] == 1
            && frozen["required"] == true
            && frozen["status"] == "ERROR"
            && frozen["evidence"] == "NOT_RUN"
            && dead_gate_only(report);
        if exit_within_row {
            used_exit_row.push(name.clone());
        }
        let wanted: Vec<String> = serde_json::from_value(frozen["events"].clone()).unwrap();
        let order_matches = if name == "verify-deep-control" || name == "verify-deep-broken" {
            // decisions.md "legacy `verify` deep의 release build 선행과 분석 DB 재사용": with the
            // build engine selected, native's release build runs first and is reused as the
            // analysis database. Everything but that move and one release CONFIGURE is equal.
            let release = |event: &&String| event.ends_with(":release");
            let others = |events: &[String]| {
                with_build_compiles_sorted(events.iter().filter(|e| !release(e)).cloned().collect())
            };
            let mut frozen_release: Vec<String> = wanted.iter().filter(release).cloned().collect();
            let configure = frozen_release
                .iter()
                .position(|e| e == "CONFIGURE:release")
                .unwrap();
            frozen_release.remove(configure);
            frozen_release.sort();
            let mut native_release: Vec<String> =
                actual.events.iter().filter(release).cloned().collect();
            native_release.sort();
            others(&actual.events) == others(&wanted) && native_release == frozen_release
        } else if name.starts_with("verify-linker-only-") {
            // Open, cpp.json: with `cpp_unused = "off"` frozen still configures the analysis
            // database no selected check reads, and native does not.
            wanted.first().map(String::as_str) == Some("CONFIGURE:release")
                && with_build_compiles_sorted(actual.events.clone())
                    == with_build_compiles_sorted(wanted[1..].to_vec())
        } else {
            with_build_compiles_sorted(actual.events.clone()) == with_build_compiles_sorted(wanted)
        };
        if !order_matches
            || frozen["custom_runs"] != actual.custom_runs
            || json!(actual.output_files) != frozen["output_files"]
            || verdict != frozen_verdict
            || native_tools != frozen_tools
            || json!(other_engines) != frozen["other_engines"]
            || !(exit_within_row || actual.exit.map(i64::from) == frozen["exit_code"].as_i64())
        {
            differences.push(format!(
                "{name}:\n native {:?} custom={} exit={:?} {verdict} {native_tools:?} {other_engines:?} {:?}\n frozen {frozen}",
                actual.events, actual.custom_runs, actual.exit, actual.output_files
            ));
        }
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_eq!(
        used_exit_row,
        [
            "verify-custom-command-broken",
            "verify-deep-broken",
            "verify-replay-fails"
        ]
    );
    // Guards against a vacuous comparison: the control really builds, the error path never.
    assert_eq!(cases["custom-command-control"]["custom_runs"], 1);
    assert_eq!(cases["custom-command-broken"]["custom_runs"], 0);
    assert_eq!(cases["verify-custom-command-control"]["custom_runs"], 1);
    assert_eq!(cases["verify-custom-command-broken"]["custom_runs"], 0);
    // The build engine still runs its own build, custom command included, on the error path.
    assert_eq!(cases["verify-deep-broken"]["other_engines"][0][0], "build");
    assert_eq!(cases["verify-deep-control"]["custom_runs"], 2);
    assert_eq!(cases["verify-deep-broken"]["custom_runs"], 1);
    assert!(
        !cases["verify-deep-control"]["output_files"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    // A deep run without the build engine has no release build to move or reuse.
    assert!(
        cases["verify-deep-build-disabled"]["other_engines"]
            .as_array()
            .unwrap()
            .is_empty()
    );
}

/// A cancel arriving during the compiler scope starts no shadow build and keeps exit 130.
#[test]
#[ignore = "needs /usr/bin/cmake and /usr/bin/g++"]
fn a_cancel_during_the_compiler_scope_starts_no_shadow_build() {
    for (family, name, command) in SHADOW_STOP_RUNS {
        let actual = run(family, name, command, |_| on_last_replay("kill -INT $PPID"));
        assert_eq!(actual.exit, Some(130), "{command}: {:?}", actual.events);
        assert!(!built(&actual.events), "{command}: {:?}", actual.events);
        if name == "verify-deep-control" {
            // The selected build engine's release build finished first; its records remain.
            let report = actual.report.expect("report");
            let build = report["results"]
                .as_array()
                .unwrap()
                .iter()
                .find(|engine| engine["engine_name"] == "build")
                .expect("build result");
            assert_eq!(build["status"], "PASS", "{build}");
            assert!(
                build["tool_evidence"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|tool| tool["name"] == "cmake build"),
                "{build}"
            );
        }
    }
}

/// A source changing during the compiler scope starts no shadow build and keeps exit 3.
#[test]
#[ignore = "needs /usr/bin/cmake and /usr/bin/g++"]
fn a_source_changing_during_the_compiler_scope_starts_no_shadow_build() {
    for (family, name, command) in SHADOW_STOP_RUNS {
        let actual = run(family, name, command, |project| {
            on_last_replay(&format!(
                "echo '// drift' >> '{}'",
                project.join("src/main.cpp").display()
            ))
        });
        assert_eq!(actual.exit, Some(3), "{command}: {:?}", actual.events);
        assert!(!built(&actual.events), "{command}: {:?}", actual.events);
        let reasons = &actual.report.expect("report")["native_run"]["gate"]["reasons"];
        assert!(
            reasons
                .as_array()
                .unwrap()
                .iter()
                .any(|reason| reason.as_str().unwrap().contains("inputs changed")),
            "{command}: {reasons}"
        );
    }
}

/// A source changing while the selected build engine's release build runs in preparation keeps
/// the stability check: the run ends incomplete with exit 3, and no shadow is built.
#[test]
#[ignore = "needs /usr/bin/cmake and /usr/bin/g++"]
fn a_source_changing_during_the_deep_release_build_stays_incomplete() {
    let actual = run("linker-order", "verify-deep-control", "verify", |project| {
        format!(
            "case \"$PWD\" in *link-reachability*) ;; *) case \" $* \" in *\" -c \"*main.cpp*) case \" $* \" in *\" -Wunused-function \"*) ;; *) echo '// drift' >> '{}';; esac;; esac;; esac\n",
            project.join("src/util.cpp").display()
        )
    });
    assert_eq!(actual.exit, Some(3), "{:?}", actual.events);
    assert!(
        actual.events.iter().any(|event| event == "BUILD:release"),
        "{:?}",
        actual.events
    );
    let report = actual.report.expect("report");
    let gate = &report["native_run"]["gate"];
    assert_eq!(gate["selected"], "INCOMPLETE", "{gate}");
}
