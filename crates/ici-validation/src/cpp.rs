use crate::{
    ArtifactPattern, StepPrerequisite, ValidationError, ValidationKind, ValidationPlan, planning,
    python::{finding, pair},
};
use ici_domain::{AnalysisOutput, InspectionTarget, TaskKind};
use ici_execution::TaskOutcome;
use regex::Regex;
use serde_json::json;
use std::{collections::BTreeMap, path::Path};

pub(crate) fn plan(plan: &mut ValidationPlan) -> Result<(), ValidationError> {
    let request = &plan.context;
    match request.kind {
        ValidationKind::Ctest => {
            let directory =
                request.options.build_dir.as_ref().ok_or_else(|| {
                    ValidationError("ctest requires declared build directory".into())
                })?;
            if !directory.is_absolute() {
                return Err(ValidationError(
                    "ctest build directory must be absolute".into(),
                ));
            }
            let mut args = vec![
                "--test-dir".into(),
                directory.to_string_lossy().into_owned(),
                "--output-on-failure".into(),
            ];
            // Frozen next `CtestProvider.plan` runs the suite without `--parallel`.
            if !request.options.frozen_next_suite {
                args.extend(["--parallel".into(), "2".into()]);
            }
            let mut test = planning::task(request, "suite", args);
            test.id = crate::next_suite::step_id(request);
            test.kind = TaskKind::Test;
            plan.tasks.push(test);
        }
        ValidationKind::Qtest | ValidationKind::MakeCheck => {
            let mut test = planning::task(request, "suite", arguments(plan)?);
            test.id = crate::next_suite::step_id(request);
            test.kind = TaskKind::Test;
            plan.tasks.push(test);
        }
        ValidationKind::CppExecutable => crate::standalone::plan(plan)?,
        ValidationKind::Sanitizer | ValidationKind::ThreadSanitizer => {
            if request.options.frozen_next_sanitizer {
                return crate::next_sanitizer::plan(plan);
            }
            if request.options.sanitizer_suite_kind.is_some() {
                return crate::sanitizer_suite::plan(plan);
            }
            let readelf = request
                .options
                .auxiliary_tools
                .get("readelf")
                .ok_or_else(|| {
                    ValidationError(
                        "sanitizer verification requires an explicit readelf identity".into(),
                    )
                })?;
            let mut probe = planning::task(request, "instrumentation", vec![]);
            probe.argv = vec![
                readelf.path.to_string_lossy().into_owned(),
                "--symbols".into(),
                "--wide".into(),
                request.tool.path.to_string_lossy().into_owned(),
            ];
            probe.tool_digest = Some(readelf.digest.clone());
            probe.kind = TaskKind::Probe;
            probe.timeout_seconds = Some(30.0);
            probe.output_limit_bytes = Some(8 * 1024 * 1024);
            probe
                .input_refs
                .push(request.tool.path.to_string_lossy().into_owned());
            let mut test = planning::task(request, "suite", arguments(plan)?);
            test.kind = TaskKind::Test;
            test.output_limit_bytes = Some(1024 * 1024);
            let settings = if request.kind == ValidationKind::ThreadSanitizer {
                vec![(
                    "TSAN_OPTIONS",
                    if request.options.legacy_tsan_halt_on_error {
                        "halt_on_error=1"
                    } else {
                        "halt_on_error=0"
                    },
                )]
            } else {
                vec![
                    ("ASAN_OPTIONS", "detect_leaks=1"),
                    ("UBSAN_OPTIONS", "halt_on_error=1"),
                ]
            };
            for (key, extra) in settings {
                let inherited = request
                    .options
                    .environment
                    .get(key)
                    .cloned()
                    .or_else(|| std::env::var(key).ok())
                    .unwrap_or_default();
                test.env_overlay.insert(
                    key.into(),
                    if inherited.is_empty() {
                        extra.into()
                    } else {
                        format!("{inherited}:{extra}")
                    },
                );
            }
            plan.prerequisites.insert(
                test.id.clone(),
                vec![StepPrerequisite {
                    task_id: probe.id.clone(),
                    accepted_exits: vec![0],
                    require_test_evidence: false,
                }],
            );
            plan.tasks.extend([probe, test]);
            plan.limitations.push("instrumentation symbols prove a linked sanitizer runtime; they do not prove every translation unit was instrumented".into());
        }
        ValidationKind::Gcov if request.options.frozen_next_gcov => {
            // Frozen next `GcovProvider.plan`: one ANALYZE read of every note after the declared
            // suites (the check-level edge to cpp.test orders it), reports written in the owned
            // run directory under the 900 s bound. Notes keep native's `-x` report naming
            // (unique like `-p` without overflowing NAME_MAX in deep checkouts).
            if request.targets.is_empty()
                || request
                    .targets
                    .iter()
                    .any(|target| !target.ends_with(".gcno"))
            {
                return Err(ValidationError(
                    "gcov notes must be explicit .gcno artifacts".into(),
                ));
            }
            let mut args = vec!["--json-format".into(), "-b".into(), "-x".into()];
            args.extend(
                request
                    .targets
                    .iter()
                    .map(|path| request.root.join(path).to_string_lossy().into_owned()),
            );
            let mut report = planning::task(request, "report", args);
            report.cwd = request.owned_run_dir.to_string_lossy().into_owned();
            report.cacheable = false;
            report.timeout_seconds = Some(900.0);
            report.output_limit_bytes = Some(1_000_000);
            report.output_specs.push("*.gcov.json.gz".into());
            plan.artifact_patterns.push(ArtifactPattern {
                id_prefix: "gcov-json".into(),
                suffix: ".gcov.json.gz".into(),
                producer_task_id: report.id.clone(),
                max_files: 4096,
                max_bytes_each: 8 * 1024 * 1024,
                max_bytes_total: 64 * 1024 * 1024,
            });
            plan.tasks.push(report);
        }
        ValidationKind::Gcov => {
            if request.targets.is_empty() || request.source_files.is_empty() {
                return Err(ValidationError(
                    "gcov requires explicit notes and source scopes".into(),
                ));
            }
            // `-x` names each report `<notes>##<md5 of the full path>`: unique like `-p`, but
            // never longer than NAME_MAX. `-p` mangled the whole absolute notes path into the
            // report name, so gcov exited 6 ("Cannot open JSON output file") once a project
            // lived about 100 characters deep.
            let mut args = vec!["--json-format".into(), "-b".into(), "-x".into()];
            args.extend(
                request
                    .targets
                    .iter()
                    .map(|path| request.root.join(path).to_string_lossy().into_owned()),
            );
            let mut report = planning::task(request, "report", args);
            report.cwd = request.owned_run_dir.to_string_lossy().into_owned();
            // Frozen `GcovProvider.plan` is an ANALYZE task: gcov only reads the counters and
            // writes its reports inside the owned run directory, so it is not a prepare task
            // (the fast profile keeps it) and stays uncached like every validation step.
            report.kind = TaskKind::Analyze;
            report.cacheable = false;
            report.output_specs.push("*.gcov.json.gz".into());
            request
                .options
                .shared_test_task_id
                .as_ref()
                .ok_or_else(|| {
                    ValidationError(
                        "gcov requires a completed instrumented suite from this run".into(),
                    )
                })?;
            if !matches!(
                request.options.shared_test_kind,
                Some(
                    ValidationKind::Ctest
                        | ValidationKind::Qtest
                        | ValidationKind::MakeCheck
                        | ValidationKind::CppExecutable
                )
            ) {
                return Err(ValidationError(
                    "gcov shared suite must declare CTest, QTest, make check or plain C++ executable kind"
                        .into(),
                ));
            }
            if request
                .targets
                .iter()
                .any(|target| !target.ends_with(".gcno"))
            {
                return Err(ValidationError(
                    "gcov notes must be explicit .gcno artifacts".into(),
                ));
            }
            plan.prerequisites.insert(
                report.id.clone(),
                crate::gcov::suite_ids(plan)?
                    .into_iter()
                    .map(|id| StepPrerequisite {
                        task_id: id,
                        accepted_exits: (0..=255).collect(),
                        require_test_evidence: false,
                    })
                    .collect(),
            );
            plan.artifact_patterns.push(ArtifactPattern {
                id_prefix: "gcov-json".into(),
                suffix: ".gcov.json.gz".into(),
                producer_task_id: report.id.clone(),
                max_files: 4096,
                max_bytes_each: 8 * 1024 * 1024,
                max_bytes_total: 64 * 1024 * 1024,
            });
            plan.tasks.push(report);
            crate::gcov_format::configure(plan)?;
        }
        ValidationKind::BinaryCompatibility if request.options.frozen_next_elf => {
            // Frozen next `BinaryCompatProvider.plan`: one readelf read of one artifact, run
            // from the workspace root under the provider's 60 s bound and the executor's
            // default 1,000,000-byte output bound.
            let (Some(step), [target]) = (&request.options.suite_step_id, &request.targets[..])
            else {
                return Err(ValidationError(
                    "a frozen next ELF step names exactly one artifact".into(),
                ));
            };
            let mut read = planning::task(
                request,
                "elf",
                vec![
                    "--file-header".into(),
                    "--sections".into(),
                    "--dynamic".into(),
                    "--version-info".into(),
                    "--wide".into(),
                    request.root.join(target).to_string_lossy().into_owned(),
                ],
            );
            read.id.clone_from(step);
            read.timeout_seconds = Some(60.0);
            read.output_limit_bytes = Some(1_000_000);
            plan.tasks.push(read);
        }
        ValidationKind::BinaryCompatibility => {
            if request.options.max_artifacts == 0
                || request.options.max_artifacts > 4096
                || request.targets.is_empty()
                || request.targets.len() > request.options.max_artifacts
            {
                return Err(ValidationError(
                    "binary compatibility requires 1..max_artifacts explicit artifacts (bound 4096)".into(),
                ));
            }
            if request
                .targets
                .iter()
                .collect::<std::collections::BTreeSet<_>>()
                .len()
                != request.targets.len()
            {
                return Err(ValidationError(
                    "binary artifact identities are duplicated".into(),
                ));
            }
            for (index, target) in request.targets.iter().enumerate() {
                let read = planning::task(
                    request,
                    &format!("elf{index}"),
                    vec![
                        "--file-header".into(),
                        "--sections".into(),
                        "--dynamic".into(),
                        "--version-info".into(),
                        "--wide".into(),
                        request.root.join(target).to_string_lossy().into_owned(),
                    ],
                );
                plan.tasks.push(read);
            }
        }
        _ => return Err(ValidationError("unsupported C++ validation kind".into())),
    }
    Ok(())
}
fn arguments(plan: &ValidationPlan) -> Result<Vec<String>, ValidationError> {
    let request = &plan.context;
    if request.options.argv.is_empty() {
        return Ok(vec![]);
    }
    if Path::new(&request.options.argv[0]) != request.tool.path {
        return Err(ValidationError(
            "suite argv executable must match its selected tool identity".into(),
        ));
    }
    Ok(request.options.argv[1..].to_vec())
}
pub(crate) fn instrumentation(
    plan: &ValidationPlan,
    outcome: &TaskOutcome,
) -> Result<(), ValidationError> {
    if !planning::completed(outcome) || outcome.exit_code != Some(0) {
        return Err(ValidationError(
            "instrumentation readelf probe failed".into(),
        ));
    }
    let text = std::str::from_utf8(&outcome.stdout)
        .map_err(|_| ValidationError("instrumentation output is not UTF-8".into()))?;
    if !text.contains("Symbol table '") {
        return Err(ValidationError(
            "instrumentation probe did not return a symbol table".into(),
        ));
    }
    let marker = if plan.kind == ValidationKind::ThreadSanitizer {
        r"(?m)\b__tsan_init(?:@[^\s]+)?\s*$"
    } else {
        r"(?m)\b(?:__asan_init|__ubsan_handle_[A-Za-z0-9_]+)(?:@[^\s]+)?\s*$"
    };
    if !Regex::new(marker).expect("constant regex").is_match(text) {
        return Err(ValidationError(
            "declared sanitizer variant lacks its native runtime symbols".into(),
        ));
    }
    Ok(())
}
pub(crate) fn completed_test(
    plan: &ValidationPlan,
    task_id: &str,
    outcome: &TaskOutcome,
) -> Result<(), ValidationError> {
    test_evidence(plan, task_id, outcome).map(|_| ())
}
pub(crate) fn test_evidence(
    plan: &ValidationPlan,
    task_id: &str,
    outcome: &TaskOutcome,
) -> Result<AnalysisOutput, ValidationError> {
    let mut suite_plan = plan.clone();
    suite_plan.kind = plan
        .context
        .options
        .shared_test_kind
        .ok_or_else(|| ValidationError("shared C++ suite kind absent".into()))?;
    suite_plan.context.kind = suite_plan.kind;
    if let Some(source) = plan.context.options.shared_cpp_test_sources.get(task_id) {
        suite_plan.context.options.executable_test_source = Some(source.clone());
        if !suite_plan.context.source_files.contains(source) {
            suite_plan.context.source_files.push(source.clone());
        }
    }
    if let Some(task) = task_id.strip_suffix(".suite") {
        suite_plan.context.task_id = task.into();
        suite_plan.context.options.suite_step_id = None;
    } else if plan.context.options.frozen_next_suite {
        // A frozen next suite step (`<task>.<build>-ctest`) is its own identity.
        suite_plan.context.task_id = task_id.into();
        suite_plan.context.options.suite_step_id = Some(task_id.into());
    } else {
        return Err(ValidationError(
            "shared C++ suite step id must end with .suite".into(),
        ));
    }
    let outcomes = BTreeMap::from([(task_id.to_string(), outcome.clone())]);
    let mut output = AnalysisOutput::new(&suite_plan.context.task_id, suite_plan.kind.provider());
    match suite_plan.kind {
        ValidationKind::Ctest => observe_ctest(&suite_plan, &outcomes, &mut output),
        ValidationKind::Qtest => observe_qtest(&suite_plan, &outcomes, &mut output),
        ValidationKind::MakeCheck => observe_make_check(&suite_plan, &outcomes, &mut output),
        ValidationKind::CppExecutable => {
            crate::standalone::observe(&suite_plan, &outcomes, &mut output)
        }
        _ => Err(ValidationError("unsupported C++ test evidence kind".into())),
    }?;
    Ok(output)
}
pub(crate) fn collective_test_evidence(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<AnalysisOutput, ValidationError> {
    let mut output = AnalysisOutput::new(&plan.context.task_id, "cpp-executable");
    let mut nodes = serde_json::Map::new();
    let mut passed = 0u64;
    let mut total = 0u64;
    for id in plan.context.options.shared_cpp_test_sources.keys() {
        let observed = test_evidence(
            plan,
            id,
            outcomes.get(id).ok_or_else(|| {
                ValidationError(format!("C++ physical suite outcome {id} is absent"))
            })?,
        )?;
        let (yes, count) = observed
            .observation
            .measurements
            .iter()
            .find(|metric| metric.name.ends_with(".cases"))
            .and_then(|metric| metric.numerator.zip(metric.denominator))
            .ok_or_else(|| ValidationError("C++ physical suite raw test counts absent".into()))?;
        passed += yes;
        total += count;
        for (node, state) in observed.extra["test_outcomes"]
            .as_object()
            .ok_or_else(|| ValidationError("C++ physical suite identities absent".into()))?
        {
            if nodes.insert(node.clone(), state.clone()).is_some() {
                return Err(ValidationError(
                    "C++ physical suites repeat a source identity".into(),
                ));
            }
        }
        output.targets.extend(observed.targets);
        output
            .observation
            .findings
            .extend(observed.observation.findings);
    }
    if total == 0 {
        return Err(ValidationError(
            "no C++ physical suites were observed".into(),
        ));
    }
    output
        .observation
        .measurements
        .push(pair("cpp-executable.cases", passed, total));
    output.extra["test_outcomes"] = json!(nodes);
    Ok(output)
}
fn suite<'a>(
    plan: &ValidationPlan,
    outcomes: &'a BTreeMap<String, TaskOutcome>,
) -> Result<&'a TaskOutcome, ValidationError> {
    let id = crate::next_suite::step_id(&plan.context);
    let outcome = outcomes
        .get(&id)
        .ok_or_else(|| ValidationError("C++ suite step is absent".into()))?;
    if outcome.task_id != id {
        return Err(ValidationError(
            "C++ suite outcome identity mismatch".into(),
        ));
    }
    Ok(outcome)
}
pub(crate) fn observe_ctest(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let outcome = suite(plan, outcomes)?;
    let text = format!(
        "{}\n{}",
        String::from_utf8_lossy(&outcome.stdout),
        String::from_utf8_lossy(&outcome.stderr)
    );
    let cases = Regex::new(r"Test\s+#(\d+):\s+(.+?)\s*\.{3,}\s*(.+?)\s+[\d.]+\s*sec\b")
        .expect("constant regex");
    let summary =
        Regex::new(r"\d+% tests passed,\s*\d+ tests failed out of (\d+)").expect("constant regex");
    let mut passed = 0;
    let mut total = 0;
    let mut skipped = 0;
    // CTest leaves DISABLED tests out of its "out of N" line (ctest 4.2.3: Passed, Skipped,
    // Not Run and Failed count; "Not Run (Disabled)" does not).
    let mut disabled = 0;
    let mut observed = BTreeMap::new();
    // `--parallel` prints cases in completion order; the CTest index (frozen JUnit order) is the
    // stable case order.
    let mut ordered: Vec<(u64, &str, &str)> = cases
        .captures_iter(&text)
        .map(|case| {
            (
                case[1].parse().unwrap_or(u64::MAX),
                case.get(2).expect("name").as_str(),
                case.get(3).expect("verdict").as_str(),
            )
        })
        .collect();
    ordered.sort_by_key(|(index, _, _)| *index);
    let locator = Locator::new(plan);
    for (_, name, raw) in ordered {
        total += 1;
        let verdict = raw.trim_start_matches('*').trim();
        disabled += u64::from(verdict.ends_with("(Disabled)"));
        let path = locator.source(name);
        let mut target = InspectionTarget::at(&path, 1);
        target.target_name = name.into();
        target.message = verdict.into();
        if verdict == "Passed" {
            passed += 1;
            target.status = "PASS".into();
        } else if ["not run", "disabled", "skipped", "skip"]
            .iter()
            .any(|prefix| verdict.to_ascii_lowercase().starts_with(prefix))
        {
            skipped += 1;
            target.status = "SKIP".into();
        } else {
            target.status = "FAIL".into();
            let mut item = finding(
                plan,
                "ctest.failed",
                &format!("test {name}: {verdict}"),
                &path,
                1,
            );
            if plan.context.options.frozen_next_suite {
                item.fingerprint =
                    crate::next_suite::process_fingerprint(plan, "ctest", &[name, raw]);
            }
            output.observation.findings.push(item);
        }
        let node = format!("ctest:{name}");
        let status = match target.status.as_str() {
            "PASS" => "PASSED",
            "SKIP" => "SKIPPED",
            _ => "FAILED",
        };
        if observed.insert(node, status).is_some() || observed.len() > 2048 {
            return Err(ValidationError(
                "CTest repeats test identities or exceeds 2048 cases".into(),
            ));
        }
        output.targets.push(target);
    }
    output.extra["test_outcomes"] = json!(observed);
    if total > 0 {
        let mut cases = pair("ctest.cases", passed, total);
        if plan.context.options.frozen_next_suite {
            // Frozen next `ctest.cases` carries no unit.
            cases.unit.clear();
        }
        output.observation.measurements.push(cases);
    }
    output.summary = format!("{passed}/{total} CTest suites passed");
    if !planning::completed(outcome) {
        return Err(ValidationError("CTest suite did not complete".into()));
    }
    if total == 0 {
        return Err(ValidationError(if plan.context.options.frozen_next_suite {
            crate::next_suite::ctest_without_verdicts(&text).into()
        } else {
            "CTest ran no test suites".into()
        }));
    }
    if summary
        .captures(&text)
        .and_then(|capture| capture[1].parse::<u64>().ok())
        != Some(total - disabled)
    {
        return Err(ValidationError(
            "CTest terminal count does not match case evidence".into(),
        ));
    }
    if skipped > 0 {
        if !plan.context.options.ctest_not_run_measured {
            return Err(ValidationError(format!(
                "{skipped} CTest suites were not run"
            )));
        }
        output
            .observation
            .limitations
            .push(format!("{skipped} CTest suites were not run"));
    }
    // ctest exits 8 for a "Not Run" case (missing executable); the legacy view counts it a SKIP.
    if outcome.exit_code != Some(0)
        && output.observation.findings.is_empty()
        && !(plan.context.options.ctest_not_run_measured && skipped > 0)
    {
        return Err(ValidationError(
            "CTest failed without attributable test failures".into(),
        ));
    }
    Ok(())
}
/// Frozen `core/cmake.run_tests` for a build system's own test target: the transcript
/// (`stdout + stderr`) read per test binary, QtTest failures merged for qmake, and a sanitizer
/// report attributed (`_attach_sanitizer_output`). A timed-out or truncated run is incomplete.
pub(crate) fn observe_make_check(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let outcome = suite(plan, outcomes)?;
    let label = if plan.context.options.make_check_qtest {
        "make check"
    } else {
        "Make test command"
    };
    if outcome.timed_out || outcome.truncated {
        let reason = if outcome.timed_out {
            "timed out"
        } else {
            "truncated its output"
        };
        return Err(ValidationError(format!(
            "{label} {reason}; test evidence is incomplete"
        )));
    }
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&outcome.stdout),
        String::from_utf8_lossy(&outcome.stderr)
    );
    let code = outcome.exit_code.unwrap_or(-1);
    let cases = if plan.context.options.make_check_qtest {
        crate::make_check::qmake_results(&text, code)
    } else {
        crate::make_check::parse_make_check_stdout(&text, code)
    };
    let cases = crate::make_check::attach_sanitizer_output(cases, &text);
    let (mut passed, mut skipped) = (0u64, 0u64);
    let mut observed = BTreeMap::new();
    let mut rows = vec![];
    let locator = Locator::new(plan);
    for case in &cases {
        let path = locator.source(&case.name);
        let mut target = InspectionTarget::at(&path, 1);
        target.target_name = case.name.clone();
        target.message = case.message.clone();
        let status = if case.passed {
            passed += 1;
            target.status = "PASS".into();
            "PASSED"
        } else if !case.executed {
            skipped += 1;
            target.status = "SKIP".into();
            "SKIPPED"
        } else {
            target.status = "FAIL".into();
            output.observation.findings.push(finding(
                plan,
                "make-check.failed",
                &format!("test {}: {}", case.name, case.message),
                &path,
                1,
            ));
            "FAILED"
        };
        if observed
            .insert(format!("make-check:{}", case.name), status)
            .is_some()
            || observed.len() > 2048
        {
            return Err(ValidationError(
                "make check repeats test identities or exceeds 2048 binaries".into(),
            ));
        }
        rows.push(
            json!({"name":case.name,"passed":case.passed,"executed":case.executed,
            "message":case.message,"diagnostic_output":case.diagnostic_output,
            "diagnostic_output_truncated":case.diagnostic_output_truncated}),
        );
        output.targets.push(target);
    }
    let total = cases.len() as u64;
    output.extra["test_outcomes"] = json!(observed);
    output.extra["make_check_cases"] = json!(rows);
    if total > 0 {
        output
            .observation
            .measurements
            .push(pair("make-check.cases", passed, total));
    }
    output.summary = format!("{passed}/{total} {label} tests passed");
    if !planning::completed(outcome) && outcome.exit_code.is_none() {
        return Err(ValidationError(format!("{label} did not complete")));
    }
    if total == 0 {
        return Err(ValidationError(format!(
            "{label} reported no test binaries"
        )));
    }
    if skipped > 0 {
        output
            .observation
            .limitations
            .push(format!("{skipped} {label} tests were not executed"));
    }
    Ok(())
}
pub(crate) fn observe_qtest(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let outcome = suite(plan, outcomes)?;
    let text = format!(
        "{}\n{}",
        String::from_utf8_lossy(&outcome.stdout),
        String::from_utf8_lossy(&outcome.stderr)
    );
    let cases = Regex::new(r"^(PASS|FAIL!|XFAIL|XPASS|SKIP)\s*:\s*(\S+)").expect("constant regex");
    let location = Regex::new(r"Loc:\s*\[(.+)\(([1-9]\d*)\)\]").expect("constant regex");
    let totals = Regex::new(r"Totals:\s*(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped")
        .expect("constant regex");
    let (mut passed, mut failed, mut skipped) = (0u64, 0u64, 0u64);
    let mut last_failure = None;
    let mut observed = BTreeMap::new();
    let mut expected_failures = std::collections::BTreeSet::new();
    let locator = Locator::new(plan);
    for line in text.lines() {
        if let Some(case) = cases.captures(line) {
            let path = locator.source(&case[2]);
            let mut target = InspectionTarget::at(&path, 1);
            target.target_name = case[2].into();
            target.message = case[1].into();
            if &case[1] == "XFAIL" {
                // Qt emits XFAIL as an assertion annotation, followed by one terminal case row.
                expected_failures.insert(format!("qtest:{}", &case[2]));
                target.status = "SKIP".into();
                output.targets.push(target);
                last_failure = None;
                continue;
            }
            if &case[1] == "PASS" {
                passed += 1;
                target.status = "PASS".into();
                last_failure = None;
            } else if &case[1] == "SKIP" {
                skipped += 1;
                target.status = "SKIP".into();
                last_failure = None;
            } else {
                failed += 1;
                target.status = "FAIL".into();
                let index = output.observation.findings.len();
                let mut item = finding(
                    plan,
                    &format!(
                        "qtest.{}",
                        case[1].trim_end_matches('!').to_ascii_lowercase()
                    ),
                    // Frozen `f"{node} {verdict.rstrip('!').lower()}"`.
                    &format!(
                        "{} {}",
                        &case[2],
                        case[1].trim_end_matches('!').to_ascii_lowercase()
                    ),
                    &path,
                    1,
                );
                if plan.context.options.frozen_next_suite {
                    item.fingerprint = crate::next_suite::process_fingerprint(
                        plan,
                        "qtest",
                        &[&case[2], &case[1]],
                    );
                }
                output.observation.findings.push(item);
                last_failure = Some((index, output.targets.len()));
            }
            let status = match &case[1] {
                "PASS" if expected_failures.contains(&format!("qtest:{}", &case[2])) => "XFAIL",
                "PASS" => "PASSED",
                "FAIL!" => "FAILED",
                "SKIP" => "SKIPPED",
                "XFAIL" => "XFAIL",
                _ => "XPASS",
            };
            if observed
                .insert(format!("qtest:{}", &case[2]), status)
                .is_some()
                || observed.len() > 2048
            {
                return Err(ValidationError(
                    "QTest repeats test identities or exceeds 2048 cases".into(),
                ));
            }
            output.targets.push(target);
        } else if let (Some(capture), Some((finding_index, target_index))) =
            (location.captures(line), last_failure)
        {
            let path = planning::relative(&capture[1], &plan.context.root, &plan.context.cwd)?;
            let line = capture[2]
                .parse::<u32>()
                .map_err(|_| ValidationError("QTest source line overflow".into()))?;
            output.observation.findings[finding_index].primary_location =
                ici_domain::SourceSpan::line(&path, line);
            output.targets[target_index].file_path = path;
            output.targets[target_index].start_line = line;
        }
    }
    output.extra["test_outcomes"] = json!(observed);
    if expected_failures
        .iter()
        .any(|node| !observed.contains_key(node))
    {
        return Err(ValidationError(
            "QTest expected-failure annotation has no terminal case evidence".into(),
        ));
    }
    let total = passed + failed + skipped;
    if total > 0 {
        let mut cases = pair("qtest.cases", passed, total);
        if plan.context.options.frozen_next_suite {
            // Frozen next `qtest.cases` carries no unit.
            cases.unit.clear();
        }
        output.observation.measurements.push(cases);
    }
    output.summary = format!("{passed}/{total} QTest cases passed");
    if !planning::completed(outcome) {
        return Err(ValidationError("QTest suite did not complete".into()));
    }
    if total == 0 {
        return Err(ValidationError(if plan.context.options.frozen_next_suite {
            crate::next_suite::qtest_without_verdicts(&text).into()
        } else {
            "QTest binary ran no cases".into()
        }));
    }
    let summary = totals
        .captures(&text)
        .ok_or_else(|| ValidationError("QTest terminal totals are absent".into()))?;
    let parsed = (
        summary[1].parse::<u64>().ok(),
        summary[2].parse::<u64>().ok(),
        summary[3].parse::<u64>().ok(),
    );
    if parsed != (Some(passed), Some(failed), Some(skipped)) {
        return Err(ValidationError(
            "QTest terminal totals disagree with case evidence".into(),
        ));
    }
    if passed == 0 && failed == 0 {
        return Err(ValidationError(
            "all QTest cases skipped; nothing verified".into(),
        ));
    }
    if outcome.exit_code != Some(0) && failed == 0 {
        return Err(ValidationError(
            "QTest exit status has no attributable failed cases".into(),
        ));
    }
    Ok(())
}
pub(crate) fn source(plan: &ValidationPlan, name: &str) -> String {
    Locator::new(plan).source(name)
}

/// Test-case locations of one observed suite run: the frozen next-suite `.cpp` walk is made
/// once per run and shared by every case line.
pub(crate) struct Locator<'a> {
    plan: &'a ValidationPlan,
    suite: crate::next_suite::SuiteSources<'a>,
}

impl<'a> Locator<'a> {
    pub(crate) fn new(plan: &'a ValidationPlan) -> Self {
        Self {
            plan,
            suite: crate::next_suite::SuiteSources::new(&plan.context.cwd),
        }
    }

    pub(crate) fn source(&self, name: &str) -> String {
        located(self.plan, &self.suite, name)
    }
}

fn located(plan: &ValidationPlan, suite: &crate::next_suite::SuiteSources, name: &str) -> String {
    if let Some(source) = &plan.context.options.executable_test_source {
        return source.clone();
    }
    if plan.context.options.frozen_next_suite {
        return suite.locate(name);
    }
    let stem = name
        .split("::")
        .next()
        .unwrap_or(name)
        .rsplit('/')
        .next()
        .unwrap_or(name);
    plan.context
        .source_files
        .iter()
        .find(|path| {
            Path::new(path)
                .file_stem()
                .and_then(|s| s.to_str())
                .is_some_and(|name| name.contains(stem))
        })
        .cloned()
        .or_else(|| plan.context.targets.first().cloned())
        .unwrap_or_else(|| "test-suite".into())
}
pub(crate) fn observe_elf(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    if plan.context.options.frozen_next_elf {
        return crate::next_elf::observe(plan, outcomes, output);
    }
    let mut facts = BTreeMap::new();
    for (index, path) in plan.context.targets.iter().enumerate() {
        if let Some(header) = plan.artifact_headers.get(path)
            && header.magic != b"\x7fELF"
        {
            let mut target = InspectionTarget::at(path, 1);
            target.target_name = "ELF/ABI artifact".into();
            target.status = if plan.context.options.allow_non_elf {
                "SKIP"
            } else {
                "FAIL"
            }
            .into();
            target.message = "selected artifact is not ELF".into();
            output.targets.push(target);
            if !plan.context.options.allow_non_elf {
                let mut item = finding(
                    plan,
                    "ici.binary.non-elf",
                    "Selected binary manifest artifact is not an ELF object",
                    path,
                    1,
                );
                item.native_rule_id = "elf.magic".into();
                item.category = "compatibility".into();
                item.confidence = "exact".into();
                crate::finding_details::binary(&item, output);
                output.observation.findings.push(item);
            }
            continue;
        }
        let id = format!("{}.elf{index}", plan.context.task_id);
        let outcome = outcomes
            .get(&id)
            .ok_or_else(|| ValidationError(format!("ELF step {id} absent")))?;
        if !planning::completed(outcome) || outcome.exit_code != Some(0) {
            return Err(ValidationError(format!(
                "readelf could not inspect artifact {path}"
            )));
        }
        let text = std::str::from_utf8(&outcome.stdout)
            .map_err(|_| ValidationError("readelf output is not UTF-8".into()))?;
        let parsed = ici_cpp::parse_readelf(text).map_err(|e| ValidationError(e.to_string()))?;
        let violations = ici_cpp::abi_violations(
            &parsed,
            &plan.context.options.elf_policy,
            &plan.context.options.build_roots,
        )
        .map_err(|e| ValidationError(e.to_string()))?;
        let mut target = InspectionTarget::at(path, 1);
        target.target_name = "ELF/ABI artifact".into();
        target.status = if violations.is_empty() {
            "PASS"
        } else {
            "FAIL"
        }
        .into();
        output.targets.push(target);
        for violation in violations {
            let mut item = finding(plan, &violation.rule_id, &violation.message, path, 1);
            item.native_rule_id = violation.native_rule_id;
            item.category = "compatibility".into();
            item.confidence = "exact".into();
            crate::finding_details::binary(&item, output);
            output.observation.findings.push(item);
        }
        facts.insert(path.clone(), parsed);
    }
    output.extra["artifacts"] = json!(facts);
    output.extra["artifact_headers"] = json!(
        plan.artifact_headers
            .iter()
            .map(|(path, header)| (
                path,
                json!({"is_elf":header.magic==b"\x7fELF","size":header.size})
            ))
            .collect::<BTreeMap<_, _>>()
    );
    let non_elf = plan
        .artifact_headers
        .values()
        .filter(|header| header.magic != b"\x7fELF")
        .count();
    output.summary = format!(
        "{} ELF artifacts inspected, {non_elf} non-ELF artifacts",
        facts.len()
    );
    let policy = &plan.context.options.elf_policy;
    if policy.expected_class.is_empty()
        && policy.expected_machine.is_empty()
        && policy.max_glibc.is_empty()
        && policy.max_glibcxx.is_empty()
        && policy.max_cxxabi.is_empty()
    {
        output.observation.limitations.push(
            "no deployment ABI floors were declared; loader policy and measured ABI facts only"
                .into(),
        );
    }
    Ok(())
}
