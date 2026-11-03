//! `ici next` C++ compiler diagnostics against frozen v0.13.0 (`tests/rust-parity/cpp/
//! next-compiler-oracle.json`, `capture_next_compiler.py`): a three-unit component whose
//! compile_commands.json yields one -Wunused-variable warning, one error without a -W option,
//! one clean unit and one build-generated unit under the linked build directory, with the
//! compile directory at the workspace root (`root`) or at the build directory `out` (`build`).
//!
//! Compared exactly (decisions.md "next 경로 compiler finding"): the per-TU task ids
//! (`app.cpp.diagnostics.<slug>-<n>`, frozen order, no task for the generated unit) and their
//! `-fsyntax-only` argv (the build's own flags, no added warning) apart from the named native
//! output-format flags, every
//! finding's rule id `compiler.compiler`, `<kind>: ` message with GCC's UTF-8 quotes,
//! severity, confidence, evidence, native rule (none for the error), line and column, the
//! `units.covered`/`units.generated` metrics and the gate. Fingerprints follow the native
//! identity namespace (decisions.md identity row) and are not compared.
//!
//! Open differences, pinned here and proposed to root (cpp.json `finding_site:…compiler.py:
//! 225:16`, difference_review): frozen plans and records one `cpp.diagnostics` check per TU
//! (each finding carries its TU task id and `compile-<digest>` variant), native one
//! `app.cpp.diagnostics` check whose steps are the per-TU tasks (the native plan model has one
//! check record per component and check); with the compile directory `out`, frozen parses each
//! diagnostic relative to that directory and locates it at `[external]`, native at the unit's
//! workspace source path.
//!
//! GCC's diagnostic wording for these one-line sources is the same across the GCC releases the
//! CI runner and the capture use, so the replay is not version-pinned; it needs `/usr/bin/g++`.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/next-compiler-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

fn materialize(project: &Path, oracle: &Value, directory: &str) {
    for (name, text) in oracle["files"].as_object().unwrap() {
        let path = project.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::write(project.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
    fs::create_dir_all(project.join("out")).unwrap();
    let database = oracle["database"]
        .to_string()
        .replace("<directory>", directory)
        .replace("{root}", &project.display().to_string());
    fs::write(project.join("out/compile_commands.json"), database).unwrap();
}

/// Native `ici next <command> --json`: (exit code, document with the root written `{root}`).
fn next(project: &Path, home: &Path, command: &str) -> (i32, Value) {
    let environment = BTreeMap::from([
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
        (
            "XDG_CACHE_HOME".to_owned(),
            home.join("cache").display().to_string(),
        ),
        ("LANG".to_owned(), "C.UTF-8".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
    ]);
    let invocation = registry::parse(["ici", "next", command, "--json"]).unwrap();
    let output = commands::dispatch(&invocation, project, &environment).unwrap();
    let text = output
        .stdout
        .replace(&project.display().to_string(), "{root}");
    (
        output.exit_code,
        serde_json::from_str(&text).unwrap_or_else(|error| panic!("{error}: {text}")),
    )
}

fn pick(item: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .map(|key| {
                (
                    (*key).to_owned(),
                    item.get(*key).cloned().unwrap_or(Value::Null),
                )
            })
            .collect(),
    )
}

/// The native plan's cpp.diagnostics per-TU tasks: (task id, argv).
fn planned(plan: &Value) -> Vec<Value> {
    let mut tasks = vec![];
    for component in plan["plans"].as_array().unwrap() {
        for check in component["checks"].as_array().unwrap() {
            if check["check"] != "cpp.diagnostics" {
                continue;
            }
            for step in check["steps"].as_array().cloned().unwrap_or_default() {
                if step["provider"] == "compiler" {
                    tasks.push(json!({"task_id": step["id"], "argv": step["argv"]}));
                }
            }
        }
    }
    tasks
}

/// Output-format flags native appends to every GNU replay: no colour, the option name and
/// machine-readable fix-its (`ici_providers::transform_compiler_argv`, which the parser reads)
/// and byte columns counted from 1 (`compiler_columns` "gnu-byte", exact on non-ASCII lines).
/// None of them changes which diagnostics GCC emits.
const NATIVE_FLAGS: [&str; 5] = [
    "-fdiagnostics-color=never",
    "-fdiagnostics-show-option",
    "-fdiagnostics-parseable-fixits",
    "-fdiagnostics-column-unit=byte",
    "-fdiagnostics-column-origin=1",
];

fn compare(name: &str) {
    let oracle = oracle();
    let expected = &oracle["cases"][name];
    let work = tempfile::tempdir().unwrap();
    let project = work.path().canonicalize().unwrap().join("project");
    let home = work.path().join("home");
    fs::create_dir_all(&home).unwrap();
    materialize(&project, &oracle, expected["directory"].as_str().unwrap());
    let (plan_exit, plan) = next(&project, &home, "plan");
    assert_eq!(plan_exit, expected["plan_exit_code"], "{name} plan exit");
    let tasks = planned(&plan);
    let ids = |tasks: &[Value]| -> Vec<Value> {
        tasks.iter().map(|task| task["task_id"].clone()).collect()
    };
    assert_eq!(
        ids(&tasks),
        ids(expected["planned"].as_array().unwrap()),
        "{name} per-TU task ids\n{plan:#}"
    );
    for (native, frozen) in tasks.iter().zip(expected["planned"].as_array().unwrap()) {
        let native: Vec<&str> = native["argv"]
            .as_array()
            .unwrap()
            .iter()
            .map(|argument| argument.as_str().unwrap())
            .collect();
        let frozen: Vec<&str> = frozen["argv"]
            .as_array()
            .unwrap()
            .iter()
            .map(|argument| argument.as_str().unwrap())
            .collect();
        let added: Vec<&str> = native
            .iter()
            .copied()
            .filter(|argument| !frozen.contains(argument))
            .collect();
        assert_eq!(added, NATIVE_FLAGS, "{name}: {native:?}");
        let kept: Vec<&str> = native
            .iter()
            .copied()
            .filter(|argument| !NATIVE_FLAGS.contains(argument))
            .collect();
        assert_eq!(kept, frozen, "{name} argv");
    }
    let (exit_code, verify) = next(&project, &home, "verify");
    assert_eq!(exit_code, expected["exit_code"], "{name} exit");
    let fields = [
        "rule_id",
        "message",
        "severity",
        "confidence",
        "evidence",
        "native_rule_id",
        "primary_location",
        "provider",
    ];
    let native: Vec<&Value> = verify["findings"].as_array().unwrap().iter().collect();
    let frozen: Vec<&Value> = expected["findings"].as_array().unwrap().iter().collect();
    assert_eq!(native.len(), frozen.len(), "{name} findings");
    let planned_ids = ids(expected["planned"].as_array().unwrap());
    for (native, frozen) in native.iter().zip(&frozen) {
        let mut wanted = pick(frozen, &fields);
        // Pinned open difference: frozen's `[external]` for a diagnostic parsed under the
        // build directory; native locates the TU's own source (frozen's `root` location).
        if wanted["primary_location"]["path"] == "[external]" {
            let source = if wanted["severity"] == "medium" {
                "src/a.cpp"
            } else {
                "src/b.cpp"
            };
            wanted["primary_location"]["path"] = json!(source);
        }
        assert_eq!(pick(native, &fields), wanted, "{name} finding");
        // Pinned open difference: the TU task id and `compile-<digest>` variant of frozen's
        // per-TU check against native's one check id and the unit variant.
        assert!(planned_ids.contains(&frozen["task_id"]), "{name}");
        assert_eq!(native["task_id"], "app.cpp.diagnostics", "{name}");
    }
    let metric = |metric: &Value| {
        pick(
            metric,
            &[
                "name",
                "value",
                "numerator",
                "denominator",
                "unit",
                "evidence",
            ],
        )
    };
    let metrics: Vec<Value> = verify["metrics"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|item| item["name"].as_str().unwrap().starts_with("units."))
        .map(metric)
        .collect();
    let wanted: Vec<Value> = expected["metrics"]
        .as_array()
        .unwrap()
        .iter()
        .map(metric)
        .collect();
    assert_eq!(metrics, wanted, "{name} metrics");
    assert_eq!(
        pick(&verify["gate"], &["selected", "has_violations", "reasons"]),
        pick(
            &expected["gate"],
            &["selected", "has_violations", "reasons"]
        ),
        "{name} gate"
    );
    // Pinned open difference: frozen records one cpp.diagnostics check per planned TU task,
    // native one `app.cpp.diagnostics` check over the same steps.
    let checks = |document: &Value| -> Vec<Value> {
        document["checks"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|check| check["check_id"] == "cpp.diagnostics")
            .map(|check| json!([check["task_id"], check["state"]]))
            .collect()
    };
    assert_eq!(
        checks(&verify),
        [json!(["app.cpp.diagnostics", "SUCCEEDED"])],
        "{name}"
    );
    let frozen_checks: Vec<Value> = planned_ids
        .iter()
        .map(|id| json!([id, "SUCCEEDED"]))
        .collect();
    assert_eq!(checks(expected), frozen_checks, "{name}");
}

/// Positive and failure: the warning and the error of the compile directory at the root, the
/// clean unit and the generated unit (counted, never replayed).
#[test]
fn next_compiler_findings_match_frozen_with_the_root_compile_directory() {
    compare("root");
}

/// Negative (the clean unit) and the build-directory compile directory.
#[test]
fn next_compiler_findings_match_frozen_with_the_build_compile_directory() {
    compare("build");
}

/// decisions.md "next `cpp.diagnostics`·`cpp.test` 실행 단위": native keeps one cpp.diagnostics
/// check whose per-TU steps carry the frozen task ids. Each step's state stays visible as its
/// own `task.completed` event, and a run where one TU's compiler is killed never shows the check
/// as complete or the gate as passed.
#[test]
fn a_killed_compiler_step_stays_visible_and_never_completes_the_check() {
    use std::os::unix::fs::PermissionsExt;
    let oracle = oracle();
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let project = base.join("project");
    let home = base.join("home");
    fs::create_dir_all(&home).unwrap();
    materialize(&project, &oracle, "{root}");
    let bin = base.join("bin");
    fs::create_dir_all(&bin).unwrap();
    fs::write(
        bin.join("g++"),
        "#!/bin/sh\ncase \"$*\" in\n  *--version*) exec /usr/bin/g++ \"$@\" ;;\n  \
         *c.cpp*) kill -KILL $$ ;;\nesac\nexec /usr/bin/g++ \"$@\"\n",
    )
    .unwrap();
    fs::set_permissions(bin.join("g++"), fs::Permissions::from_mode(0o755)).unwrap();
    let database = fs::read_to_string(project.join("out/compile_commands.json"))
        .unwrap()
        .replace(
            "\"/usr/bin/g++\"",
            &format!("\"{}\"", bin.join("g++").display()),
        );
    fs::write(project.join("out/compile_commands.json"), database).unwrap();
    let environment = BTreeMap::from([
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
        (
            "XDG_CACHE_HOME".to_owned(),
            home.join("cache").display().to_string(),
        ),
        ("LANG".to_owned(), "C.UTF-8".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
    ]);
    let invocation = registry::parse([
        "ici",
        "next",
        "verify",
        "--json",
        "--events",
        "events.jsonl",
    ])
    .unwrap();
    let output = commands::dispatch(&invocation, &project, &environment).unwrap();
    let verify: Value = serde_json::from_str(&output.stdout).unwrap();
    let completed: BTreeMap<String, String> = fs::read_to_string(project.join("events.jsonl"))
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .filter(|event| event["event_type"] == "task.completed")
        .filter_map(|event| {
            let id = event["task_id"].as_str()?.to_owned();
            id.starts_with("app.cpp.diagnostics.")
                .then(|| (id, event["message"].as_str().unwrap().to_owned()))
        })
        .collect();
    assert_eq!(completed.len(), 3, "{completed:?}");
    for (id, state) in &completed {
        let killed = id.starts_with("app.cpp.diagnostics.src-c-cpp");
        assert_eq!(state == "SUCCEEDED", !killed, "{completed:?}");
    }
    let check = verify["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["check_id"] == "cpp.diagnostics")
        .unwrap();
    assert_eq!(check["task_id"], "app.cpp.diagnostics");
    assert_ne!(check["state"], "SUCCEEDED", "{check}");
    assert_eq!(check["evidence_complete"], false, "{check}");
    assert_ne!(verify["gate"]["selected"], "PASS", "{}", verify["gate"]);
    assert_ne!(output.exit_code, 0);
}
