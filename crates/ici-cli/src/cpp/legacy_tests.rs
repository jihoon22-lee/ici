//! C++ test executables: per-test compile failures of descriptor-free builds, and the frozen
//! legacy `TestEngine` view of C++ test targets (`[C++] <name>` PASS/FAIL/SKIP/ERROR).
use super::*;

/// The descriptor-free test sources of a component whose own translation unit did not compile.
#[derive(Clone, Debug, Default)]
pub struct TestCompileFailures {
    /// Each such source with the bounded compiler stderr (empty for an unfinished compile).
    pub failed: BTreeMap<String, String>,
    /// Those whose compiler did not finish (`direct_test_compile_unfinished`):
    /// `{"incomplete": timed out or past the output bound, "coverage": the coverage variant,
    /// "path": the absolute source}`; a signal is "terminated".
    pub unfinished: BTreeMap<String, Value>,
}
impl TestCompileFailures {
    pub fn is_empty(&self) -> bool {
        self.failed.is_empty()
    }
}
impl From<BTreeMap<String, String>> for TestCompileFailures {
    fn from(failed: BTreeMap<String, String>) -> Self {
        Self {
            failed,
            unfinished: BTreeMap::new(),
        }
    }
}

/// Test sources of a component's prepared direct builds whose own translation unit failed to
/// compile, with the bounded compiler stderr (`direct_test_compile_failures`) and the
/// compiles that did not finish (`direct_test_compile_unfinished`).
pub fn direct_test_compile_failures(
    project: &ResolvedProject,
    component_id: &str,
    environment: &BTreeMap<String, String>,
) -> TestCompileFailures {
    let Some(component) = project
        .components
        .iter()
        .find(|component| component.id == component_id)
    else {
        return TestCompileFailures::default();
    };
    let mut builds: BTreeSet<&str> = component.build.as_deref().into_iter().collect();
    if let Some(variants) = component
        .settings
        .get("native_build_variants")
        .and_then(Value::as_object)
    {
        builds.extend(
            variants
                .values()
                .filter_map(|variant| variant.get("build_id").and_then(Value::as_str)),
        );
    }
    let direct: Vec<_> = project
        .builds
        .iter()
        .filter(|build| {
            builds.contains(build.id.as_str()) && build.system.as_deref() == Some("direct-gcc")
        })
        .collect();
    let settings = |key: &'static str| {
        direct
            .iter()
            .filter_map(move |build| build.settings.get(key).and_then(Value::as_object))
            .flatten()
    };
    TestCompileFailures {
        failed: settings("direct_test_compile_failures")
            .filter_map(|(source, stderr)| Some((source.clone(), stderr.as_str()?.to_owned())))
            .collect(),
        unfinished: settings("direct_test_compile_unfinished")
            .map(|(source, kind)| {
                let mut kind = kind.clone();
                // Frozen `shutil.which("gcov")`: without gcov the test compiles plainly.
                if kind["coverage"] == true && !gcov_available(project, environment) {
                    kind["coverage"] = json!(false);
                }
                (source.clone(), kind)
            })
            .collect(),
    }
}

pub fn gcov_available(project: &ResolvedProject, environment: &BTreeMap<String, String>) -> bool {
    ToolRequest {
        id: "gcov".into(),
        explicit: project
            .tools
            .get("gcov")
            .and_then(|tool| tool.path.as_deref())
            .map(|path| crate::files::under(&project.root, path)),
        project_root: project.root.clone(),
        pack_root: None,
        path_env: environment.get("PATH").cloned(),
    }
    .resolve()
    .is_ok()
}

/// One FAIL target and finding per test source that did not compile. The test counts as run
/// and failed, as the frozen engine counted it; nothing about its behaviour was observed. A
/// compile that did not finish is an ERROR target instead (frozen `_append_cpp_tool_target`):
/// no verdict was observed, so the check's evidence is incomplete.
pub fn append_test_compile_failures(output: &mut AnalysisOutput, failures: &TestCompileFailures) {
    if failures.is_empty() {
        return;
    }
    let completed: BTreeMap<&String, &String> = failures
        .failed
        .iter()
        .filter(|(source, _)| !failures.unfinished.contains_key(*source))
        .collect();
    for (source, kind) in &failures.unfinished {
        let mut target = InspectionTarget::at(source, 1);
        target.target_name = source.clone();
        target.status = "ERROR".into();
        target.message = format!(
            "test executable compile did not finish: {}",
            if kind["incomplete"] == true {
                "timed out or exceeded the output bound"
            } else {
                "the compiler was terminated by a signal"
            }
        );
        output
            .observation
            .limitations
            .push(format!("C++ test compile did not finish: {source}"));
        output.targets.push(target);
    }
    if !failures.unfinished.is_empty() {
        output.observation.state = TaskState::Failed;
        output.status = "INCOMPLETE".into();
    }
    for (source, stderr) in &completed {
        let source = *source;
        let mut target = InspectionTarget::at(source, 1);
        target.target_name = source.clone();
        target.status = "FAIL".into();
        let detail: String = stderr.chars().take(2048).collect();
        target.message = format!("test executable did not compile: {}", detail.trim_end());
        add_finding(
            output,
            &target,
            "ici.cpp.executable-test.compile-failed",
            &format!("cpp-test-compile:{source}"),
        );
        if let Some(finding) = output.observation.findings.last_mut() {
            finding.category = "test".into();
        }
        output.targets.push(target);
        output.observation.measurements.push(Measurement {
            name: "cpp-executable.cases".into(),
            value: 1.0,
            evidence: ici_domain::EvidenceLevel::Measured,
            unit: "cases".into(),
            numerator: Some(0),
            denominator: Some(1),
        });
    }
    let mut notes = vec![];
    if !completed.is_empty() {
        let count = completed.len();
        // One physical suite per uncompiled test: counted as a run test that failed (0/1), so
        // the aggregate test count matches the sources the engine attempted.
        let task = output.observation.task_id.clone();
        let suites: Vec<_> = completed
            .keys()
            .map(|source| {
                json!({"canonical_suite_id":format!("{task}.compile:{source}.suite"),"complete":true,
                    "framework":"cpp-executable","passed_tests":0,"skipped_tests":0,"total_tests":1})
            })
            .collect();
        output.extra[format!("{task}.compile")] = json!({
            "test_execution":{"canonical_suite_id":format!("{task}.compile.suite"),"complete":true,
                "framework":"cpp-executable","phase":"test","passed_tests":0,"skipped_tests":0,
                "total_tests":count,"pass_rate":0.0},
            "physical_test_suites":suites,"passed_tests":0,"skipped_tests":0,"total_tests":count,
            "test_outcomes":completed.keys().map(|source|(format!("cpp-executable:{source}"),"FAILED")).collect::<BTreeMap<_,_>>(),
        });
        notes.push(format!("{count} C++ test source(s) did not compile"));
    }
    if !failures.unfinished.is_empty() {
        notes.push(format!(
            "{} C++ test compile(s) did not finish",
            failures.unfinished.len()
        ));
    }
    let note = notes.join("; ");
    output.summary = if output.summary.is_empty() {
        note
    } else {
        format!("{}; {note}", output.summary)
    };
    if output.status == "PASS" {
        output.status = "FAIL".into();
    }
}

/// The frozen output of a test command whose every test source failed to compile.
pub fn test_compile_failures_only(id: &str, failures: &TestCompileFailures) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(id, "cpp-executable");
    output.observation.state = TaskState::Succeeded;
    output.status = "FAIL".into();
    append_test_compile_failures(&mut output, failures);
    output
}

/// The test half of frozen's run whose `_compile_cpp_objects` stopped at a later production
/// source: every test was attempted and failed, so the coverage its notes measure sits beside
/// `0/<tests>` in the engine view.
pub fn legacy_stopped_compile_suite(output: &mut AnalysisOutput, task_id: &str) {
    let total = output.targets.len();
    output.extra["test_execution"] = json!({"canonical_suite_id":format!("{task_id}.suite"),
        "actual_suite_ids":[],"phase":"test","framework":"cpp-executable","complete":true,
        "passed_tests":0,"total_tests":total,"skipped_tests":0});
    output.extra["passed_tests"] = json!(0);
    output.extra["total_tests"] = json!(total);
    output.extra["skipped_tests"] = json!(0);
}

/// Whether a direct build's incomplete `cpp.test` still leaves coverage data. A compile that did
/// not finish ran no test; a run whose output hit the bound ended by itself, and only the
/// transcript is short. `step_diagnostics` has a null exit code for a process that was killed or
/// timed out, and any such process keeps coverage blocked for the whole suite.
pub(super) fn coverage_survives_incomplete_tests(output: &AnalysisOutput) -> bool {
    let has = |prefix: &str| {
        output
            .observation
            .limitations
            .iter()
            .any(|limitation| limitation.starts_with(prefix))
    };
    let unfinished_compile = has("C++ test compile did not finish: ");
    let bounded_run = output.observation.truncated && has("plain C++ executable did not complete");
    let killed = output
        .extra
        .as_object()
        .into_iter()
        .flat_map(|parts| parts.values())
        .filter_map(|part| part.get("step_diagnostics")?.as_array())
        .flatten()
        .any(|step| step["exit_code"].is_null());
    // A test that compiled and ran beside an unfinished compile keeps coverage blocked: frozen's
    // gcov exit 5 over that mix is the approved difference (decisions.md "next 직접 compile/link
    // 한도").
    let test_ran = output
        .extra
        .as_object()
        .into_iter()
        .flat_map(|parts| parts.values())
        .any(|part| part.get("executable_test").is_some());
    output.observation.state == TaskState::Failed
        && !output.observation.timed_out
        && ((unfinished_compile && !test_ran) || bounded_run)
        && !killed
}

/// Frozen `TestEngine._run_cpp_test_case` targets for descriptor-free (direct g++) tests: one
/// `[C++] <file name>` target per `tests/**/*.cpp` at the source, line 1.
pub fn legacy_direct_tests(output: &mut AnalysisOutput, failures: &TestCompileFailures) {
    let incomplete = output.observation.timed_out || output.observation.truncated;
    let runs: BTreeMap<String, Value> = output
        .extra
        .as_object()
        .into_iter()
        .flat_map(|parts| parts.values())
        .filter_map(|part| {
            let run = part.get("executable_test")?;
            Some((run["source"].as_str()?.to_owned(), run.clone()))
        })
        .collect();
    for target in &mut output.targets {
        if target.target_name != target.file_path {
            continue;
        }
        let name = Path::new(&target.file_path).file_name().map_or_else(
            || target.file_path.clone(),
            |name| name.to_string_lossy().into_owned(),
        );
        let message = if let Some(kind) = failures.unfinished.get(&target.file_path) {
            // Frozen `_run_cpp_test_case` / `_compile_cpp_objects`: the plain compile names its
            // cause; the coverage compile's target always reads "terminated" once a tool error
            // was recorded. Each records its own tool error (the ERROR summary).
            let incomplete = kind["incomplete"] == true;
            let coverage = kind["coverage"] == true;
            let path = kind["path"].as_str().unwrap_or(&target.file_path);
            let error = match (coverage, incomplete) {
                (true, true) => format!("C++ coverage compilation incomplete: {path}"),
                (true, false) => {
                    format!("C++ coverage compiler terminated before reporting results: {path}")
                }
                (false, true) => format!("C++ test compilation incomplete: {name}"),
                (false, false) => {
                    format!("C++ test compilation terminated before reporting results: {name}")
                }
            };
            if !output.extra.is_object() {
                output.extra = json!({});
            }
            output.extra["legacy_test_tool_errors"][&target.file_path] = json!(error);
            if incomplete && !coverage {
                "Compilation output was incomplete".into()
            } else {
                "Compiler process terminated before reporting results".into()
            }
        } else if let Some(stderr) = failures.failed.get(&target.file_path) {
            // Frozen `f"Compilation Error: {stderr[:200]}"`: the first 200 characters.
            format!(
                "Compilation Error: {}",
                stderr.chars().take(200).collect::<String>()
            )
        } else {
            match target.status.as_str() {
                "PASS" => "C++ Test Passed".into(),
                "FAIL" => {
                    let run = runs.get(&target.file_path);
                    let text = |key: &str| {
                        run.and_then(|run| run[key].as_str())
                            .unwrap_or_default()
                            .to_owned()
                    };
                    let stdout = text("stdout");
                    // Frozen `run_result.stdout or run_result.stderr`.
                    format!(
                        "Execution Failed: {}",
                        if stdout.is_empty() {
                            text("stderr")
                        } else {
                            stdout
                        }
                    )
                }
                "ERROR" if incomplete => "Execution output was incomplete".into(),
                "ERROR" => "Test process terminated before reporting results".into(),
                _ => continue,
            }
        };
        target.target_name = format!("[C++] {name}");
        target.message = message;
    }
    adapt_test_findings(output);
}

/// Frozen findings of a C++ test engine are its targets (`findings_for_result`): a native
/// finding at a renamed `[C++]` target's source and line names that target's message, so the
/// legacy projection adapts it into the target's own row instead of adding a second finding.
fn adapt_test_findings(output: &mut AnalysisOutput) {
    let messages: BTreeMap<(String, u32), String> = output
        .targets
        .iter()
        .filter(|target| target.target_name.starts_with("[C++] "))
        .map(|target| {
            (
                (target.file_path.clone(), target.start_line),
                target.message.clone(),
            )
        })
        .collect();
    for finding in &output.observation.findings {
        let location = &finding.primary_location;
        let Some(message) = messages.get(&(location.path.clone(), location.start_line)) else {
            continue;
        };
        if !output.extra["finding_details"].is_object() {
            output.extra["finding_details"] = json!({});
        }
        let detail = &mut output.extra["finding_details"][&finding.fingerprint];
        if !detail.is_object() {
            *detail = json!({});
        }
        detail["legacy_message"] = json!(message);
    }
}

/// A failed build of the test/coverage variant as the test check's verdict: one FAIL target at
/// the build descriptor with the adapter's failure message (`[C++] build` in the legacy view).
pub fn test_build_failure(
    id: &str,
    observed: &AnalysisOutput,
    legacy: bool,
) -> Option<AnalysisOutput> {
    let failed = observed
        .targets
        .iter()
        .find(|target| target.status == "FAIL" && target.target_name == "BuildAdapter")?;
    let mut output = AnalysisOutput::new(id, &observed.observation.provider);
    output.observation.state = TaskState::Succeeded;
    output.observation.limitations = observed.observation.limitations.clone();
    output
        .observation
        .limitations
        .push("the test build failed; no test was executed".into());
    output.status = "FAIL".into();
    output.summary = "C++ build failed before tests could run".into();
    let mut target = InspectionTarget::at(&failed.file_path, 1);
    target.target_name = if legacy {
        "[C++] build"
    } else {
        "C++ test build"
    }
    .into();
    target.status = "FAIL".into();
    target.message = failed.message.clone();
    let mut located = target.clone();
    located.message = failed.message.trim().into();
    add_finding(
        &mut output,
        &located,
        "ici.cpp.test-build.failed",
        &format!("cpp-test-build:{}:{}", failed.file_path, located.message),
    );
    if let Some(finding) = output.observation.findings.last_mut() {
        finding.category = "build".into();
        if legacy {
            // Finding text is trimmed; the frozen row keeps the target's untrimmed message.
            output.extra["finding_details"] = json!({finding.fingerprint.clone():
                {"legacy_path": target.file_path, "legacy_message": target.message}});
        }
    }
    output.targets.push(target);
    Some(output)
}

/// Frozen `TestEngine._resolve_test_source`: the first `tests/**/*.cpp` (sorted) whose stem is
/// the test name before `::`; otherwise the build descriptor.
fn legacy_test_source(root: &Path, name: &str, descriptor: &str) -> String {
    let stem = name.split("::").next().unwrap_or(name);
    let mut candidates: Vec<String> = crate::files::tree(root, &root.join("tests"))
        .unwrap_or_default()
        .into_iter()
        .filter(|entry| entry.file && entry.relative.ends_with(".cpp"))
        .map(|entry| entry.relative)
        .collect();
    candidates.sort();
    candidates
        .into_iter()
        .find(|path| Path::new(path).file_stem().and_then(|stem| stem.to_str()) == Some(stem))
        .unwrap_or_else(|| descriptor.to_owned())
}

/// Frozen `_bounded_test_result`: stripped, then at most 512 characters with a `...` tail.
fn bounded_case_name(value: &str) -> String {
    let value = value.trim();
    if value.chars().count() <= 512 {
        return value.to_owned();
    }
    format!("{}...", value.chars().take(509).collect::<String>())
}

/// The ctest JUnit message frozen reads for a terminal verdict (ctest 4.2.3 `--output-junit`:
/// Failed and WILL_FAIL "Failed", "Timeout", segmentation fault "SEGFAULT", a missing executable
/// "Unable to find executable", a DISABLED test `status="disabled"`).
fn junit_message(verdict: &str) -> String {
    match verdict {
        "Exception: SegFault" => "SEGFAULT".into(),
        "Not Run (Disabled)" => "ctest reported status 'disabled'".into(),
        "Not Run" => "Unable to find executable".into(),
        other => other.into(),
    }
}

/// The `SKIP_RETURN_CODE` property CMake recorded for one test in its generated CTestTestfile.
fn skip_return_code(testfile: &Path, name: &str) -> Option<u32> {
    let text = std::fs::read(testfile)
        .ok()
        .filter(|bytes| bytes.len() <= 4 * 1024 * 1024)?;
    let text = String::from_utf8_lossy(&text);
    let prefix = format!("set_tests_properties({name} PROPERTIES ");
    let line = text.lines().find(|line| line.starts_with(&prefix))?;
    let value = line.split_once("SKIP_RETURN_CODE \"")?.1.split_once('"')?.0;
    value.parse().ok()
}

/// The native `ctest.failed` finding of a case whose target the legacy view relocated names the
/// relocated target (`legacy_path`/`legacy_message`), so it becomes that frozen target-adapted
/// row instead of an extra native row at the owned CTestTestfile.
fn map_case_findings(
    output: &mut AnalysisOutput,
    native_path: &str,
    name: &str,
    verdict: &str,
    target: &InspectionTarget,
) {
    let message = format!("test {name}: {verdict}");
    let fingerprints: Vec<String> = output
        .observation
        .findings
        .iter()
        .filter(|finding| {
            matches!(
                finding.native_rule_id.as_str(),
                "ctest.failed" | "make-check.failed"
            ) && finding.primary_location.path == native_path
                && finding.message == message
        })
        .map(|finding| finding.fingerprint.clone())
        .collect();
    if fingerprints.is_empty() {
        return;
    }
    if !output.extra["finding_details"].is_object() {
        output.extra["finding_details"] = json!({});
    }
    for fingerprint in fingerprints {
        let details = &mut output.extra["finding_details"][&fingerprint];
        if !details.is_object() {
            *details = json!({});
        }
        details["legacy_path"] = json!(target.file_path);
        details["legacy_message"] = json!(target.message);
    }
}

/// Frozen `TestEngine._run_cpp_tests_via_adapter` targets for CTest cases: `[C++] <name>` at the
/// resolved test source, PASS "C++ Test Passed", FAIL "Execution Failed: <message>", and a case
/// that did not run SKIP "Execution skipped: <message>". `junit` is whether frozen `run_tests`
/// took the JUnit reading (the report ctest wrote passed `parse_ctest_junit`); otherwise frozen
/// read `parse_ctest_stdout(result.stdout)`: the stdout name unbounded and the verdict itself
/// as the message (`"" if verdict == "Passed" else verdict`).
pub fn legacy_ctest_tests(
    output: &mut AnalysisOutput,
    root: &Path,
    descriptor: &str,
    testfile: &Path,
    junit: bool,
) {
    // Every target of a CTest suite check is one case (native locates a case at a declared
    // source whose stem contains its name, else at the CTestTestfile).
    let mut relocated = vec![];
    for target in &mut output.targets {
        if target.target_name.starts_with('[')
            || !matches!(target.status.as_str(), "PASS" | "FAIL" | "SKIP")
        {
            continue;
        }
        let name = target.target_name.clone();
        let native = (target.file_path.clone(), target.message.clone());
        if junit
            && target.message == "Skipped"
            && let Some(code) = skip_return_code(testfile, &name)
        {
            // ctest's JUnit `<skipped message="SKIP_RETURN_CODE=<n>"/>`, from the same property.
            target.message = format!("SKIP_RETURN_CODE={code}");
        }
        // Frozen `_junit_case`: `_bounded_test_result(node.get("name", ""))` (512 characters);
        // `parse_ctest_stdout` keeps `match.group("name")`.
        let shown = if junit {
            bounded_case_name(&name)
        } else {
            name.clone()
        };
        let verdict = if junit {
            junit_message(&target.message)
        } else {
            target.message.clone()
        };
        target.file_path = legacy_test_source(root, &shown, descriptor);
        target.start_line = 1;
        target.target_name = format!("[C++] {shown}");
        target.message = match target.status.as_str() {
            "PASS" => "C++ Test Passed".into(),
            "FAIL" => format!("Execution Failed: {verdict}"),
            _ => format!("Execution skipped: {verdict}"),
        };
        relocated.push((native, name, target.clone()));
    }
    for ((path, verdict), name, target) in relocated {
        map_case_findings(output, &path, &name, &verdict, &target);
    }
}

/// Frozen SanitizeEngine/ThreadSanitizeEngine `_analysis_scopes`: without a selected C++
/// source (`.cpp/.cc/.cxx/.c`) or C++ test, and (for `sanitize`) without a Python source or
/// `tests/**/*.py`, the engine has no scope at all.
pub fn legacy_sanitizer_without_scope(root: &Path, component: &Component, check_id: &str) -> bool {
    let has = |extensions: &[&str]| {
        component.files.iter().any(|path| {
            Path::new(path)
                .extension()
                .and_then(|extension| extension.to_str())
                .is_some_and(|extension| extensions.contains(&extension))
        })
    };
    if has(&["cpp", "cc", "cxx", "c"]) {
        return false;
    }
    if check_id == "cpp.tsan" {
        return true;
    }
    let python_tests = root.join("tests").is_dir()
        && crate::files::tree(root, &root.join("tests"))
            .map(|entries| {
                entries
                    .iter()
                    .any(|entry| entry.file && entry.relative.ends_with(".py"))
            })
            .unwrap_or(true);
    !has(&["py"]) && !python_tests
}

/// The frozen no-scope verdict: one SKIP target at "." and NOT_APPLICABLE evidence.
pub fn legacy_sanitizer_no_scope(id: &str, check_id: &str) -> AnalysisOutput {
    let (name, message, summary) = if check_id == "cpp.tsan" {
        (
            "ThreadSanitizer",
            "No applicable C++ sources were selected; ThreadSanitizer was not run",
            "ThreadSanitizer skipped: no applicable C++ checks were executed",
        )
    } else {
        (
            "Sanitize",
            "No applicable Python or C++ sources were selected; sanitize was not run",
            "Sanitize skipped: no applicable checks were executed",
        )
    };
    let mut output = AnalysisOutput::new(id, "ici.cpp");
    output.status = "SKIP".into();
    output.summary = summary.into();
    output.observation.measurements.push(Measurement {
        name: "applicability".into(),
        value: 0.0,
        evidence: ici_domain::EvidenceLevel::NotApplicable,
        unit: "checks".into(),
        numerator: None,
        denominator: None,
    });
    output.observation.limitations.push(message.into());
    let mut target = InspectionTarget::at(".", 1);
    target.target_name = name.into();
    target.status = "SKIP".into();
    target.message = message.into();
    output.targets.push(target);
    output
}

/// Frozen SanitizeEngine/ThreadSanitizeEngine adapter (CMake/ctest) targets in CTest case order:
/// a case without a diagnostic is `[C++ <label>] <name>` at the descriptor ("Sanitizers reported
/// no diagnostics" / "Sanitizer run failed: <message>"), a failed case whose output printed a
/// sanitizer report is replaced by its located diagnostic targets, and a case that was not
/// executed (DISABLED, SKIP_RETURN_CODE) is "Sanitizer test was not executed: <reason>" — an
/// ERROR scope under `required = true` (the default; engine ERROR with the first three reasons),
/// otherwise a SKIP scope (partial or skipped verdict, ESTIMATED evidence). `ctest_stdout` is
/// whether frozen `run_tests` read `parse_ctest_stdout(result.stdout)` (the CTest JUnit report
/// was refused): the verdict itself is the message (no JUnit wording, no `SKIP_RETURN_CODE`
/// reason) and `_attach_sanitizer_output` gives the whole transcript to the first executed case
/// that failed, else to the first executed case.
pub fn legacy_ctest_sanitizer(
    output: &mut AnalysisOutput,
    check_id: &str,
    descriptor: &str,
    testfile: &Path,
    required: bool,
    ctest_stdout: bool,
) {
    if !matches!(output.status.as_str(), "PASS" | "FAIL")
        || !output.observation.evidence_is_complete()
    {
        return;
    }
    let (label, clean, partial, skipped_summary, noun) = if check_id == "cpp.tsan" {
        (
            "TSan",
            "Thread Safety & TSan Clean (0 Defects)",
            "ThreadSanitizer partially executed: one or more applicable scopes were skipped",
            "ThreadSanitizer skipped: no applicable C++ checks were executed",
            "Thread Safety",
        )
    } else {
        (
            "ASan/UBSan",
            "Memory Safety & Sanitize Clean (0 Defects)",
            "Sanitize partially executed: one or more applicable scopes were skipped",
            "Sanitize skipped: no applicable checks were executed",
            "Memory / Resource",
        )
    };
    let case = |target: &InspectionTarget| {
        !matches!(
            target.target_name.as_str(),
            "ASan/UBSan Error" | "TSan Error" | "LSan Error" | "SanitizerDiagnosticLocation"
        )
    };
    // Diagnostic targets keep the order of their diagnostics (an unlocated report is the
    // `SanitizerDiagnosticLocation` ERROR scope); each names the CTest case that printed it.
    // Ordered validation keeps each plan's extra under its task id.
    let diagnostics = output
        .extra
        .get("sanitizer_diagnostics")
        .or_else(|| output.extra[&output.observation.task_id].get("sanitizer_diagnostics"));
    let owners: Vec<Option<String>> = diagnostics
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|item| item["test_name"].as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default();
    // Frozen `_attach_sanitizer_output`: when no case printed its own report (a `make check`
    // transcript, or CTest output without a case header), the whole sanitizer transcript belongs
    // to the first executed case that failed, else to the first executed case.
    // A passing owner becomes the failed case its transcript replaces.
    let mut transcript_owner = None;
    let owners = if ctest_stdout || owners.iter().all(Option::is_none) {
        let executed = output
            .targets
            .iter()
            .filter(|target| case(target) && target.target_name != "Instrumented suite")
            .filter(|target| matches!(target.status.as_str(), "PASS" | "FAIL"));
        transcript_owner = executed
            .clone()
            .find(|target| target.status == "FAIL")
            .or_else(|| executed.clone().next())
            .map(|target| target.target_name.clone());
        owners.iter().map(|_| transcript_owner.clone()).collect()
    } else {
        owners
    };
    let mut owners = owners.into_iter();
    let mut unattributed = vec![];
    let mut attributed: BTreeMap<String, Vec<InspectionTarget>> = BTreeMap::new();
    for target in output.targets.iter().filter(|target| !case(target)) {
        match owners.next().flatten() {
            Some(name) => attributed.entry(name).or_default().push(target.clone()),
            None => unattributed.push(target.clone()),
        }
    }
    let cases: Vec<_> = output
        .targets
        .iter()
        .filter(|target| case(target) && target.target_name != "Instrumented suite")
        .cloned()
        .collect();
    // Diagnostics no case claims stay first, as before attribution.
    let mut errors: Vec<String> = unattributed
        .iter()
        .filter(|target| target.status == "ERROR")
        .map(|target| target.message.clone())
        .collect();
    errors.dedup();
    let mut reshaped = unattributed;
    let unclaimed = !reshaped.is_empty();
    let mut mapped = vec![];
    let (mut measured, mut skipped, mut failed) = (0usize, 0usize, false);
    for target in &cases {
        let name = target.target_name.clone();
        if (target.status == "FAIL" || transcript_owner.as_ref() == Some(&name))
            && let Some(diagnostics) = attributed.remove(&name)
        {
            measured += 1;
            failed = true;
            for diagnostic in &diagnostics {
                // Frozen records an unlocated report once among the engine's tool errors.
                if diagnostic.status == "ERROR" && !errors.contains(&diagnostic.message) {
                    errors.push(diagnostic.message.clone());
                }
            }
            // The case's own ctest.failed finding is that frozen diagnostic row.
            mapped.push((target.clone(), diagnostics[0].clone()));
            reshaped.extend(diagnostics);
            continue;
        }
        if unclaimed && target.status == "FAIL" {
            // A report this projection could not attribute replaces the failed cases.
            measured += 1;
            failed = true;
            continue;
        }
        let mut projected = InspectionTarget::at(descriptor, 1);
        projected.target_name = format!("[C++ {label}] {name}");
        projected.status = target.status.clone();
        projected.message = match target.status.as_str() {
            "PASS" => {
                measured += 1;
                "Sanitizers reported no diagnostics".into()
            }
            "FAIL" => {
                measured += 1;
                failed = true;
                let message = if ctest_stdout {
                    format!("Sanitizer run failed: {}", target.message)
                } else {
                    format!("Sanitizer run failed: {}", junit_message(&target.message))
                };
                let mut relocated = projected.clone();
                relocated.message = message.clone();
                mapped.push((target.clone(), relocated));
                message
            }
            _ => {
                skipped += 1;
                let reason = match skip_return_code(testfile, &name) {
                    _ if ctest_stdout && target.message.is_empty() => "no reason reported".into(),
                    _ if ctest_stdout => target.message.clone(),
                    Some(code) if target.message == "Skipped" => format!("SKIP_RETURN_CODE={code}"),
                    _ => junit_message(&target.message),
                };
                let message = format!("Sanitizer test was not executed: {reason}");
                projected.status = if required { "ERROR" } else { "SKIP" }.into();
                if required {
                    errors.push(message.clone());
                }
                message
            }
        };
        reshaped.push(projected);
    }
    // Attributed diagnostics whose case did not fail are still reported.
    for diagnostic in attributed.into_values().flatten() {
        if diagnostic.status == "ERROR" && !errors.contains(&diagnostic.message) {
            errors.push(diagnostic.message.clone());
        }
        reshaped.push(diagnostic);
    }
    for (native, relocated) in mapped {
        map_case_findings(
            output,
            &native.file_path,
            &native.target_name,
            &native.message,
            &relocated,
        );
    }
    let issues = reshaped
        .iter()
        .filter(|target| matches!(target.status.as_str(), "WARN" | "FAIL"))
        .count();
    output.targets = reshaped;
    let estimated = |output: &mut AnalysisOutput| {
        output.observation.measurements.push(Measurement {
            name: "legacy.sanitizer.skipped_scopes".into(),
            value: skipped as f64,
            evidence: ici_domain::EvidenceLevel::Estimated,
            unit: "scopes".into(),
            numerator: None,
            denominator: None,
        });
    };
    if skipped > 0 {
        output.observation.limitations.push(format!(
            "{skipped} CTest case(s) were not executed under the sanitizers"
        ));
    }
    if !errors.is_empty() {
        output.status = "ERROR".into();
        output.summary = errors
            .iter()
            .take(3)
            .cloned()
            .collect::<Vec<_>>()
            .join("; ");
    } else if measured > 0 && skipped > 0 {
        if !failed {
            output.status = "WARN".into();
        }
        output.summary = partial.into();
        estimated(output);
    } else if measured > 0 {
        output.summary = if output.status == "PASS" {
            clean.into()
        } else {
            format!("{issues} {noun} Defect(s) Detected")
        };
    } else if skipped > 0 {
        output.status = "SKIP".into();
        output.summary = skipped_summary.into();
        estimated(output);
    }
}

/// Frozen `TestEngine._run_cpp_tests_via_adapter` targets for a `make check` run (qmake): one
/// `[C++] <binary>` per test binary at its resolved test source, PASS "C++ Test Passed", FAIL
/// "Execution Failed: <message>", a binary that did not run SKIP "Execution skipped: <message>".
pub fn legacy_make_check_tests(output: &mut AnalysisOutput, root: &Path, descriptor: &str) {
    let names: BTreeSet<String> = make_check_cases(output)
        .into_iter()
        .filter_map(|case| case["name"].as_str().map(str::to_owned))
        .collect();
    let mut relocated = vec![];
    for target in &mut output.targets {
        if !names.contains(&target.target_name)
            || !matches!(target.status.as_str(), "PASS" | "FAIL" | "SKIP")
        {
            continue;
        }
        let name = target.target_name.clone();
        let native = (target.file_path.clone(), target.message.clone());
        // Frozen `_junit_case`: `_bounded_test_result(node.get("name", ""))` (512 characters).
        let shown = bounded_case_name(&name);
        target.file_path = legacy_test_source(root, &shown, descriptor);
        target.start_line = 1;
        target.target_name = format!("[C++] {shown}");
        target.message = match target.status.as_str() {
            "PASS" => "C++ Test Passed".into(),
            "FAIL" => format!("Execution Failed: {}", target.message),
            _ if target.message.is_empty() => "Execution skipped: no reason reported".into(),
            _ => format!("Execution skipped: {}", target.message),
        };
        relocated.push((native, name, target.clone()));
    }
    for ((path, verdict), name, target) in relocated {
        map_case_findings(output, &path, &name, &verdict, &target);
    }
}

/// The observed `make check` cases of a validation output (top level or one plan's part).
pub fn make_check_cases(output: &AnalysisOutput) -> Vec<Value> {
    std::iter::once(&output.extra)
        .chain(
            output
                .extra
                .as_object()
                .into_iter()
                .flat_map(|parts| parts.values()),
        )
        .filter_map(|part| part.get("make_check_cases").and_then(Value::as_array))
        .flatten()
        .cloned()
        .collect()
}

/// Frozen `_record_coverage_scope` over the project's sources.
pub(crate) fn frozen_project_scope(
    included: Vec<Value>,
    context: &crate::legacy_extra::Context<'_>,
) -> Value {
    let entry_points: Vec<Value> = context
        .cpp_sources
        .iter()
        .filter(|path| {
            // Frozen `defines_main` (test_coverage.py:407).
            std::fs::read(context.root.join(path))
                .is_ok_and(|bytes| ici_cpp::legacy_defines_main(&bytes))
        })
        .map(|path| json!(path))
        .collect();
    let mut scope = crate::legacy_extra::frozen_coverage_scope(included, context.python_sources);
    scope["excluded_entry_points"] = json!(entry_points);
    scope["discovered_cpp_sources"] = json!(context.cpp_sources.len());
    scope
}

/// Frozen `TestEngine.run` extra keys of a legacy C++ test run, from the native checks'
/// measurements (decisions.md "측정된 legacy Python coverage 결과의 native 키" applied to the
/// C++ facet): frozen objects keep only frozen keys, the native values stay in the native
/// checks' own blocks (`native_checks[].extra`, decisions.md "legacy `extra` 안 native 증거 키").
/// * `test_suites`: frozen `_build_test_suites` over the legacy `[C++]` targets (threshold
///   breaches are not test cases, validation.json `test_quality.py:87:16`);
/// * `branch_coverage`: frozen `_measure_coverage`'s vacuous 100.0 for a measured run without a
///   branch is the coverage check's own legacy reading (`legacy_coverage_inputs`);
/// * `coverage_policy`/`coverage_totals`/`function_rows`/`test_quality`: frozen keys only;
/// * `coverage_provenance`: frozen `cpp` (the consumed gcov JSON tally, when measured) and
///   `scope` (`_record_coverage_scope`).
pub(crate) fn frozen_cpp_test_extra(
    engine: &mut Value,
    context: &crate::legacy_extra::Context<'_>,
) {
    let targets = crate::legacy_extra::targets(engine);
    let cases: Vec<Value> = targets
        .iter()
        .filter(|target| {
            !target["target_name"]
                .as_str()
                .is_some_and(|name| name.starts_with("Threshold: "))
        })
        .cloned()
        .collect();
    let coverage: Option<Value> = crate::legacy_extra::checks(engine)
        .iter()
        .filter(|check| check["check_id"] == "cpp.coverage")
        .flat_map(|check| {
            crate::legacy_extra::check_blocks(check)
                .into_iter()
                .cloned()
                .collect::<Vec<_>>()
        })
        .next();
    // A test that did not finish beside a gcov reading: the projection took the rows from the
    // pair of checks only when both completed, and frozen reports those rows.
    let check_state = |id: &str| {
        crate::legacy_extra::checks(engine)
            .iter()
            .find(|check| check["check_id"] == id)
            .map(|check| check["state"].clone())
    };
    if check_state("cpp.test") == Some(json!("FAILED"))
        && check_state("cpp.coverage") == Some(json!("SUCCEEDED"))
        && let Some(block) = coverage
            .as_ref()
            .filter(|block| block["coverage_totals"].is_object())
    {
        for key in [
            "branch_coverage",
            "coverage_files",
            "coverage_policy",
            "coverage_totals",
            "function_rows",
            "line_coverage",
        ] {
            if let Some(value) = block.get(key) {
                engine["extra"][key] = value.clone();
            }
        }
    }
    // Frozen `_result_summary` of an ERROR run: the first three tool errors, which the direct
    // g++ path records per test executable that did not complete (`_run_cpp_test_case`).
    // A compile that did not finish names its own frozen tool error (`legacy_direct_tests`).
    let compile_errors: serde_json::Map<String, Value> = crate::legacy_extra::checks(engine)
        .iter()
        .filter(|check| check["check_id"] == "cpp.test")
        .flat_map(|check| {
            // Written beside any nested per-task blocks the check carries.
            std::iter::once(&check["extra"])
                .chain(crate::legacy_extra::check_blocks(check))
                .filter_map(|block| block.get("legacy_test_tool_errors")?.as_object().cloned())
                .collect::<Vec<_>>()
        })
        .flatten()
        .collect();
    let tool_errors: Vec<String> = cases
        .iter()
        .filter(|target| target["status"] == "ERROR")
        .filter_map(|target| {
            if let Some(error) = target["file_path"]
                .as_str()
                .and_then(|path| compile_errors.get(path))
                .and_then(Value::as_str)
            {
                return Some(error.to_owned());
            }
            let name = target["target_name"].as_str()?.strip_prefix("[C++] ")?;
            let prefix = match target["message"].as_str()? {
                "Test process terminated before reporting results" => {
                    "C++ test process terminated before reporting results"
                }
                "Execution output was incomplete" => "C++ test execution incomplete",
                "Compiler process terminated before reporting results" => {
                    "C++ test compilation terminated before reporting results"
                }
                "Compilation output was incomplete" => "C++ test compilation incomplete",
                _ => return None,
            };
            Some(format!("{prefix}: {name}"))
        })
        .collect();
    if engine["status"] == "ERROR" && !tool_errors.is_empty() {
        engine["summary"] = json!(tool_errors[..tool_errors.len().min(3)].join("; "));
    }
    let extra = &mut engine["extra"];
    extra["test_suites"] = json!(crate::legacy_extra::frozen_test_suites(&cases));
    // Frozen `_measure_coverage`: `covered_funcs / len(function_rows) * 100.0`, unrounded.
    if extra["function_coverage"].is_number()
        && let Some(rows) = extra["function_rows"]
            .as_array()
            .filter(|rows| !rows.is_empty())
    {
        let covered = rows.iter().filter(|row| row["covered"] == true).count();
        extra["function_coverage"] = json!(covered as f64 / rows.len() as f64 * 100.0);
    }
    if extra.get("passed_tests").is_none() {
        // A run without measured coverage: frozen `TestEngine.run` still writes the counts of
        // the executed `[C++]` cases, the empty quality block and empty coverage rows; its
        // estimated coverage, TEM and metrics summary stay absent (validation.json
        // `test.py:352:12`, no fabricated values).
        let runs: Vec<&Value> = cases
            .iter()
            .filter(|target| {
                target["target_name"].as_str().is_some_and(|name| {
                    name.starts_with("[C++] ") && name != "[C++] build" && name != "[C++] Tests"
                })
            })
            .collect();
        let count = |status: &str| -> u64 {
            runs.iter()
                .filter(|target| target["status"] == status)
                .map(|target| target["metrics"]["test_cases"].as_u64().unwrap_or(1))
                .sum()
        };
        let total: u64 = runs
            .iter()
            .map(|target| target["metrics"]["test_cases"].as_u64().unwrap_or(1))
            .sum();
        let passed = count("PASS");
        extra["passed_tests"] = json!(passed);
        extra["total_tests"] = json!(total);
        extra["skipped_tests"] = json!(count("SKIP"));
        // An incomplete test beside a measured reading keeps that reading's rows
        // (`legacy_hybrid::cpp_incomplete_test_measured`).
        if !extra.get("coverage_totals").is_some_and(Value::is_object) {
            extra["line_coverage"] = Value::Null;
            extra["coverage_files"] = json!([]);
            extra["function_rows"] = json!([]);
            extra["coverage_totals"] = Value::Null;
            extra["coverage_policy"] = Value::Null;
        }
    }
    // No suite ran, so no quality pass either (`legacy_stopped_compile_suite` measures coverage).
    if extra.get("test_quality").is_none_or(Value::is_null) {
        let quality = crate::legacy_extra::frozen_quality(None, context.profile);
        extra["quality_enabled"] = json!(false);
        for (key, value) in quality.as_object().into_iter().flatten() {
            if !matches!(key.as_str(), "enabled" | "mode" | "profile") {
                extra[key] = value.clone();
            }
        }
        extra["test_quality"] = quality;
    }
    if let Some(quality) = extra.get_mut("test_quality").and_then(Value::as_object_mut) {
        quality.remove("evidence");
        quality.remove("repeat_observations");
        quality
            .entry("profile")
            .or_insert_with(|| json!(context.profile));
    }
    if let Some(rows) = extra.get_mut("function_rows").and_then(Value::as_array_mut) {
        for row in rows.iter_mut().filter_map(Value::as_object_mut) {
            row.remove("native_columns");
        }
        // Frozen `parse_gcov_json_dir` sorts by (file, start_line, start_column, name), not by
        // the mangled symbol. Its gcov text rows (no columns) keep each report's record order;
        // frozen lists the reports in directory order, native by source path.
        if rows.iter().all(|row| row.get("start_column").is_none()) {
            rows.sort_by(|left, right| left["file"].as_str().cmp(&right["file"].as_str()));
        } else {
            rows.sort_by(|left, right| {
                let key = |row: &Value| {
                    (
                        row["file"].as_str().unwrap_or_default().to_owned(),
                        row["start_line"].as_u64().unwrap_or(0),
                        row["start_column"].as_u64().unwrap_or(0),
                        row["name"].as_str().unwrap_or_default().to_owned(),
                    )
                };
                key(left).cmp(&key(right))
            });
        }
    }
    // Frozen `calculate_tem` always computes a score in the legacy engine (an estimate stands
    // in for unmeasured coverage), so `pass_rate` is `tem.calculate`'s `round(pass_rate, 4)`.
    let (passed, total) = (
        extra["passed_tests"].as_u64().unwrap_or(0),
        extra["total_tests"].as_u64().unwrap_or(0),
    );
    if passed <= total {
        let rate = if total == 0 {
            0.0
        } else {
            passed as f64 / total as f64
        };
        extra["pass_rate"] = json!(format!("{rate:.4}").parse::<f64>().unwrap_or(rate));
    }
    let measured = extra.get("coverage_totals").is_some_and(Value::is_object);
    if let Some(totals) = extra
        .get_mut("coverage_totals")
        .and_then(Value::as_object_mut)
    {
        totals.retain(|key, _| matches!(key.as_str(), "stmts" | "miss" | "cover" | "branch_cover"));
    }
    if let Some(policy) = extra
        .get_mut("coverage_policy")
        .and_then(Value::as_object_mut)
    {
        policy
            .retain(|key, _| matches!(key.as_str(), "metrics" | "files" | "regression_tolerance"));
    }
    let branch = extra["branch_coverage"].as_f64();
    let included: Vec<Value> = {
        let mut files: Vec<Value> = extra["coverage_files"]
            .as_array()
            .map(|rows| rows.iter().map(|row| row["file"].clone()).collect())
            .unwrap_or_default();
        files.sort_by(|left, right| left.as_str().cmp(&right.as_str()));
        files
    };
    let scope = frozen_project_scope(included, context);
    let mut provenance = serde_json::Map::new();
    // Frozen writes its gcov-text provenance even for a reading without rows.
    if let Some(info) = coverage
        .as_ref()
        .and_then(|block| {
            let json = block.get("gcov_json_info").filter(|_| measured);
            json.or(block.get("gcov_text_info"))
        })
        .filter(|info| info.is_object())
    {
        let mut info = info.clone();
        if let Some(info) = info.as_object_mut() {
            info.remove("legacy_coverage_error");
        }
        provenance.insert("cpp".into(), info);
    }
    // Frozen `_measure_coverage` returns before `_record_coverage_scope` when no production
    // source has a countable line (test_coverage.py:330-344).
    if context.python_sources > 0
        || context.cpp_sources.iter().any(|path| {
            std::fs::read(context.root.join(path)).is_ok_and(|bytes| {
                String::from_utf8_lossy(&bytes).lines().any(|line| {
                    let line = line.trim();
                    !line.is_empty() && !["#", "//", "/*"].iter().any(|mark| line.starts_with(mark))
                })
            })
        })
    {
        provenance.insert("scope".into(), scope);
    }
    extra["coverage_provenance"] = Value::Object(provenance);
    if measured {
        crate::legacy_extra::frozen_coverage_policy(engine);
        // `get_mut`, so an incomplete reading's null snapshot (frozen `{}`) stays null.
        if let (Some(branch), Some(metrics)) = (
            branch,
            engine["extra"]
                .get_mut("coverage_policy")
                .and_then(|policy| policy.get_mut("metrics"))
                .and_then(Value::as_object_mut),
        ) {
            metrics.insert(
                "branch".into(),
                json!(format!("{branch:.1}").parse::<f64>().unwrap_or(branch)),
            );
        }
    }
}

#[cfg(test)]
mod frozen_extra_tests {
    use super::*;

    /// Frozen `_bounded_test_result` (core/_cmake_test_results.py:172-176) on a JUnit case
    /// name; capture test:cmake-junit-long-name replays a 600-character CTest name in full.
    #[test]
    fn ctest_case_names_keep_frozen_512_character_bound() {
        assert_eq!(bounded_case_name("  calc  "), "calc");
        let name = "n".repeat(600);
        let shown = bounded_case_name(&name);
        assert_eq!(shown.chars().count(), 512);
        assert!(shown.ends_with("n..."));
        assert_eq!(bounded_case_name(&"é".repeat(512)), "é".repeat(512));
    }

    fn context<'a>(
        settings: &'a Value,
        root: &'a Path,
        sources: &'a [String],
    ) -> crate::legacy_extra::Context<'a> {
        crate::legacy_extra::Context {
            settings,
            profile: "",
            python_sources: 0,
            root,
            cpp_sources: sources,
            cpp_scope: false,
        }
    }

    fn target(name: &str, path: &str, status: &str, message: &str) -> Value {
        json!({"target_name": name, "file_path": path, "start_line": 1, "status": status,
            "message": message, "metrics": {}})
    }

    /// Positive: a measured direct run keeps frozen's keys only, keeps the coverage check's
    /// vacuous 100.0 branch coverage (frozen `_measure_coverage` without a branch), rounds
    /// `pass_rate` like `tem.calculate`, builds the suites from the `[C++]` targets (not threshold breaches)
    /// and records frozen's `cpp`/`scope` provenance (entry points defining `main` excluded).
    #[test]
    fn measured_cpp_test_extra_keeps_frozen_keys_and_values() {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(root.path().join("src")).unwrap();
        std::fs::write(
            root.path().join("src/calc.cpp"),
            "int add(int a){return a;}\n",
        )
        .unwrap();
        std::fs::write(
            root.path().join("src/main.cpp"),
            "int main() { return 0; }\n",
        )
        .unwrap();
        let sources = vec!["src/calc.cpp".to_owned(), "src/main.cpp".to_owned()];
        let tally = json!({"format": "gcov-json", "report_count": 2});
        let mut engine = json!({
            "status": "FAIL",
            "summary": "1/3 Tests Passed | Line: 100.0%, Func: 100.0%, Branch: 100.0% -> TEM: 1.67 / 5.0",
            "targets": [
                target("[C++] test_fail.cpp", "tests/test_fail.cpp", "FAIL", "Execution Failed: x"),
                target("[C++] test_pass.cpp", "tests/test_pass.cpp", "PASS", "C++ Test Passed"),
                target("Coverage:Functions", ".", "PASS", "ok"),
                target("Threshold: TEM score", ".", "WARN", "low"),
            ],
            "extra": {
                "passed_tests": 1, "total_tests": 3, "pass_rate": 1.0 / 3.0,
                "branch_coverage": 100.0,
                "coverage_files": [{"file": "src/calc.cpp", "cover": 100.0}],
                "coverage_totals": {"stmts": 2, "miss": 0, "cover": 100.0, "branch_cover": null,
                    "covered": 2, "nb": 0},
                "coverage_policy": {"metrics": {"line": 100.0}, "files": {},
                    "regression_tolerance": null, "schema_id": "ici.coverage-policy"},
                "function_rows": [{"name": "add(int)", "native_columns": {"start": 5}}],
                "test_quality": {"enabled": false, "evidence": "NOT_SELECTED"},
                "coverage_provenance": {"format": "gcov-json"},
                "native_checks": [{"check_id": "cpp.test", "extra": {}},
                    {"check_id": "cpp.coverage", "extra": {"gcov_json_info": tally}}],
            },
        });
        let settings = json!({});
        frozen_cpp_test_extra(&mut engine, &context(&settings, root.path(), &sources));
        let extra = &engine["extra"];
        assert_eq!(extra["branch_coverage"], json!(100.0));
        assert_eq!(
            engine["summary"],
            "1/3 Tests Passed | Line: 100.0%, Func: 100.0%, Branch: 100.0% -> TEM: 1.67 / 5.0"
        );
        assert_eq!(extra["pass_rate"], json!(0.3333));
        assert_eq!(
            extra["coverage_totals"],
            json!({"stmts": 2, "miss": 0, "cover": 100.0, "branch_cover": null})
        );
        assert_eq!(
            extra["coverage_policy"],
            json!({"metrics": {"line": 100.0, "branch": 100.0}, "files": {},
                "regression_tolerance": null})
        );
        assert_eq!(extra["function_rows"], json!([{"name": "add(int)"}]));
        assert_eq!(
            extra["test_quality"],
            json!({"enabled": false, "profile": ""})
        );
        assert_eq!(extra["coverage_provenance"]["cpp"], tally);
        let scope = &extra["coverage_provenance"]["scope"];
        assert_eq!(scope["included_sources"], json!(["src/calc.cpp"]));
        assert_eq!(scope["excluded_entry_points"], json!(["src/main.cpp"]));
        assert_eq!(scope["discovered_cpp_sources"], json!(2));
        let suites = extra["test_suites"].as_array().unwrap();
        assert_eq!(suites.len(), 2, "{suites:?}");
        assert_eq!(suites[0]["tests"][0]["name"], "[C++] test_fail.cpp");
        assert_eq!(suites[0]["failed"], json!(1));
    }

    /// Negative and failure: a run without measured coverage writes frozen's counts, the empty
    /// quality block and empty coverage rows (no fabricated estimate), and an ERROR run whose
    /// test process terminated reads frozen's tool-error summary.
    #[test]
    fn unmeasured_and_terminated_cpp_runs_keep_frozen_counts_and_summary() {
        let root = tempfile::tempdir().unwrap();
        let settings = json!({});
        let mut engine = json!({
            "status": "ERROR",
            "summary": "plain C++ executable did not complete",
            "targets": [
                target("[C++] test_abort.cpp", "tests/test_abort.cpp", "ERROR",
                    "Test process terminated before reporting results"),
                target("[C++] test_pass.cpp", "tests/test_pass.cpp", "PASS", "C++ Test Passed"),
                target("[C++] test_skip", "tests/test_skip.cpp", "SKIP", "Execution skipped: x"),
            ],
            "extra": {"native_checks": [{"check_id": "cpp.test", "extra": {}}]},
        });
        frozen_cpp_test_extra(&mut engine, &context(&settings, root.path(), &[]));
        let extra = &engine["extra"];
        assert_eq!(
            engine["summary"],
            "C++ test process terminated before reporting results: test_abort.cpp"
        );
        assert_eq!(
            (
                &extra["passed_tests"],
                &extra["total_tests"],
                &extra["skipped_tests"]
            ),
            (&json!(1), &json!(3), &json!(1))
        );
        assert_eq!(extra["pass_rate"], json!(0.3333));
        for key in ["line_coverage", "coverage_totals", "coverage_policy"] {
            assert!(extra[key].is_null(), "{key}");
        }
        assert_eq!(extra["coverage_files"], json!([]));
        assert!(extra.get("branch_coverage").is_none());
        assert!(extra.get("tem_score").is_none());
        assert_eq!(extra["mutation_status"], "disabled");
        assert_eq!(
            extra["coverage_provenance"],
            json!({"scope": crate::legacy_extra::frozen_coverage_scope(vec![], 0)})
        );
    }

    /// A native executable-test finding at a renamed `[C++]` target names that target's
    /// message, so the legacy projection adapts it instead of adding a second finding; a
    /// finding elsewhere is untouched.
    #[test]
    fn executable_test_findings_name_their_legacy_target() {
        let mut output = AnalysisOutput::new("project.cpp.test", "cpp-executable");
        let mut failed = InspectionTarget::at("tests/test_fail.cpp", 1);
        failed.target_name = "tests/test_fail.cpp".into();
        failed.status = "FAIL".into();
        failed.message = "executable exited 1".into();
        add_finding(&mut output, &failed, "ici.cpp.executable-test.failed", "a");
        let mut other = InspectionTarget::at("src/other.cpp", 3);
        other.message = "elsewhere".into();
        add_finding(&mut output, &other, "ici.cpp.other", "b");
        output.targets.push(failed);
        output.extra = json!({"project.cpp.test.case0": {"executable_test":
            {"source": "tests/test_fail.cpp", "stdout": "twice(2) was 5\n", "stderr": ""}}});
        legacy_direct_tests(&mut output, &TestCompileFailures::default());
        assert_eq!(output.targets[0].target_name, "[C++] test_fail.cpp");
        let details = output.extra["finding_details"].as_object().unwrap();
        assert_eq!(details.len(), 1);
        assert_eq!(
            details[&output.observation.findings[0].fingerprint]["legacy_message"],
            "Execution Failed: twice(2) was 5\n"
        );
    }

    /// Frozen `_run_cpp_test_case`/`_compile_cpp_objects` (test.py:839): a test compile that
    /// did not finish is that test's ERROR target and tool error, never a FAIL verdict; the
    /// plain compile names its cause, the coverage compile always reads "terminated", and the
    /// ERROR summary joins the per-test tool errors. A complete exit-1 compile stays a FAIL.
    #[test]
    fn unfinished_test_compiles_are_frozen_error_targets_and_tool_errors() {
        let failures = |coverage: bool| TestCompileFailures {
            failed: BTreeMap::from([
                ("tests/test_bad.cpp".into(), "error: x\n".into()),
                ("tests/test_fail.cpp".into(), String::new()),
                ("tests/test_pass.cpp".into(), String::new()),
            ]),
            unfinished: BTreeMap::from([
                (
                    "tests/test_fail.cpp".into(),
                    json!({"incomplete": true, "coverage": coverage,
                        "path": "/p/tests/test_fail.cpp"}),
                ),
                (
                    "tests/test_pass.cpp".into(),
                    json!({"incomplete": false, "coverage": coverage,
                        "path": "/p/tests/test_pass.cpp"}),
                ),
            ]),
        };
        for (coverage, messages, errors) in [
            (
                false,
                [
                    "Compilation output was incomplete",
                    "Compiler process terminated before reporting results",
                ],
                [
                    "C++ test compilation incomplete: test_fail.cpp",
                    "C++ test compilation terminated before reporting results: test_pass.cpp",
                ],
            ),
            (
                true,
                [
                    "Compiler process terminated before reporting results",
                    "Compiler process terminated before reporting results",
                ],
                [
                    "C++ coverage compilation incomplete: /p/tests/test_fail.cpp",
                    "C++ coverage compiler terminated before reporting results: /p/tests/test_pass.cpp",
                ],
            ),
        ] {
            let failures = failures(coverage);
            let mut output = test_compile_failures_only("project.cpp.test", &failures);
            assert_eq!(output.observation.state, TaskState::Failed);
            assert_eq!(output.status, "INCOMPLETE");
            assert_eq!(
                output.observation.limitations,
                [
                    "C++ test compile did not finish: tests/test_fail.cpp",
                    "C++ test compile did not finish: tests/test_pass.cpp"
                ]
            );
            // Only the complete exit-1 compile is a finding and a counted failed case.
            assert_eq!(output.observation.findings.len(), 1);
            assert_eq!(output.observation.measurements.len(), 1);
            legacy_direct_tests(&mut output, &failures);
            let statuses: Vec<_> = output
                .targets
                .iter()
                .map(|target| (target.target_name.as_str(), target.status.as_str()))
                .collect();
            assert_eq!(
                statuses,
                [
                    ("[C++] test_fail.cpp", "ERROR"),
                    ("[C++] test_pass.cpp", "ERROR"),
                    ("[C++] test_bad.cpp", "FAIL")
                ]
            );
            assert_eq!(
                [&output.targets[0].message, &output.targets[1].message],
                messages
            );
            assert_eq!(
                output.extra["legacy_test_tool_errors"],
                json!({"tests/test_fail.cpp": errors[0], "tests/test_pass.cpp": errors[1]})
            );
            // The ERROR summary reads those tool errors in target order.
            let targets: Vec<Value> = output
                .targets
                .iter()
                .map(|t| target(&t.target_name, &t.file_path, &t.status, &t.message))
                .collect();
            let mut engine = json!({"status": "ERROR", "summary": "", "targets": targets,
                "extra": {"native_checks": [{"check_id": "cpp.test", "extra": output.extra}]}});
            let root = tempfile::tempdir().unwrap();
            let settings = json!({});
            frozen_cpp_test_extra(&mut engine, &context(&settings, root.path(), &[]));
            assert_eq!(engine["summary"], json!(errors.join("; ")));
        }
    }
}

/// Frozen `SanitizeEngine.run` extra of a C++-only legacy sanitizer engine: the issue count and
/// `sanitizer_diagnostics` in frozen's `_sanitizer_details` shape — `primary_location`
/// {path, start_line, start_column}, `related_locations` {path, start_line, start_column, label},
/// the diagnosed `test_name` and the `process_evidence_index` of the run that reported it (also
/// on each diagnostic target's metrics, `_append_sanitizer_diagnostics`). The native evidence
/// (steps, scope, test evidence, per-case blocks, finding details) stays in
/// `native_checks[].extra` (decisions.md "legacy `extra` 안 native 증거 키").
pub(crate) fn frozen_sanitizer_extra(engine: &mut Value) {
    let checks = crate::legacy_extra::checks(engine);
    if checks.is_empty()
        || !checks.iter().all(|check| {
            matches!(
                check["check_id"].as_str(),
                Some("cpp.sanitize" | "cpp.tsan")
            )
        })
    {
        return;
    }
    let evidence = engine["tool_evidence"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    // The adapter run (`_adapter_process_evidence_index`): the last ctest/make process.
    let adapter_index = evidence.iter().rposition(|record| {
        matches!(
            record["name"].as_str(),
            Some("ctest" | "make check" | "make test")
        )
    });
    let mut details = vec![];
    // Frozen `run_evidence.error` of a direct test whose report could not be normalized: the
    // `SanitizerDiagnostic` ERROR target's message on that test's `sanitizer execution`.
    let refused: Vec<(String, Value)> = engine["targets"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|target| target["target_name"] == "SanitizerDiagnostic")
        .filter_map(|target| {
            Some((
                target["file_path"].as_str()?.to_owned(),
                target["message"].clone(),
            ))
        })
        .collect();
    let mut refused_records = vec![];
    for check in &checks {
        for block in crate::legacy_extra::check_blocks(check) {
            let artifact = block["sanitizer_scope"]["instrumented_artifacts"][0]["path"]
                .as_str()
                .unwrap_or_default();
            if block["sanitizer_diagnostics"]
                .as_array()
                .is_none_or(|diagnostics| diagnostics.is_empty())
                && !artifact.is_empty()
                && let Some((_, message)) = refused.iter().find(|(path, _)| {
                    block["sanitizer_test_evidence"]["targets"][0]["file_path"] == path.as_str()
                })
                && let Some(index) = evidence.iter().position(|record| {
                    record["name"] == "sanitizer execution"
                        && record["argv"][0]
                            .as_str()
                            .is_some_and(|program| program.ends_with(artifact))
                })
            {
                refused_records.push((index, message.clone()));
            }
            let Some(diagnostics) = block["sanitizer_diagnostics"].as_array() else {
                continue;
            };
            let test_evidence = &block["sanitizer_test_evidence"];
            // Direct g++: one executable per test source, run by its own `sanitizer execution`.
            let artifact = block["sanitizer_scope"]["instrumented_artifacts"][0]["path"]
                .as_str()
                .unwrap_or_default();
            let direct_index = (!artifact.is_empty())
                .then(|| {
                    evidence.iter().position(|record| {
                        record["name"] == "sanitizer execution"
                            && record["argv"][0]
                                .as_str()
                                .is_some_and(|program| program.ends_with(artifact))
                    })
                })
                .flatten();
            let direct_test = test_evidence["kind"] == "cpp_executable";
            let test_name = if direct_test {
                test_evidence["targets"][0]["file_path"]
                    .as_str()
                    .and_then(|path| Path::new(path).file_name())
                    .map(|name| name.to_string_lossy().into_owned())
            } else {
                // An adapter case: frozen names the case whose output held the report; with
                // one failed case that is the case.
                let mut failed: Vec<&str> = block["make_check_cases"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter(|case| case["passed"] == false && case["executed"] == true)
                    .filter_map(|case| case["name"].as_str())
                    .chain(
                        test_evidence["targets"]
                            .as_array()
                            .into_iter()
                            .flatten()
                            .filter(|target| target["status"] == "FAIL")
                            .filter_map(|target| target["target_name"].as_str()),
                    )
                    .collect();
                // Make reports a case both as a check case and as its FAIL test target.
                failed.sort_unstable();
                failed.dedup();
                (failed.len() == 1).then(|| failed[0].to_owned())
            };
            let index = if direct_test {
                direct_index
            } else {
                adapter_index
            };
            for diagnostic in diagnostics {
                let location = |item: &Value, label: bool| {
                    let mut shaped = json!({"path": item["path"], "start_line": item["start_line"],
                        "start_column": item.get("start_column").cloned().unwrap_or(Value::Null)});
                    if label {
                        shaped["label"] = item["label"].clone();
                    }
                    shaped
                };
                details.push(json!({
                    "kind": diagnostic["kind"], "tool_name": diagnostic["tool_name"],
                    "defect": diagnostic["defect"], "rule_id": diagnostic["rule_id"],
                    "message": diagnostic["message"],
                    "test_name": diagnostic["test_name"].as_str().map(str::to_owned)
                        .or_else(|| test_name.clone()).unwrap_or_default(),
                    "process_evidence_index": index,
                    "frames_observed": diagnostic["frames_observed"],
                    "project_frames": diagnostic["project_frames"],
                    "primary_location": if diagnostic["primary_location"].is_object() {
                        location(&diagnostic["primary_location"], false)
                    } else {
                        Value::Null
                    },
                    "related_locations": diagnostic["related_locations"].as_array()
                        .into_iter().flatten().map(|item| location(item, true))
                        .collect::<Vec<_>>(),
                }));
            }
        }
    }
    let indices: Vec<Value> = details
        .iter()
        .map(|detail| detail["process_evidence_index"].clone())
        .collect();
    for (index, message) in refused_records {
        engine["tool_evidence"][index]["error"] = message;
    }
    // Frozen `_append_sanitizer_targets`: the run that reported an unlocated diagnostic carries
    // its tool error.
    for detail in &details {
        if let (true, Some(index)) = (
            detail["primary_location"].is_null(),
            detail["process_evidence_index"].as_u64(),
        ) && let Some(record) = engine["tool_evidence"].get_mut(index as usize)
        {
            record["error"] = json!(format!(
                "{} diagnostic has no validated project-owned source location",
                detail["tool_name"].as_str().unwrap_or_default()
            ));
        }
    }
    let mut diagnostic_targets = engine["targets"]
        .as_array_mut()
        .into_iter()
        .flatten()
        .filter(|target| {
            matches!(
                target["target_name"].as_str(),
                Some(
                    "ASan/UBSan Error"
                        | "TSan Error"
                        | "LSan Error"
                        | "SanitizerDiagnosticLocation"
                )
            )
        })
        .collect::<Vec<_>>();
    let mut adapted = vec![];
    if diagnostic_targets.len() == indices.len() {
        for (target, index) in diagnostic_targets.iter_mut().zip(&indices) {
            if !index.is_null() {
                target["metrics"]["process_evidence_index"] = index.clone();
                if target["target_name"] == "SanitizerDiagnosticLocation" {
                    adapted.push((
                        target["file_path"].clone(),
                        target["start_line"].clone(),
                        index.clone(),
                    ));
                }
            }
        }
    }
    // The finding frozen adapts from an unlocated diagnostic's target carries the target's
    // metrics (`findings_for_result`), the process index included.
    for finding in engine["findings"].as_array_mut().into_iter().flatten() {
        let location = &finding["primary_location"];
        if location["label"] != "SanitizerDiagnosticLocation" {
            continue;
        }
        if let Some((_, _, index)) = adapted
            .iter()
            .find(|(path, line, _)| location["path"] == *path && location["start_line"] == *line)
        {
            finding["metrics"]["process_evidence_index"] = json!({"unit": "", "value": index});
        }
    }
    let extra = &mut engine["extra"];
    extra["sanitizer_diagnostics"] = json!(details);
    if let Some(fields) = extra.as_object_mut() {
        fields.retain(|key, _| {
            matches!(
                key.as_str(),
                "sanitize_issues"
                    | "thread_sanitize_issues"
                    | "sanitizer_diagnostics"
                    | "native_checks"
                    | "target_scopes"
                    | "finding_identity_map"
            )
        });
    }
}

#[cfg(test)]
mod frozen_sanitizer_tests {
    use super::*;

    fn diagnostic(test_name: Option<&str>) -> Value {
        let mut value = json!({
            "kind": "asan", "tool_name": "AddressSanitizer", "defect": "heap-use-after-free",
            "rule_id": "ici.sanitize.asan.heap-use-after-free",
            "message": "AddressSanitizer detected heap use after free",
            "frames_observed": 14, "project_frames": 3,
            "primary_location": {"label": "frame #0: main", "path": "tests/test_uaf.cpp", "start_line": 2},
            "related_locations": [{"label": "frame #0: free", "path": "[external]", "start_line": 51}],
            "external_locations": [{"column_unit": "producer-native", "label": "frame #0: free",
                "native_column": null, "path": "[external]", "start_line": 51}],
        });
        if let Some(name) = test_name {
            value["test_name"] = json!(name);
        }
        value
    }

    fn error_target() -> Value {
        json!({"target_name": "ASan/UBSan Error", "file_path": "tests/test_uaf.cpp", "start_line": 2,
            "status": "FAIL", "metrics": {"frames_observed": 14, "project_frames": 3}})
    }

    fn frozen_detail(test_name: &str, index: usize) -> Value {
        json!({
            "kind": "asan", "tool_name": "AddressSanitizer", "defect": "heap-use-after-free",
            "rule_id": "ici.sanitize.asan.heap-use-after-free",
            "message": "AddressSanitizer detected heap use after free",
            "test_name": test_name, "process_evidence_index": index,
            "frames_observed": 14, "project_frames": 3,
            "primary_location": {"path": "tests/test_uaf.cpp", "start_line": 2, "start_column": null},
            "related_locations": [{"path": "[external]", "start_line": 51, "start_column": null,
                "label": "frame #0: free"}],
        })
    }

    /// Positive (direct g++): frozen `_sanitizer_details` keys, the test source's file name and
    /// the index of the `sanitizer execution` that ran the diagnosed executable, on the detail
    /// and the target; the native per-case evidence stays only in `native_checks`.
    #[test]
    fn direct_sanitizer_diagnostics_take_the_frozen_shape_and_process_index() {
        let block = |case: usize, test: &str, diagnostics: Vec<Value>| {
            json!({"sanitizer_diagnostics": diagnostics, "steps": [], "optional_steps": [],
                "conditional_steps": [],
                "sanitizer_scope": {"instrumented_artifacts": [{"path": format!(".ici/bin/test-{case}")}]},
                "sanitizer_test_evidence": {"kind": "cpp_executable",
                    "targets": [{"file_path": test, "status": "PASS"}]}})
        };
        let check = json!({"check_id": "cpp.sanitize", "extra": {
            "project.cpp.sanitize.case0": block(0, "tests/test_ok.cpp", vec![]),
            "project.cpp.sanitize.case1": block(1, "tests/test_uaf.cpp", vec![diagnostic(None)]),
        }});
        let execution = |case: usize| json!({"name": "sanitizer execution", "argv": [format!("/r/.ici/bin/test-{case}")]});
        let mut engine = json!({
            "tool_evidence": [{"name": "sanitizer compile", "argv": ["/usr/bin/g++"]}, execution(0),
                {"name": "sanitizer compile", "argv": ["/usr/bin/g++"]}, execution(1)],
            "targets": [error_target()],
            "extra": {"native_checks": [check], "target_scopes": {}, "finding_identity_map": {},
                "sanitize_issues": 1, "sanitizer_diagnostics": [diagnostic(None)],
                "finding_details": {}, "project.cpp.sanitize.case1": {}},
        });
        frozen_sanitizer_extra(&mut engine);
        assert_eq!(
            engine["extra"]["sanitizer_diagnostics"],
            json!([frozen_detail("test_uaf.cpp", 3)])
        );
        assert_eq!(engine["targets"][0]["metrics"]["process_evidence_index"], 3);
        let mut keys: Vec<&String> = engine["extra"].as_object().unwrap().keys().collect();
        keys.sort();
        assert_eq!(
            keys,
            [
                "finding_identity_map",
                "native_checks",
                "sanitize_issues",
                "sanitizer_diagnostics",
                "target_scopes"
            ]
        );
    }

    /// Positive (adapter): the native case name is kept and the index is the last
    /// ctest/make test process (`_adapter_process_evidence_index`). Negative: an engine with
    /// a non-sanitizer native check is left untouched.
    #[test]
    fn adapter_sanitizer_diagnostics_name_the_case_and_the_last_test_process() {
        let check = json!({"check_id": "cpp.tsan", "extra": {
            "sanitizer_diagnostics": [diagnostic(Some("test_uaf"))],
            "sanitizer_test_evidence": {"kind": "ctest", "targets": []}}});
        let mut engine = json!({
            "tool_evidence": [{"name": "cmake configure", "argv": []},
                {"name": "ctest", "argv": []}, {"name": "cmake build", "argv": []}],
            "targets": [error_target()],
            "extra": {"native_checks": [check], "sanitizer_diagnostics": [],
                "steps": [], "sanitizer_scope": {}},
        });
        let mut untouched = engine.clone();
        untouched["extra"]["native_checks"][0]["check_id"] = json!("cpp.test");
        let before = untouched.clone();
        frozen_sanitizer_extra(&mut engine);
        assert_eq!(
            engine["extra"]["sanitizer_diagnostics"],
            json!([frozen_detail("test_uaf", 1)])
        );
        assert_eq!(engine["targets"][0]["metrics"]["process_evidence_index"], 1);
        assert!(engine["extra"].get("steps").is_none());
        frozen_sanitizer_extra(&mut untouched);
        assert_eq!(untouched, before);

        // Make: the failed case is both a check case and a FAIL test target (one name).
        let block = json!({"sanitizer_diagnostics": [diagnostic(None)],
            "make_check_cases": [{"name": "test_ok", "passed": true, "executed": true},
                {"name": "test_uaf", "passed": false, "executed": true}],
            "sanitizer_test_evidence": {"kind": "make_check", "targets": [
                {"target_name": "test_ok", "status": "PASS"},
                {"target_name": "test_uaf", "status": "FAIL"}]}});
        let mut make = json!({
            "tool_evidence": [{"name": "make build", "argv": []}, {"name": "make test", "argv": []}],
            "targets": [error_target()],
            "extra": {"native_checks": [{"check_id": "cpp.sanitize",
                "extra": {"project.cpp.sanitize": block}}]},
        });
        frozen_sanitizer_extra(&mut make);
        assert_eq!(
            make["extra"]["sanitizer_diagnostics"],
            json!([frozen_detail("test_uaf", 1)])
        );
    }
}

/// Frozen `BinaryCompatibilityEngine._readelf`: the readelf version on its process records and
/// findings is the analysis context's capability version when that capability is available and
/// complete (no own probe record), else "". Native records the same suite capability inventory.
pub(crate) fn frozen_readelf_version(document: &mut Value) {
    let version = document["capability_inventory"]["tools"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|row| row["name"] == "readelf" && row["available"] == true && row["complete"] == true)
        .and_then(|row| row["version"].as_str())
        .unwrap_or_default()
        .to_owned();
    for engine in document["results"].as_array_mut().into_iter().flatten() {
        if engine["engine_name"] != "binary_compat" {
            continue;
        }
        for (list, name, field) in [
            ("tool_evidence", "name", "version"),
            ("findings", "tool_name", "tool_version"),
        ] {
            for item in engine[list].as_array_mut().into_iter().flatten() {
                if item[name] == "readelf binary compatibility" {
                    item[field] = json!(version);
                }
            }
        }
    }
}

/// Frozen `BinaryCompatibilityEngine.run` extra: `elf` {state (the engine evidence),
/// artifacts_checked, facts} with one fact per inspected ELF artifact in target order (`id`,
/// `path`, class, machine, type, needed, rpath, runpath, versions GLIBC/GLIBCXX/CXXABI,
/// stripped). The native artifact facts, headers and steps stay in `native_checks[].extra`
/// (decisions.md "legacy `extra` 안 native 증거 키").
pub(crate) fn frozen_binary_compat_extra(engine: &mut Value) {
    let checks = crate::legacy_extra::checks(engine);
    if checks.is_empty()
        || !checks
            .iter()
            .all(|check| check["check_id"] == "cpp.binary-compat")
    {
        return;
    }
    let mut artifacts = serde_json::Map::new();
    for check in &checks {
        for block in crate::legacy_extra::check_blocks(check) {
            if let Some(items) = block["artifacts"].as_object() {
                artifacts.extend(items.clone());
            }
        }
    }
    if let Some(items) = engine["extra"]["artifacts"].as_object() {
        artifacts.extend(items.clone());
    }
    let facts: Vec<Value> = engine["targets"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|target| {
            let id = target["target_name"]
                .as_str()?
                .strip_prefix("BinaryCompatibility:")?;
            if matches!(id, "NotApplicable" | "NonElf" | "AnalysisUnavailable") {
                return None;
            }
            let path = target["file_path"].as_str()?;
            let suffix = format!("/{path}");
            let facts = artifacts
                .iter()
                .find(|(key, _)| key.as_str() == path || key.ends_with(&suffix))
                .map(|(_, facts)| facts)?;
            Some(json!({
                "id": id, "path": path, "class": facts["elf_class"], "machine": facts["machine"],
                "type": facts["elf_type"], "needed": facts["needed"], "rpath": facts["rpath"],
                "runpath": facts["runpath"],
                "versions": {"GLIBC": facts["glibc"], "GLIBCXX": facts["glibcxx"],
                    "CXXABI": facts["cxxabi"]},
                "stripped": facts["stripped"],
            }))
        })
        .collect();
    let elf =
        json!({"state": engine["evidence"], "artifacts_checked": facts.len(), "facts": facts});
    let extra = &mut engine["extra"];
    extra["elf"] = elf;
    if let Some(fields) = extra.as_object_mut() {
        fields.retain(|key, _| {
            matches!(
                key.as_str(),
                "elf" | "native_checks" | "target_scopes" | "finding_identity_map"
            )
        });
    }
}

#[cfg(test)]
mod frozen_binary_compat_tests {
    use super::*;

    /// Positive: one fact per inspected ELF target in target order, frozen field names and the
    /// engine evidence as `state`; the non-ELF SKIP and the native artifact/header/step keys are
    /// left out. Negative: a not-applicable run has no facts. The readelf version comes from the
    /// complete suite capability, else "".
    #[test]
    fn binary_compat_extra_and_readelf_version_take_the_frozen_shape() {
        let facts = json!({"cxxabi": [], "dynamic": true, "elf_class": "ELF64", "elf_type": "DYN",
            "glibc": ["2.2.5"], "glibcxx": [], "machine": "X86-64", "needed": ["libc.so.6"],
            "rpath": [], "runpath": ["$ORIGIN"], "stripped": false});
        let target = |name: &str, path: &str, status: &str| json!({"target_name": name, "file_path": path, "status": status});
        let mut engine = json!({"evidence": "MEASURED",
            "targets": [target("BinaryCompatibility:release:shadow:app", "app", "PASS"),
                target("BinaryCompatibility:NonElf", "tool.exe", "SKIP")],
            "extra": {"native_checks": [{"check_id": "cpp.binary-compat", "extra": {}}],
                "target_scopes": {}, "finding_identity_map": {}, "steps": [],
                "artifact_headers": {"build/ici-make/app": {"is_elf": true}},
                "artifacts": {"build/ici-make/app": facts}}});
        frozen_binary_compat_extra(&mut engine);
        assert_eq!(
            engine["extra"]["elf"],
            json!({"state": "MEASURED", "artifacts_checked": 1, "facts": [{
                "id": "release:shadow:app", "path": "app", "class": "ELF64", "machine": "X86-64",
                "type": "DYN", "needed": ["libc.so.6"], "rpath": [], "runpath": ["$ORIGIN"],
                "versions": {"GLIBC": ["2.2.5"], "GLIBCXX": [], "CXXABI": []}, "stripped": false}]})
        );
        let mut keys: Vec<&String> = engine["extra"].as_object().unwrap().keys().collect();
        keys.sort();
        assert_eq!(
            keys,
            [
                "elf",
                "finding_identity_map",
                "native_checks",
                "target_scopes"
            ]
        );
        let mut skipped = json!({"evidence": "NOT_APPLICABLE",
            "targets": [target("BinaryCompatibility:NotApplicable", ".", "SKIP")],
            "extra": {"native_checks": [{"check_id": "cpp.binary-compat", "extra": {}}]}});
        frozen_binary_compat_extra(&mut skipped);
        assert_eq!(
            skipped["extra"]["elf"],
            json!({"state": "NOT_APPLICABLE", "artifacts_checked": 0, "facts": []})
        );

        let record = json!({"name": "readelf binary compatibility", "version": ""});
        let finding = json!({"tool_name": "readelf binary compatibility", "tool_version": ""});
        for (complete, version) in [(true, "GNU readelf 2.46"), (false, "")] {
            let mut document = json!({
                "capability_inventory": {"tools": [{"name": "readelf", "available": true,
                    "complete": complete, "version": "GNU readelf 2.46"}]},
                "results": [{"engine_name": "binary_compat", "tool_evidence": [record.clone()],
                    "findings": [finding.clone()]}]});
            frozen_readelf_version(&mut document);
            assert_eq!(
                document["results"][0]["tool_evidence"][0]["version"],
                version
            );
            assert_eq!(
                document["results"][0]["findings"][0]["tool_version"],
                version
            );
        }
    }
}
