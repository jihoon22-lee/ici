//! A next clang-tidy check is never complete evidence: its providers prove no header/module
//! closure and, when the recorded database covers only part of the scope, not the scope either.
//! A run whose tidy evidence is incomplete therefore cannot resolve a baseline tidy finding,
//! even when the tool reports nothing the second time. Nothing here is an unread input.
use ici_cli::{commands, planning, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path};

fn config() -> String {
    "schema_version = 1\n[workspace]\nname = \"probe\"\nprofile = \"standard\"\n\
     [[components]]\nid = \"app\"\nroot = \"app\"\nlanguages = [\"cpp\"]\n"
        .to_owned()
        + &ici_domain::builtin_catalog()
            .checks
            .iter()
            .map(|check| {
                format!(
                    "[checks.\"{}\"]\nenabled = {}\n",
                    check.id,
                    matches!(check.id.as_str(), "cpp.compile" | "cpp.tidy")
                )
            })
            .collect::<String>()
}

fn verify(project: &Path, base: &Path, tools: &Path, extra: &[&str]) -> (i32, Value) {
    let environment = BTreeMap::from([
        ("HOME".to_owned(), base.join("home").display().to_string()),
        (
            "PATH".to_owned(),
            format!("{}:/usr/bin:/bin", tools.display()),
        ),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            base.display().to_string(),
        ),
    ]);
    let invocation = registry::parse(
        ["ici", "next", "verify", "--json"]
            .iter()
            .chain(extra)
            .copied(),
    )
    .unwrap();
    let output = commands::dispatch(&invocation, project, &environment).unwrap();
    (
        output.exit_code,
        serde_json::from_str(&output.stdout).unwrap(),
    )
}

/// A fake clang-tidy: a version banner, then one warning on `a.cpp` while `warn` exists.
fn fake_tidy(tools: &Path, warn: &Path) {
    let script = tools.join("clang-tidy");
    fs::write(
        &script,
        format!(
            "#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'LLVM version 18.1.3'; exit 0; fi\n\
             if [ -e '{}' ]; then for arg in \"$@\"; do case \"$arg\" in *a.cpp) \
             echo \"$arg:1:5: warning: use auto [modernize-use-auto]\";; esac; done; fi\nexit 0\n",
            warn.display()
        ),
    )
    .unwrap();
    fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();
}

fn tidy(document: &Value) -> &Value {
    document["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["check_id"] == "cpp.tidy")
        .unwrap_or_else(|| panic!("{document}"))
}

#[test]
fn incomplete_tidy_evidence_never_resolves_a_baseline_finding() {
    for covered in [&["a.cpp", "b.cpp"][..], &["a.cpp"][..]] {
        let work = tempfile::tempdir().unwrap();
        let base = work.path().canonicalize().unwrap();
        let project = base.join("project");
        let tools = base.join("tools");
        fs::create_dir_all(project.join("app")).unwrap();
        fs::create_dir_all(base.join("home")).unwrap();
        fs::create_dir_all(&tools).unwrap();
        fs::write(project.join(".hg"), "").unwrap();
        fs::write(project.join("ici.toml"), config()).unwrap();
        fs::write(project.join("app/a.cpp"), "int a() { return 1; }\n").unwrap();
        fs::write(project.join("app/b.cpp"), "int b() { return 2; }\n").unwrap();
        let rows: Vec<Value> = covered
            .iter()
            .map(|source| {
                serde_json::json!({"directory": project, "file": format!("app/{source}"),
                    "arguments": ["/usr/bin/g++", "-c", format!("app/{source}")]})
            })
            .collect();
        fs::write(
            project.join("compile_commands.json"),
            serde_json::to_string(&rows).unwrap(),
        )
        .unwrap();
        let warn = base.join("warn");
        fs::write(&warn, "").unwrap();
        fake_tidy(&tools, &warn);
        let (_, first) = verify(&project, &base, &tools, &["--result", "first.json"]);
        let check = tidy(&first);
        assert_eq!(check["evidence_complete"], false, "{covered:?}: {check}");
        let fingerprints: Vec<Value> = first["findings"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|finding| finding["task_id"] == check["task_id"])
            .map(|finding| finding["fingerprint"].clone())
            .collect();
        // The tool may refuse the partial scope; when it ran, its finding must not resolve.
        fs::remove_file(&warn).unwrap();
        let (_, now) = verify(
            &project,
            &base,
            &tools,
            &["--result", "now.json", "--baseline", "first.json"],
        );
        let check = tidy(&now);
        assert_eq!(check["evidence_complete"], false, "{covered:?}: {check}");
        let resolved = now["baseline"]["resolved"].as_array().cloned();
        for fingerprint in &fingerprints {
            assert!(
                !resolved.iter().flatten().any(|item| item == fingerprint),
                "{covered:?}: {}",
                now["baseline"]
            );
        }
        if covered.len() == 2 {
            assert_eq!(fingerprints.len(), 1, "{first}");
        }
    }
}

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/tidy-batch-oracle.json"
    ))
    .unwrap()
}

/// A workspace laid out like one frozen batch capture case (its files and database rows) under
/// `<base>/project`, with the frozen capture's configuration (only cpp.compile and cpp.tidy).
fn workspace(base: &Path, case: &Value, config: &str) -> std::path::PathBuf {
    let project = base.join("project");
    fs::create_dir_all(&project).unwrap();
    fs::create_dir_all(base.join("home")).unwrap();
    fs::write(base.join(".hg"), "").unwrap();
    for (name, text) in case["files"].as_object().unwrap() {
        let path = project.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    for (name, target) in case["links"].as_object().into_iter().flatten() {
        let path = project.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::os::unix::fs::symlink(target.as_str().unwrap(), path).unwrap();
    }
    let rows: Vec<Value> = case["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| {
            let directory = if row[0] == "." {
                project.clone()
            } else {
                project.join(row[0].as_str().unwrap())
            };
            let root = project.display().to_string();
            let local = |value: &Value| value.as_str().unwrap().replace("{root}", &root);
            let arguments: Vec<String> = row[2].as_array().unwrap().iter().map(local).collect();
            let mut entry = serde_json::json!({"directory": directory, "file": local(&row[1]), "arguments": arguments});
            for (key, value) in row.get(3).and_then(Value::as_object).into_iter().flatten() {
                entry[key] = value.clone();
            }
            entry
        })
        .collect();
    fs::write(
        project.join("compile_commands.json"),
        serde_json::to_string(&rows).unwrap(),
    )
    .unwrap();
    fs::write(project.join("ici.toml"), config).unwrap();
    project
}

/// A clang-tidy that records every call (cwd, argv element by element) and answers `script`.
fn recording_tidy(tools: &Path, log: &Path, run: &str) {
    fs::create_dir_all(tools).unwrap();
    fs::create_dir_all(log).unwrap();
    let script = tools.join("clang-tidy");
    fs::write(
        &script,
        format!(
            "#!/bin/sh\nd=$(mktemp -d \"{log}/call-XXXXXXXX\") || exit 125\n\
             date +%s%N > \"$d/start\"\npwd > \"$d/cwd\"\n\
             for a in \"$0\" \"$@\"; do printf '%s\\0' \"$a\"; done > \"$d/argv\"\n{run}\n",
            log = log.display()
        ),
    )
    .unwrap();
    fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();
}

struct Call {
    cwd: String,
    argv: Vec<String>,
    stdout: String,
    stderr: String,
}

fn calls(log: &Path) -> Vec<Call> {
    let mut entries: Vec<_> = fs::read_dir(log)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .collect();
    entries.sort_by_key(|path| {
        fs::read_to_string(path.join("start"))
            .unwrap()
            .trim()
            .parse::<u128>()
            .unwrap()
    });
    entries
        .iter()
        .map(|path| Call {
            cwd: fs::read_to_string(path.join("cwd"))
                .unwrap()
                .trim()
                .to_owned(),
            argv: fs::read(path.join("argv"))
                .unwrap()
                .split(|byte| *byte == 0)
                .filter(|item| !item.is_empty())
                .map(|item| String::from_utf8(item.to_vec()).unwrap())
                .collect(),
            stdout: fs::read_to_string(path.join("stdout")).unwrap_or_default(),
            stderr: fs::read_to_string(path.join("stderr")).unwrap_or_default(),
        })
        .collect()
}

fn next_verify(project: &Path, base: &Path, tools: &Path) -> (i32, Value) {
    verify(project, base, tools, &["--result", "now.json"])
}

fn blocked_reason(document: &Value) -> String {
    let check = tidy(document);
    assert_eq!(check["state"], "BLOCKED", "{document}");
    document.to_string()
}

/// Frozen next `ClangTidyProvider` argv over two units of different directories: one process
/// for the component, `-p <database parent>`, the covered sources in database order, cwd the
/// workspace root. Nothing else is added (no `--checks`, `--config`, `--` replay or library
/// projection); the configuration clang-tidy discovers is snapshotted as an input.
#[test]
fn next_tidy_is_one_frozen_process_per_component() {
    let oracle = oracle();
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let mut case = oracle["cases"]["flags-cwd"].clone();
    case["files"]["app/.clang-tidy"] = "Checks: '-*,clang-analyzer-*'\n".into();
    let project = workspace(&base, &case, case["config"].as_str().unwrap());
    let tools = base.join("tools");
    let log = base.join("calls");
    recording_tidy(&tools, &log, "exit 0");
    let (code, document) = next_verify(&project, &base, &tools);
    assert_eq!(code, 0, "{document}");
    let calls = calls(&log);
    assert_eq!(calls.len(), 1);
    let root = project.display().to_string();
    assert_eq!(calls[0].cwd, root);
    assert_eq!(
        calls[0].argv,
        [
            tools.join("clang-tidy").display().to_string(),
            "-p".into(),
            root.clone(),
            format!("{root}/app/a.cpp"),
            format!("{root}/app/sub/b.cpp"),
        ]
    );
    let check = tidy(&document);
    assert_eq!(check["state"], "SUCCEEDED");
    assert_eq!(check["evidence_complete"], false);
    assert_eq!(check["analysis_unit_id"], "app.cpp");
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let environment = BTreeMap::from([
        ("HOME".to_owned(), base.join("home").display().to_string()),
        (
            "PATH".to_owned(),
            format!("{}:/usr/bin:/bin", tools.display()),
        ),
    ]);
    let prepared = planning::prepare(&invocation, &project, &environment, true).unwrap();
    let providers = &prepared.cpp.diagnostics["app.cpp.tidy"];
    assert_eq!(providers.len(), 1);
    let task = &providers[0].task;
    assert_eq!(task.timeout_seconds, Some(900.0));
    // Frozen `execution/process.py` DEFAULT_OUTPUT_LIMIT per stream.
    assert_eq!(task.output_limit_bytes, Some(1_000_000));
    assert!(!task.cacheable);
    assert!(prepared.cpp.tooling_searches.is_empty());
    for input in [
        "compile_commands.json",
        "app/a.cpp",
        "app/sub/b.cpp",
        "app/.clang-tidy",
    ] {
        assert!(
            task.input_refs.contains(&format!("{root}/{input}")),
            "{input}: {:?}",
            task.input_refs
        );
    }
    assert!(
        prepared
            .cpp
            .evidence_configurations_for("app.cpp.tidy")
            .contains("app/.clang-tidy")
    );
    assert!(
        ici_cli::inputs::capture(&prepared)
            .unwrap()
            .files
            .contains_key("app/.clang-tidy")
    );
}

/// `-p` and configuration discovery read inputs frozen never checks. Each one that clang-tidy
/// would read unchecked blocks the check before anything runs: a configuration above the
/// workspace, `compile_flags.txt` beside the database (read instead of it), an unsafe or
/// symlinked configuration, a database under another name. Negative controls run.
#[test]
fn next_tidy_refuses_inputs_clang_tidy_would_read_unchecked() {
    let oracle = oracle();
    let config = oracle["cases"]["config"]["config"].as_str().unwrap();
    type Mutation = Box<dyn Fn(&Path, &Path)>;
    let refused: Vec<(Mutation, &str)> = vec![
        (
            Box::new(|base, _| fs::write(base.join(".clang-tidy"), "Checks: '-*'\n").unwrap()),
            "clang-tidy would read the unchecked configuration",
        ),
        (
            Box::new(|_, project| fs::write(project.join("compile_flags.txt"), "-DX\n").unwrap()),
            "clang-tidy -p would read the unvalidated compile_flags.txt instead of the selected database",
        ),
        (
            Box::new(|_, project| {
                fs::write(
                    project.join("app/.clang-tidy"),
                    "Checks: '-*'\n\"\\x45xtraArgs\": ['-fplugin=x.so']\n",
                )
                .unwrap()
            }),
            "clang-tidy config may not inject compiler arguments or inherit parent config",
        ),
        (
            Box::new(|_, project| {
                fs::write(
                    project.join("app/.clang-tidy"),
                    "Checks: '-*'\nInheritParentConfig: true\n",
                )
                .unwrap()
            }),
            "clang-tidy config may not inject compiler arguments or inherit parent config",
        ),
        (
            Box::new(|base, project| {
                fs::remove_file(project.join("app/.clang-tidy")).unwrap();
                fs::write(base.join("real-tidy"), "Checks: '-*'\n").unwrap();
                std::os::unix::fs::symlink(base.join("real-tidy"), project.join("app/.clang-tidy"))
                    .unwrap();
            }),
            "clang-tidy config must be contained without symlink traversal",
        ),
        (
            Box::new(|_, project| {
                fs::write(project.join("app/.clang-tidy"), b"Checks: '-*'\0\n").unwrap()
            }),
            "clang-tidy config may not inject compiler arguments or inherit parent config",
        ),
    ];
    for (index, (mutate, reason)) in refused.iter().enumerate() {
        let work = tempfile::tempdir().unwrap();
        let base = work.path().canonicalize().unwrap();
        let project = workspace(&base, &oracle["cases"]["config"], config);
        let tools = base.join("tools");
        let log = base.join("calls");
        recording_tidy(&tools, &log, "exit 0");
        mutate(&base, &project);
        let (_, document) = next_verify(&project, &base, &tools);
        let limitations = blocked_reason(&document);
        assert!(limitations.contains(reason), "{index}: {limitations}");
        assert!(calls(&log).is_empty(), "{index}: the tool never ran");
    }
    // Rows the context loader refused (directory outside the workspace) that `-p` would replay
    // for a selected source (strace: clang-tidy chdirs there): the same path, the same path with
    // `.`/`..`, another link to the selected file. A key LLVM's database parser refuses makes
    // clang-tidy drop the whole database ("Running without flags.").
    for (name, reason) in [
        (
            "refused-row",
            "clang-tidy -p would also replay the database entry 1 that ici refused \
             (directory-outside-project: compile cwd leaves workspace)",
        ),
        (
            "refused-dots",
            "clang-tidy -p would also replay the database entry 1 that ici refused \
             (directory-outside-project: compile cwd leaves workspace)",
        ),
        (
            "refused-alias",
            "clang-tidy -p would also replay the database entry 1 that ici refused \
             (directory-outside-project: compile cwd leaves workspace)",
        ),
        (
            "unknown-key",
            "clang-tidy -p could not load the selected database (entry 0: Unknown key (note)) \
             and would analyse every source without its recorded flags",
        ),
    ] {
        let work = tempfile::tempdir().unwrap();
        let base = work.path().canonicalize().unwrap();
        let case = &oracle["cases"][name];
        let project = workspace(&base, case, case["config"].as_str().unwrap());
        let tools = base.join("tools");
        let log = base.join("calls");
        recording_tidy(&tools, &log, "exit 0");
        let (_, document) = next_verify(&project, &base, &tools);
        assert!(
            blocked_reason(&document).contains(reason),
            "{name}: {document}"
        );
        assert!(calls(&log).is_empty(), "{name}");
    }
    // Negative control: a refused row of a source outside the batch is never replayed for it;
    // the batch runs (frozen: the same argv, SUCCEEDED).
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let case = &oracle["cases"]["refused-other"];
    let project = workspace(&base, case, case["config"].as_str().unwrap());
    let tools = base.join("tools");
    let log = base.join("calls");
    recording_tidy(&tools, &log, "exit 0");
    let (_, document) = next_verify(&project, &base, &tools);
    assert_eq!(tidy(&document)["state"], "SUCCEEDED", "{document}");
    let recorded = calls(&log);
    assert_eq!(recorded.len(), 1);
    assert_eq!(
        recorded[0].argv[1..],
        [
            "-p".to_owned(),
            project.display().to_string(),
            project.join("app/a.cpp").display().to_string()
        ]
    );
    // A database under another name: `-p` would read `<dir>/compile_commands.json` instead.
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let project = workspace(&base, &oracle["cases"]["config"], config);
    fs::rename(
        project.join("compile_commands.json"),
        project.join("recorded.json"),
    )
    .unwrap();
    let checks: String = ici_domain::builtin_catalog()
        .checks
        .iter()
        .filter(|check| check.language == "cpp")
        .map(|check| {
            format!(
                "[checks.\"{}\"]\nenabled={}\n",
                check.id,
                matches!(check.id.as_str(), "cpp.compile" | "cpp.tidy")
            )
        })
        .collect();
    fs::write(
        project.join("ici.toml"),
        format!(
            "schema_version=2\n{checks}[project]\ncompile_database='recorded.json'\n\
             [[components]]\nid='app'\nroot='app'\nlanguages=['cpp']\n"
        ),
    )
    .unwrap();
    let tools = base.join("tools");
    let log = base.join("calls");
    recording_tidy(&tools, &log, "exit 0");
    let (_, document) = next_verify(&project, &base, &tools);
    assert!(
        blocked_reason(&document).contains(
            "clang-tidy -p reads only <directory>/compile_commands.json; the selected database recorded.json is another file"
        ),
        "{document}"
    );
    assert!(calls(&log).is_empty());
    // Negative control: the same workspace without the mutation runs once.
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let project = workspace(&base, &oracle["cases"]["config"], config);
    let tools = base.join("tools");
    let log = base.join("calls");
    recording_tidy(&tools, &log, "exit 0");
    let (_, document) = next_verify(&project, &base, &tools);
    assert_eq!(tidy(&document)["state"], "SUCCEEDED", "{document}");
    assert_eq!(calls(&log).len(), 1);
}

/// The frozen batch captures replayed natively with the same real clang-tidy 21: the same argv
/// and cwd, the same raw transcript, and frozen's verdicts where frozen read the transcript; the
/// R3 summary correction and the identical-primary merge where frozen refused it. Only
/// cpp.compile and cpp.tidy are selected: advisory tidy leaves the gate PASS whatever it found
/// or failed, like frozen's; a required tidy's failure is INCOMPLETE and its findings FAIL.
#[test]
#[ignore = "explicit actual clang-tidy 21 (ICI_TEST_CLANG_TIDY, ICI_TEST_CLANG_LIBRARY_PATH)"]
fn real_next_tidy_batches_replay_the_frozen_captures() {
    let oracle = oracle();
    let real =
        std::env::var("ICI_TEST_CLANG_TIDY").expect("ICI_TEST_CLANG_TIDY names clang-tidy 21");
    let libraries = std::env::var("ICI_TEST_CLANG_LIBRARY_PATH").unwrap_or_default();
    for name in [
        "required-broken",
        "required-default",
        "required-shared-header",
        "clean",
        "default",
        "config",
        "nested-config",
        "shared-header",
        "distinct",
        "repeat-after-clean",
        "broken",
        "stdlib",
        "flags-cwd",
        "two-rows",
        "two-spellings",
        "identical-rows",
        "refused-other",
        "single-dir",
        "two-candidates",
        "zero-candidates",
    ] {
        let case = &oracle["cases"][name];
        let work = tempfile::tempdir().unwrap();
        let base = work.path().canonicalize().unwrap();
        let project = workspace(&base, case, case["config"].as_str().unwrap());
        let tools = base.join("tools");
        let log = base.join("calls");
        recording_tidy(
            &tools,
            &log,
            &format!(
                "LD_LIBRARY_PATH='{libraries}' '{real}' \"$@\" >\"$d/stdout\" 2>\"$d/stderr\"\n\
                 rc=$?\ncat \"$d/stdout\"\ncat \"$d/stderr\" >&2\nexit $rc"
            ),
        );
        let (code, document) = next_verify(&project, &base, &tools);
        let frozen = &case["frozen"];
        let required = name.starts_with("required-");
        let corrected = frozen["tidy"]["state"] == "FAILED" && !name.ends_with("broken");
        // A relative location naming no captured file or several under the batch's compile
        // directories is unattributable (ruling 1): FAILED with no finding.
        let unattributable = match name {
            "two-candidates" => Some("relative clang-tidy location ./h.h names 2 captured files"),
            "zero-candidates" => {
                Some("relative clang-tidy location ./../../../elsewhere/z.h names 0 captured files")
            }
            _ => None,
        };
        if let Some(reason) = unattributable {
            assert_eq!(frozen["tidy"]["state"], "FAILED", "{name}");
            assert_eq!(tidy(&document)["state"], "FAILED", "{name}: {document}");
            assert!(document.to_string().contains(reason), "{name}: {document}");
            assert_eq!(code, 0, "{name}: {document}");
            assert_eq!(document["gate"]["selected"], "PASS", "{name}");
        } else if !corrected {
            // Frozen's own verdict: exit, gate and its reasons.
            assert_eq!(code, frozen["exit"], "{name}: {document}");
            assert_eq!(
                document["gate"]["selected"], frozen["gate"]["selected"],
                "{name}"
            );
            assert_eq!(
                document["gate"]["has_violations"],
                frozen["gate"]["has_violations"]
            );
            assert_eq!(
                document["gate"]["reasons"], frozen["gate"]["reasons"],
                "{name}"
            );
        } else if required {
            // A read batch's finding is a violation of the required check (frozen: INCOMPLETE on
            // the refused summary).
            assert_eq!(frozen["gate"]["selected"], "INCOMPLETE");
            assert_eq!(code, 1, "{name}: {document}");
            assert_eq!(document["gate"]["selected"], "FAIL", "{name}");
            assert_eq!(
                document["gate"]["reasons"],
                serde_json::json!(["1 violation(s) found"])
            );
        } else {
            // Advisory tidy: frozen's FAILED and native's findings both leave the gate PASS.
            assert_eq!(frozen["gate"]["selected"], "PASS");
            assert_eq!(code, 0, "{name}: {document}");
            assert_eq!(document["gate"]["selected"], "PASS", "{name}");
            assert_eq!(document["gate"]["has_violations"], false, "{name}");
        }
        let root = project.display().to_string();
        let local = |text: &str| {
            text.replace("<root>", &root).replace(
                "<base>/bin/clang-tidy",
                &tools.join("clang-tidy").display().to_string(),
            )
        };
        let calls = calls(&log);
        let recorded = &case["calls"];
        assert_eq!(calls.len(), recorded.as_array().unwrap().len(), "{name}");
        for (call, frozen_call) in calls.iter().zip(recorded.as_array().unwrap()) {
            let argv: Vec<String> = frozen_call["argv"]
                .as_array()
                .unwrap()
                .iter()
                .map(|item| local(item.as_str().unwrap()))
                .collect();
            assert_eq!(call.argv, argv, "{name}");
            assert_eq!(
                call.cwd,
                local(frozen_call["cwd"].as_str().unwrap()),
                "{name}"
            );
            assert_eq!(
                call.stdout,
                local(frozen_call["stdout"].as_str().unwrap()),
                "{name}"
            );
            assert_eq!(
                call.stderr,
                local(frozen_call["stderr"].as_str().unwrap()),
                "{name}"
            );
        }
        let check = tidy(&document);
        assert_eq!(check["evidence_complete"], false, "{name}");
        let findings: Vec<&Value> = document["findings"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|finding| finding["task_id"] == "app.cpp.tidy")
            .collect();
        let shape = |finding: &Value| {
            serde_json::json!([
                finding["rule_id"],
                finding["primary_location"]["path"],
                finding["primary_location"]["start_line"],
                finding["primary_location"]["start_column"],
                finding["message"],
            ])
        };
        if name.ends_with("broken") {
            assert_eq!(check["state"], "FAILED", "{name}");
            assert!(findings.is_empty(), "{name}: {findings:?}");
            assert!(
                document["limitations"]
                    .as_array()
                    .unwrap()
                    .contains(&"app.cpp.tidy failed with exit code 1".into()),
                "{name}: {document}"
            );
            assert_eq!(frozen["tidy"]["state"], "FAILED");
            assert_eq!(frozen["findings"], serde_json::json!([]));
            continue;
        }
        if unattributable.is_some() {
            assert!(findings.is_empty(), "{name}: {findings:?}");
            continue;
        }
        assert_eq!(check["state"], "SUCCEEDED", "{name}: {document}");
        if name == "single-dir" {
            // One compile directory below the workspace root: clang-tidy spells `b.cpp` relative
            // to it (this transcript); frozen anchored it at the cwd, a path that names no file.
            let frozen_paths: Vec<&Value> = frozen["findings"]
                .as_array()
                .unwrap()
                .iter()
                .map(|finding| &finding["primary_location"]["path"])
                .collect();
            assert_eq!(frozen_paths, ["b.cpp", "b.cpp"], "{name}");
            assert!(!project.join("b.cpp").exists());
            assert_eq!(
                findings
                    .iter()
                    .map(|finding| shape(finding))
                    .collect::<Vec<_>>(),
                [
                    serde_json::json!([DIVIDE, "app/sub/b.cpp", 3, 26, BY_ZERO]),
                    serde_json::json!([
                        "clang_tidy.clang-diagnostic-division-by-zero",
                        "app/sub/b.cpp",
                        3,
                        26,
                        "warning: remainder by zero is undefined"
                    ]),
                ],
                "{name}"
            );
            continue;
        }
        if frozen["tidy"]["state"] == "SUCCEEDED" {
            // The whole finding objects: native adds only the notes (frozen next findings carry
            // none, cpp28 ruling "keep the notes") and the advisory tag of a non-required check.
            let native: Vec<Value> = findings
                .iter()
                .map(|finding| {
                    let mut finding = (*finding).clone();
                    let object = finding.as_object_mut().unwrap();
                    let tags = object.remove("tags");
                    assert_eq!(
                        tags,
                        (!required).then(|| serde_json::json!(["advisory"])),
                        "{name}"
                    );
                    object.remove("related_locations");
                    finding
                })
                .collect();
            assert_eq!(
                native,
                frozen["findings"].as_array().unwrap().clone(),
                "{name}"
            );
            // Frozen read the transcript: the same findings, fingerprints included.
            let identity = |finding: &Value| {
                let mut shape = shape(finding);
                for key in [
                    "fingerprint",
                    "analysis_unit_id",
                    "native_rule_id",
                    "severity",
                    "confidence",
                    "evidence",
                    "provider",
                ] {
                    shape.as_array_mut().unwrap().push(finding[key].clone());
                }
                shape
            };
            assert_eq!(
                findings
                    .iter()
                    .map(|finding| identity(finding))
                    .collect::<Vec<_>>(),
                frozen["findings"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(identity)
                    .collect::<Vec<_>>(),
                "{name}"
            );
            continue;
        }
        // Frozen refused the repeated summary (or the progress lines); native reads it.
        assert_eq!(frozen["tidy"]["state"], "FAILED", "{name}");
        let expected = match name.trim_start_matches("required-") {
            "nested-config" => vec![(DIVIDE, "app/sub/b.cpp", 1, 53, BY_ZERO)],
            "shared-header" => vec![(DIVIDE, "app/h.h", 1, 46, BY_ZERO)],
            "distinct" => vec![
                (DIVIDE, "app/a.cpp", 1, 36, BY_ZERO),
                (DIVIDE, "app/b.cpp", 2, 36, BY_ZERO),
            ],
            "repeat-after-clean" => vec![(DIVIDE, "app/b.cpp", 1, 36, BY_ZERO)],
            "stdlib" => vec![],
            "flags-cwd" => vec![
                (DIVIDE, "app/a.cpp", 2, 37, BY_ZERO),
                (DIVIDE, "app/sub/b.cpp", 3, 26, BY_ZERO),
                (
                    "clang_tidy.clang-diagnostic-division-by-zero",
                    "app/sub/b.cpp",
                    3,
                    26,
                    "warning: remainder by zero is undefined",
                ),
            ],
            "two-rows" => vec![
                (DIVIDE, "app/m.cpp", 2, 37, BY_ZERO),
                (DIVIDE, "app/m.cpp", 5, 37, BY_ZERO),
            ],
            "two-spellings" | "identical-rows" => vec![(DIVIDE, "app/a.cpp", 1, 36, BY_ZERO)],
            other => panic!("{other}"),
        };
        assert_eq!(
            findings
                .iter()
                .map(|finding| shape(finding))
                .collect::<Vec<_>>(),
            expected
                .iter()
                .map(|row| serde_json::json!([row.0, row.1, row.2, row.3, row.4]))
                .collect::<Vec<_>>(),
            "{name}"
        );
        // The same primary as a frozen-read case is the same finding identity.
        let tie = |case: &str, path: &str| {
            let frozen = oracle["cases"][case]["frozen"]["findings"]
                .as_array()
                .unwrap()
                .iter()
                .find(|finding| {
                    finding["primary_location"]["path"] == path && finding["rule_id"] == DIVIDE
                })
                .unwrap()["fingerprint"]
                .clone();
            let native = findings
                .iter()
                .find(|finding| {
                    finding["primary_location"]["path"] == path && finding["rule_id"] == DIVIDE
                })
                .unwrap()["fingerprint"]
                .clone();
            assert_eq!(native, frozen, "{name} {path}");
        };
        match name {
            "distinct" | "identical-rows" => tie("default", "app/a.cpp"),
            "flags-cwd" => tie("flags-txt", "app/a.cpp"),
            _ => {}
        }
        if name.ends_with("shared-header") {
            assert_eq!(
                findings[0]["related_locations"],
                serde_json::json!([
                    {"path": "app/a.cpp", "start_line": 2, "start_column": 18, "label": "note: Calling 'div0'"},
                    {"path": "app/h.h", "start_line": 1, "start_column": 26, "label": "note: 'z' initialized to 0"},
                    {"path": "app/h.h", "start_line": 1, "start_column": 46, "label": "note: Division by zero"},
                ]),
                "{name}"
            );
        }
    }
}

const DIVIDE: &str = "clang_tidy.clang-analyzer-core.DivideZero";
const BY_ZERO: &str = "warning: Division by zero";

fn sparse(path: &Path, length: u64) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::File::create(path).unwrap().set_len(length).unwrap();
}

/// The batch reads every selected source and any header they include: one input the snapshot
/// could not read refuses the whole process (no partial batch), and the tool never runs.
#[test]
fn an_unread_input_refuses_the_whole_batch() {
    let oracle = oracle();
    let case = &oracle["cases"]["distinct"];
    for (unread, reason) in [
        (
            "app/big.hpp",
            "app/big.hpp was not analyzed: it exceeds the 16 MiB per-file input limit; a selected source may include it",
        ),
        (
            "app/b.cpp",
            "app/b.cpp was not analyzed: it exceeds the 16 MiB per-file input limit",
        ),
    ] {
        let work = tempfile::tempdir().unwrap();
        let base = work.path().canonicalize().unwrap();
        let project = workspace(&base, case, case["config"].as_str().unwrap());
        sparse(&project.join(unread), 16 * 1024 * 1024 + 1);
        let tools = base.join("tools");
        let log = base.join("calls");
        recording_tidy(&tools, &log, "exit 0");
        let (_, document) = next_verify(&project, &base, &tools);
        assert!(
            blocked_reason(&document).contains(reason),
            "{unread}: {document}"
        );
        assert!(calls(&log).is_empty(), "{unread}");
    }
}
