//! Next command options against the frozen v0.13.0 CLI (`tests/rust-parity/config/
//! capture_cli_options.py`): every case runs the actual native binary in a fresh copy of the same
//! two-component workspace and compares the exit code, the refusal class and line, the files
//! written outside `.ici/`, the publish/diff outcome line and the selected scope of a JSON
//! document (doctor profile and per-component non-omitted checks; a result's checks and gate).
//! Reviewed differences are listed in `CORRECTIONS` with their reason.
use serde_json::{Value, json};
use std::{collections::BTreeSet, fs, path::Path, process::Command};

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/config/cli-options-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

/// Reviewed differences: (case, fact, native value as JSON, reason). Every other fact of the case
/// keeps frozen's value.
const CORRECTIONS: &[(&str, &str, &str, &str)] = &[
    (
        "analyze/baseline-missing",
        "message",
        r#""config: baseline <root>/out/none.json cannot be read: No such file or directory (os error 2)""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "analyze/config-invalid",
        "message",
        r#""config: <root>/bad.toml:1:1: schema_version: workspace/component configuration requires schema_version""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "analyze/profile-invalid",
        "message",
        r#""error: invalid value 'huge' for '--profile <profile>'""#,
        "Same usage error, native parser wording.",
    ),
    (
        "analyze/profile-invalid",
        "refusal",
        r#""error""#,
        "Command-line usage errors are reported by the native parser as `error: invalid value ...` (exit 2, nothing written) where frozen typer drew an unclassed usage box; same exit code.",
    ),
    (
        "diff/next-missing-baseline",
        "message",
        r#""diff: baseline <root>/out/none.json cannot be read: No such file or directory (os error 2)""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "diff/next-missing-current",
        "message",
        r#""diff: <root>/out/none.json cannot be read: No such file or directory (os error 2)""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "diff/top-missing-baseline",
        "message",
        r#""diff: baseline <root>/out/none.json cannot be read: No such file or directory (os error 2)""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "diff/next-unreadable-baseline",
        "message",
        r#""diff: baseline <root>/out/bad.json is not a result this version can read: Expected legacy ici.result/v3 or historical version:3 archive""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "diff/top-unreadable-baseline",
        "message",
        r#""diff: baseline <root>/out/bad.json is not a result this version can read: Expected legacy ici.result/v3 or historical version:3 archive""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "doctor/config-missing",
        "message",
        r#""config: <root>/no.toml:1:1: workspace: explicit configuration does not exist: No such file or directory (os error 2)""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "doctor/local-config-invalid",
        "message",
        r#""config: <root>/local.toml:1:11: workspace.name: local preferences may change display/cache/tool paths, never inspection policy""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "doctor/local-config-missing",
        "message",
        r#""config: <root>/no.toml:1:1: : cannot read configuration: No such file or directory (os error 2)""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "doctor/profile-invalid",
        "message",
        r#""error: invalid value 'huge' for '--profile <profile>'""#,
        "Same usage error, native parser wording.",
    ),
    (
        "doctor/profile-invalid",
        "refusal",
        r#""error""#,
        "Command-line usage errors are reported by the native parser as `error: invalid value ...` (exit 2, nothing written) where frozen typer drew an unclassed usage box; same exit code.",
    ),
    (
        "legacy-publish/html-upload-refused",
        "outcome",
        r#""[publish] --publish failed to upload ici/project/local/unversioned-0.1/index.html to team/repo (gh-pages). Check token permissions (contents: write) and branch availability.""#,
        "Native keeps its gh-pages layout ici/<label>/<ref>/<revision>/index.html (accepted in reporting.json, legacy_publish.rs normalizes it); the message, exit 1 and the refused real HTTPS connect are frozen's.",
    ),
    (
        "legacy-publish/html-without-json",
        "exit_code",
        r#"0"#,
        "Frozen defect: without a readable verify_report.json frozen cmd_publish passed None to redact_suite and crashed (AttributeError, exit 1) before its own Actions guard; native applies the documented skip (exit 0).",
    ),
    (
        "legacy-publish/html-without-json",
        "outcome",
        r#""[publish] --publish requires a GitHub Actions environment; skipping.""#,
        "The documented frozen skip line, printed because native does not crash.",
    ),
    (
        "legacy-publish/json-invalid",
        "exit_code",
        r#"0"#,
        "Frozen defect: an unparseable --json made load_suite_from_json return None and cmd_publish crashed in redact_suite (AttributeError, exit 1); native applies the documented skip (exit 0).",
    ),
    (
        "legacy-publish/json-invalid",
        "outcome",
        r#""[publish] --publish requires a GitHub Actions environment; skipping.""#,
        "The documented frozen skip line, printed because native does not crash.",
    ),
    (
        "publish/config-configured-with-page",
        "message",
        r#""config: <root>/pub.toml:24:1: publish.api_url: publication endpoint must use https://""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "publish/config-missing",
        "message",
        r#""config: <root>/no.toml:1:1: workspace: explicit configuration does not exist: No such file or directory (os error 2)""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "publish/config-not-configured",
        "message",
        r#""publish: no page at <root>/.ici/rust-v1/result.html — run `ici next report` first""#,
        "Same `publish: no page at <path> — run `ici next report` first` refusal; the default page lives in the approved native .ici/rust-v1 namespace instead of .ici/next.",
    ),
    (
        "publish/config-unconfigured-with-page",
        "outcome",
        r#""publish: not_configured — no publication destination; configure publish.repo, publish.api_url and the named token environment variable""#,
        "Same `publish: not_configured — <detail>` line and exit 2; the native detail names the keys to configure (publish.repo, publish.api_url, the token variable) instead of frozen's short hint.",
    ),
    (
        "publish/config-without-page",
        "message",
        r#""config: <root>/pub.toml:24:1: publish.api_url: publication endpoint must use https://""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "publish/configured-without-page",
        "message",
        r#""config: <root>/pub.toml:24:1: publish.api_url: publication endpoint must use https://""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "publish/local-config-missing",
        "message",
        r#""config: <root>/no.toml:1:1: : cannot read configuration: No such file or directory (os error 2)""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "publish/local-config-with-page",
        "message",
        r#""config: <root>/pub.toml:24:1: publish.api_url: publication endpoint must use https://""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "report/auxiliary-invalid",
        "message",
        r#""report: <root>/aux.json is not ici.auxiliary v1 supporting evidence: Invalid report JSON: missing field `schema_id`""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "report/auxiliary-missing",
        "message",
        r#""report: cannot read supporting evidence <root>/out/none.json: No such file or directory (os error 2)""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "report/auxiliary-with-sarif",
        "message",
        r#""report: --auxiliary attaches supporting evidence to HTML and cannot be merged into SARIF""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "report/auxiliary-without-limitations",
        "exit_code",
        r#"2"#,
        "The published ici-auxiliary-v1 schema requires `limitations`; frozen load_auxiliary never checked it, native validates the schema and refuses (report: class, exit 2) before writing a page.",
    ),
    (
        "report/auxiliary-without-limitations",
        "message",
        r#""report: <root>/aux.json is not ici.auxiliary v1 supporting evidence: Invalid report JSON: missing field `limitations`""#,
        "Native names the file and the missing schema field.",
    ),
    (
        "report/auxiliary-without-limitations",
        "refusal",
        r#""report""#,
        "Native classes the refusal as frozen classed every other auxiliary refusal (`report: `).",
    ),
    (
        "report/auxiliary-without-limitations",
        "written",
        r#"[]"#,
        "Nothing is written for a refused document.",
    ),
    (
        "report/result-unreadable",
        "message",
        r#""report: <root>/out/bad.json is not a result this version can read: Expected legacy ici.result/v3 or historical version:3 archive""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "verify/config-missing",
        "message",
        r#""config: <root>/no.toml:1:1: workspace: explicit configuration does not exist: No such file or directory (os error 2)""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "verify/local-config-invalid",
        "message",
        r#""config: <root>/local.toml:1:11: workspace.name: local preferences may change display/cache/tool paths, never inspection policy""#,
        "Native refusal line: same class, exit code and named file; the problem is worded by the native loader (file:line:column: key: problem) instead of the Python exception text.",
    ),
    (
        "verify/result",
        "written",
        r#"["out/base.html", "out/base.json"]"#,
        "Approved native design (execution plan: results point to an offline HTML page): text-mode next verify also writes the offline page next to --result (out/base.html); the result itself is frozen's.",
    ),
    (
        "global/unknown-command",
        "message",
        r#""error: unrecognized subcommand 'bogus'""#,
        "Same usage error, native parser wording.",
    ),
    (
        "global/unknown-command",
        "refusal",
        r#""error""#,
        "Command-line usage errors are reported by the native parser as `error: ...` (exit 2, nothing written) where frozen typer drew an unclassed usage box; same exit code.",
    ),
    (
        "global/unknown-option",
        "message",
        r#""error: unexpected argument '--bogus' found""#,
        "Same usage error, native parser wording.",
    ),
    (
        "global/unknown-option",
        "refusal",
        r#""error""#,
        "Command-line usage errors are reported by the native parser as `error: ...` (exit 2, nothing written) where frozen typer drew an unclassed usage box; same exit code.",
    ),
    (
        "init/top-level-existing",
        "message",
        r#""init: <root>/ici.toml already exists — pass --force to rewrite it (a .before-init backup is kept) or --preview to inspect""#,
        "Same `init: ` class, exit 2 and named file; native --force rewrites the file as the sparse schema v2 scaffold with a .before-init backup (command:ici next init) instead of overwriting it, and says so.",
    ),
];

fn scope_facts(document: &Value) -> Value {
    if let (Some(components), Some(profile)) =
        (document["components"].as_array(), document.get("profile"))
    {
        return json!({
            "profile": profile,
            "components": components.iter().map(|item| {
                let checks: BTreeSet<&str> = item["checks"].as_array().unwrap().iter()
                    .filter(|check| check["status"] != "omitted")
                    .map(|check| check["check"].as_str().unwrap()).collect();
                (item["id"].as_str().unwrap().to_owned(), json!(checks))
            }).collect::<serde_json::Map<_, _>>(),
        });
    }
    if let (Some(checks), Some(gate)) = (document["checks"].as_array(), document.get("gate")) {
        let ids: BTreeSet<&str> = checks
            .iter()
            .map(|check| check["check_id"].as_str().unwrap())
            .collect();
        return json!({"checks": ids, "gate": gate["selected"]});
    }
    Value::Null
}

fn files(root: &Path) -> BTreeSet<String> {
    let mut found = BTreeSet::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                pending.push(path);
            } else {
                found.insert(
                    path.strip_prefix(root)
                        .unwrap()
                        .to_string_lossy()
                        .into_owned(),
                );
            }
        }
    }
    found
}

fn run(case: &Value) -> Value {
    let work = tempfile::tempdir().unwrap();
    let project = work.path().join("project");
    let home = work.path().join("home");
    fs::create_dir_all(&home).unwrap();
    for (name, text) in case["files"].as_object().unwrap() {
        let path = project.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    let call = |argv: &Value| {
        Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(
                argv.as_array()
                    .unwrap()
                    .iter()
                    .map(|arg| arg.as_str().unwrap()),
            )
            .current_dir(&project)
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("HOME", &home)
            .envs(
                case["env"]
                    .as_object()
                    .unwrap()
                    .iter()
                    .map(|(key, value)| (key, value.as_str().unwrap())),
            )
            .output()
            .unwrap()
    };
    for step in case["steps"].as_array().unwrap() {
        call(step);
    }
    let before = files(&project);
    let output = call(&case["argv"]);
    let written: Vec<String> = files(&project)
        .into_iter()
        .filter(|path| {
            !before.contains(path)
                && !Path::new(path)
                    .components()
                    .any(|part| part.as_os_str() == ".ici")
        })
        .collect();
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let root = project.to_string_lossy().into_owned();
    let class = regex::Regex::new(r"^([a-z][a-z-]*): ").unwrap();
    let line = stderr.lines().find(|line| {
        class
            .captures(line)
            .is_some_and(|captures| !matches!(&captures[1], "root" | "scope"))
    });
    let outcome = stdout
        .lines()
        .find(|line| {
            line.starts_with("publish: ")
                || line.starts_with("incompatible: ")
                || line.starts_with("[publish] ")
                || line.contains(" new, ")
        })
        .map(|line| line.replace(&root, "<root>"));
    let argv: Vec<&str> = case["argv"]
        .as_array()
        .unwrap()
        .iter()
        .map(|arg| arg.as_str().unwrap())
        .collect();
    let result = (argv.starts_with(&["next", "verify"]) && argv.contains(&"--result"))
        .then(|| {
            let target = argv[argv.iter().position(|arg| *arg == "--result").unwrap() + 1];
            fs::read_to_string(project.join(target))
                .ok()
                .map(|text| scope_facts(&serde_json::from_str(&text).unwrap()))
        })
        .flatten()
        .unwrap_or(Value::Null);
    json!({
        "exit_code": output.status.code(),
        "refusal": line.map(|line| class.captures(line).unwrap()[1].to_owned()),
        "message": line.map(|line| line.replace(&root, "<root>")),
        "outcome": outcome,
        "written": written,
        "stdout": serde_json::from_str::<Value>(&stdout).map_or(Value::Null, |document| scope_facts(&document)),
        "result": result,
    })
}

/// The fixture kind of a captured case: a frozen exit 0 is positive; an absent input (a missing
/// file or page, no destination, a refused upload) is a failure; any other refusal is negative.
fn kind(name: &str, case: &Value) -> &'static str {
    let failure = [
        "missing",
        "refused",
        "without",
        "not-configured",
        "unconfigured",
    ];
    if case["frozen"]["exit_code"] == 0 {
        "positive"
    } else if failure.iter().any(|word| name.contains(word)) {
        "failure"
    } else {
        "negative"
    }
}

/// Replays every case of the group (`<prefix>/`) and kind; returns how many ran.
fn replay(prefix: &str, wanted: &str) -> usize {
    let oracle = oracle();
    let mut wrong = vec![];
    let mut count = 0;
    for (name, case) in oracle["cases"].as_object().unwrap() {
        if !name.starts_with(&format!("{prefix}/")) || kind(name, case) != wanted {
            continue;
        }
        count += 1;
        let mut expected = case["frozen"].clone();
        for (_, fact, native, _) in CORRECTIONS.iter().filter(|(case, ..)| case == name) {
            assert!(expected.get(*fact).is_some(), "{name}: {fact}");
            expected[*fact] = serde_json::from_str(native).unwrap();
        }
        let actual = run(case);
        if actual != expected {
            wrong.push(format!(
                "{name}\nnative {}\nfrozen {}",
                serde_json::to_string(&actual).unwrap(),
                serde_json::to_string(&expected).unwrap()
            ));
        }
    }
    assert!(wrong.is_empty(), "{}", wrong.join("\n"));
    count
}

macro_rules! cases {
    ($($test:ident: $prefix:literal $kind:literal $count:literal;)*) => {$(
        #[test]
        fn $test() {
            assert_eq!(replay($prefix, $kind), $count);
        }
    )*};
}

// next doctor --component/--python/--cpp/--profile/--config/--local-config.
cases! {
    next_doctor_selection_profile_and_config_options_match_frozen: "doctor" "positive" 7;
    next_doctor_unknown_component_profile_and_invalid_overlay_are_refused_as_frozen: "doctor" "negative" 3;
    next_doctor_missing_config_and_overlay_files_are_refused_as_frozen: "doctor" "failure" 2;
}
// next verify --component/--python/--cpp/--json/--require-full/--result/--config/--local-config.
cases! {
    next_verify_selection_output_and_config_options_match_frozen: "verify" "positive" 6;
    next_verify_unknown_component_partial_full_scope_and_invalid_overlay_match_frozen: "verify" "negative" 4;
    next_verify_missing_config_and_absent_cpp_component_are_refused_as_frozen: "verify" "failure" 2;
}
// next report --result/--out (page)/--auxiliary.
cases! {
    next_report_result_page_and_auxiliary_options_match_frozen: "report" "positive" 3;
    next_report_invalid_auxiliary_sarif_merge_and_unreadable_result_are_refused_as_frozen: "report" "negative" 3;
    next_report_missing_result_and_auxiliary_are_refused_as_frozen: "report" "failure" 2;
}
// next publish --config/--local-config (offline: plain-HTTP endpoint refused, no destination).
cases! {
    next_publish_plain_http_endpoint_from_config_and_overlay_is_refused_as_frozen: "publish" "negative" 2;
    next_publish_missing_config_page_or_destination_match_frozen: "publish" "failure" 6;
}
// next diff and top-level diff/init spellings.
cases! {
    next_and_top_level_diff_spellings_match_frozen: "diff" "positive" 2;
    next_and_top_level_diff_refuse_a_non_result_baseline_as_frozen: "diff" "negative" 2;
    next_and_top_level_diff_missing_results_are_refused_as_frozen: "diff" "failure" 3;
    top_level_init_and_preview_match_frozen: "init" "positive" 2;
    top_level_init_over_an_existing_file_is_refused_as_frozen: "init" "negative" 1;
}
// Historical `ici publish` --html/--json/--report-dir outside Actions and inside a stubbed
// Actions environment whose API URL is a closed loopback port (a real refused HTTPS connect).
cases! {
    legacy_publish_html_json_and_report_dir_skip_outside_actions_as_frozen: "legacy-publish" "positive" 4;
    legacy_publish_unparseable_json_is_skipped_where_frozen_crashed: "legacy-publish" "negative" 1;
    legacy_publish_missing_pages_and_refused_uploads_exit_1_as_frozen: "legacy-publish" "failure" 6;
}
// analyze and global refusals.
cases! {
    analyze_unknown_component_profile_and_invalid_config_are_refused_as_frozen: "analyze" "negative" 3;
    analyze_missing_baseline_is_refused_before_the_run_as_frozen: "analyze" "failure" 1;
    global_version_matches_frozen: "global" "positive" 1;
    global_unknown_command_and_option_are_usage_errors_as_frozen: "global" "negative" 2;
}

#[test]
fn every_reviewed_difference_names_a_captured_case_and_a_reason() {
    let oracle = oracle();
    for (name, fact, native, reason) in CORRECTIONS {
        assert!(oracle["cases"].get(*name).is_some(), "{name}");
        assert!(
            oracle["cases"][*name]["frozen"].get(*fact).is_some(),
            "{name}: {fact}"
        );
        assert!(
            serde_json::from_str::<Value>(native).is_ok(),
            "{name}: {fact}"
        );
        assert!(!reason.is_empty(), "{name}: {fact}");
    }
    assert_eq!(oracle["cases"].as_object().unwrap().len(), 68);
}
