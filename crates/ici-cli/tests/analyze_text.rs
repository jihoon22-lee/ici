//! `ici analyze` human output against frozen v0.13.0 captures
//! (`tests/rust-parity/reporting/analyze-text-oracle.json`, `capture_analyze_text.py`).
use ici_cli::{commands, registry, run_text};
use ici_domain::RunResult;
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/analyze-text-oracle.json"
    ))
    .unwrap()
}

fn steps(oracle: &Value) -> impl Iterator<Item = (&str, &Value)> {
    oracle["cases"]
        .as_object()
        .unwrap()
        .iter()
        .flat_map(|(name, case)| {
            case["steps"]
                .as_array()
                .unwrap()
                .iter()
                .map(move |step| (name.as_str(), step))
        })
}

fn result_name(step: &Value) -> Option<String> {
    let argv: Vec<&str> = step["argv"]
        .as_array()?
        .iter()
        .filter_map(Value::as_str)
        .collect();
    let index = argv.iter().position(|arg| *arg == "--result")?;
    Some(argv[index + 1].to_owned())
}

/// The frozen result itself renders to the frozen verdict block: verdict, reasons, baseline,
/// run notes and the saved path, byte for byte.
#[test]
fn frozen_results_render_the_frozen_verdict_block_byte_for_byte() {
    let oracle = oracle();
    let mut rendered = 0;
    for (name, step) in steps(&oracle) {
        let (Some(result), Some(saved)) = (step.get("result"), result_name(step)) else {
            continue;
        };
        if result.is_null() || step["argv"].to_string().contains("--json") {
            continue;
        }
        let result = RunResult::from_value(result.clone()).unwrap();
        let stdout = step["stdout"].as_str().unwrap();
        let notes: Vec<String> = stdout
            .lines()
            .filter_map(|line| line.strip_prefix("  note: "))
            .map(str::to_owned)
            .collect();
        // Printed notes are the run's own limitations, in stored order.
        let mut stored = result.limitations.iter();
        for note in &notes {
            assert!(stored.any(|item| item == note), "{name}: {note}");
        }
        let expected: String = stdout.split_inclusive('\n').skip(2).collect();
        let block =
            run_text::verdict_block(&result, &[], &notes, &Path::new("<root>").join(&saved));
        assert_eq!(block, expected, "{name}");
        rendered += 1;
    }
    assert_eq!(rendered, 8, "every text-mode run with a saved result");
}

#[test]
fn verdict_block_failure_modes_cover_incompatible_baseline_reuse_and_unit_notes() {
    let mut result = RunResult::from_value(
        oracle()["cases"]["baseline_incompatible"]["steps"][1]["result"].clone(),
    )
    .unwrap();
    result.execution.reused_task_ids = vec!["a".into(), "b".into()];
    let block = run_text::verdict_block(
        &result,
        &[(
            "app.python.lint".into(),
            "prepare tasks are not cacheable".into(),
        )],
        &[],
        Path::new("/r.json"),
    );
    assert!(block.starts_with("FAIL: 2 finding(s)\n  2 violation(s) found\n  baseline: incompatible — policy changed since the baseline"));
    assert!(block.contains("\n  cache: reused 2 stored result(s)\n  note: app.python.lint: prepare tasks are not cacheable\nsaved /r.json\n"));
    // No baseline, no reuse, no notes: only the verdict and the path.
    let mut bare = result.clone();
    bare.baseline = None;
    bare.execution.reused_task_ids.clear();
    bare.gate.reasons.clear();
    assert_eq!(
        run_text::verdict_block(&bare, &[], &[], Path::new("/r.json")),
        "FAIL: 2 finding(s)\nsaved /r.json\n"
    );
    // The native preview is bounded and skips suppressed findings.
    let mut many = bare.clone();
    let template = many.findings[0].clone();
    many.findings = (0..8)
        .map(|line| {
            let mut finding = template.clone();
            finding.primary_location.start_line = line + 1;
            finding.suppression.suppressed = line == 0;
            finding
        })
        .collect();
    let tail = run_text::native_tail(&many, Path::new("/r.html"));
    assert!(
        tail.starts_with("HTML: /r.html\nfindings (5 of 7 unsuppressed, most severe first):\n")
    );
    assert_eq!(tail.lines().count(), 2 + run_text::PREVIEW_FINDINGS);
    assert!(!tail.contains(":1 "), "suppressed finding is not previewed");
    many.findings.clear();
    assert_eq!(
        run_text::native_tail(&many, Path::new("/r.html")),
        "HTML: /r.html\n"
    );
}

fn materialize(base: &Path, tree: &Value) {
    for (name, text) in tree.as_object().unwrap() {
        let path = base.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::create_dir(base.join(".git")).unwrap();
}

/// The native CLI on the same trees: announcement, stream placement, verdict line, reasons,
/// baseline line, saved path and exit codes equal the frozen run; native lines follow `saved`.
#[test]
fn native_analyze_prints_the_frozen_frame_on_the_same_projects() {
    let oracle = oracle();
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let home = tempfile::tempdir().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().canonicalize().unwrap().join("project");
        materialize(&base, &case["tree"]);
        let environment = BTreeMap::from([
            ("HOME".to_owned(), home.path().display().to_string()),
            ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ]);
        let root = base.display().to_string();
        for (index, step) in case["steps"].as_array().unwrap().iter().enumerate() {
            let argv: Vec<&str> = std::iter::once("ici")
                .chain(
                    step["argv"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|a| a.as_str().unwrap()),
                )
                .collect();
            let config = base.join("ici.toml");
            let original = fs::read_to_string(&config).ok();
            if *name == "baseline_incompatible" && index == 0 {
                let policy = oracle["baseline_incompatible_policy"].as_str().unwrap();
                fs::write(&config, original.clone().unwrap() + policy).unwrap();
            }
            let invocation = registry::parse(argv.iter().copied()).unwrap();
            let outcome = commands::dispatch(&invocation, &base, &environment);
            if let Some(original) = original {
                fs::write(&config, original).unwrap();
            }
            let frozen_out = step["stdout"].as_str().unwrap();
            let frozen_err = step["stderr"].as_str().unwrap();
            let expected_exit = step["exit_code"].as_i64().unwrap() as i32;
            let output = match outcome {
                Ok(output) => output,
                Err(error) => {
                    // Refused before scope resolution: no announcement, exit 2, as frozen.
                    assert_eq!(expected_exit, 2, "{name}: {error}");
                    assert_eq!(frozen_out, "", "{name}");
                    assert!(frozen_err.starts_with("config: "), "{name}");
                    continue;
                }
            };
            let stdout = output.stdout.replace(&root, "<root>");
            let stderr = output.stderr.replace(&root, "<root>");
            assert_eq!(output.exit_code, expected_exit, "{name}: {stdout}{stderr}");
            if argv.contains(&"--json") {
                if expected_exit == 2 {
                    // Nothing selected: the announcement, then the refusal, on stderr.
                    assert_eq!(stdout, "", "{name}");
                    let (announce, refusal) =
                        frozen_err.split_at(frozen_err.find("config: ").unwrap());
                    assert!(stderr.starts_with(announce), "{name}: {stderr}");
                    assert_eq!(&stderr[announce.len()..], refusal, "{name}");
                } else {
                    assert_eq!(stderr, frozen_err, "{name}");
                    RunResult::from_json(stdout.trim_end()).unwrap();
                }
                continue;
            }
            assert_eq!(stderr.is_empty(), frozen_err.is_empty(), "{name}: {stderr}");
            let frozen: Vec<&str> = frozen_out.lines().collect();
            let native: Vec<&str> = stdout.lines().collect();
            assert_eq!(native[..2], frozen[..2], "{name}: announcement");
            if expected_exit == 2 {
                assert_eq!(native.len(), 2, "{name}");
                assert_eq!(stderr, frozen_err, "{name}");
                continue;
            }
            assert_eq!(native[2], frozen[2], "{name}: verdict");
            let frozen_saved = frozen.iter().position(|l| l.starts_with("saved ")).unwrap();
            let native_saved = native.iter().position(|l| l.starts_with("saved ")).unwrap();
            assert_eq!(native[native_saved], frozen[frozen_saved], "{name}");
            // The page beside the default result; under .ici/rust-v1/details/<file>/<run>/ for an
            // explicit --result (root decision E2: nothing beside the user's path).
            let saved = frozen[frozen_saved].trim_start_matches("saved ");
            if saved.contains("/.ici/") {
                let page = format!("HTML: {}", saved.replace(".json", ".html"));
                assert_eq!(native[native_saved + 1], page, "{name}");
            } else {
                let file = saved.rsplit('/').next().unwrap();
                let line = native[native_saved + 1];
                assert!(
                    line.starts_with(&format!("HTML: <root>/.ici/rust-v1/details/{file}/"))
                        && line.ends_with("/page.html"),
                    "{name}: {line}"
                );
            }
            let body = |lines: &[&str]| -> Vec<String> {
                lines
                    .iter()
                    // Notes carry other areas' limitation text; native also reuses internal
                    // analyses, which frozen never caches.
                    .filter(|line| !line.starts_with("  note: ") && !line.starts_with("  cache: "))
                    .map(|line| line.to_string())
                    .collect()
            };
            let (frozen_body, native_body) = (
                body(&frozen[3..frozen_saved]),
                body(&native[3..native_saved]),
            );
            if *name == "incomplete" {
                // Blocked reasons name the provider's own unavailability text (provider area).
                let task = |line: &String| line.split(':').next().unwrap().to_owned();
                assert_eq!(
                    native_body.iter().map(task).collect::<Vec<_>>(),
                    frozen_body.iter().map(task).collect::<Vec<_>>(),
                    "{name}"
                );
            } else if *name == "baseline_incompatible" && index == 1 {
                assert_eq!(
                    native_body.len(),
                    frozen_body.len(),
                    "{name}: {native_body:?}"
                );
                assert_eq!(native_body[0], frozen_body[0]);
                // Native names the regeneration command after the frozen reason.
                assert!(
                    native_body[1].starts_with(&frozen_body[1]),
                    "{native_body:?}"
                );
            } else {
                assert_eq!(native_body, frozen_body, "{name}");
            }
            // Run notes exclude the analyses' own limitations, as frozen.
            assert!(
                native
                    .iter()
                    .all(|line| !line.contains("requires-python floor")),
                "{name}: {stdout}"
            );
        }
    }
}

/// Frozen next tool lookup wording (`ruff is not available`; frozen v0.13.0 `ici analyze` on a
/// configless Python tree with no tool on PATH, log w-rem2-missing-tool-text.log, and frozen
/// `next plan --json` / `analyze --json`, log w-runtime-missing-tool-frozen.log): the verdict
/// block, the saved gate reasons and limitations and the plan's JSON `blocked` reason all name the
/// missing tool the frozen way.
#[test]
fn a_tool_found_nowhere_is_named_like_frozen_in_the_verdict_gate_limitations_and_plan_json() {
    let home = tempfile::tempdir().unwrap();
    let empty = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path();
    fs::create_dir_all(root.join("src")).unwrap();
    fs::create_dir_all(root.join(".git")).unwrap();
    fs::write(root.join("src/core.py"), "x = 1\n").unwrap();
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.path().display().to_string()),
        ("PATH".to_owned(), empty.path().display().to_string()),
    ]);
    let output = commands::dispatch(
        &registry::parse(["ici", "analyze"]).unwrap(),
        root,
        &environment,
    )
    .unwrap();
    for line in [
        "  project.python.lint: ruff is not available\n",
        "  project.python.format: ruff is not available\n",
    ] {
        assert!(output.stdout.contains(line), "{}", output.stdout);
    }
    let saved = RunResult::from_value(
        serde_json::from_slice(&fs::read(root.join(ici_cli::DEFAULT_RESULT)).unwrap()).unwrap(),
    )
    .unwrap();
    for tool in ["lint", "format"] {
        assert!(
            saved
                .gate
                .reasons
                .contains(&format!("project.python.{tool}: ruff is not available")),
            "{:?}",
            saved.gate.reasons
        );
    }
    assert!(
        saved
            .limitations
            .contains(&"ruff is not available".to_owned()),
        "{:?}",
        saved.limitations
    );
    assert!(
        saved
            .gate
            .reasons
            .iter()
            .chain(&saved.limitations)
            .all(|reason| !reason.contains("not found")),
        "{saved:?}"
    );
    let plan = commands::dispatch(
        &registry::parse(["ici", "next", "plan", "--json"]).unwrap(),
        root,
        &environment,
    )
    .unwrap();
    let document: serde_json::Value = serde_json::from_str(&plan.stdout).unwrap();
    let blocked: BTreeMap<String, String> = document["plans"][0]["checks"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|check| {
            Some((
                check["check"].as_str()?.to_owned(),
                check["blocked"].as_str()?.to_owned(),
            ))
        })
        .collect();
    assert_eq!(blocked["python.lint"], "ruff is not available");
    assert_eq!(blocked["python.format"], "ruff is not available");
    assert_eq!(blocked["python.type"], "ty is not available");
}
