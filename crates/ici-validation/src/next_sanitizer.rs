//! Frozen next sanitizer suite contract for the per-suite steps of
//! `application/testing.expand_cpp_sanitizer`: the instrumentation evidence
//! (`workspace/instrumentation.sanitizer_marked`, `ctest_binaries`) the plan gate reads, and the
//! `adapters/providers/sanitize.py` run — the variant's runtime options appended to the
//! inherited ones, an unfinished run never parsed, a sanitizer report normalized into findings
//! (or refused with frozen's wording), a suite failure without a report, and one
//! `<variant>.diagnostics` count per suite.
use crate::{ValidationError, ValidationKind, ValidationPlan, planning};
use ici_domain::{AnalysisOutput, EvidenceLevel, Finding, Measurement, SourceSpan, TaskKind};
use ici_execution::TaskOutcome;
use regex::Regex;
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

/// Frozen `instrumentation.MAX_BINARY_BYTES`: a larger binary is not scanned.
const MAX_BINARY_BYTES: u64 = 256 * 1024 * 1024;
/// Frozen `_MAX_TESTFILE_BYTES`: each generated CTestTestfile is read to this prefix + 1.
const MAX_TESTFILE_BYTES: usize = 1024 * 1024;
/// Frozen `SanitizeProvider.plan` timeout and the executor's default output bound.
const TIMEOUT_SECONDS: u32 = 1800;
const OUTPUT_LIMIT: u64 = 1_000_000;

/// Frozen `_MARKERS[variant]`: the runtime's shared object name and init/handler symbols.
fn markers(variant: &str) -> &'static [&'static str] {
    match variant {
        "sanitize" => &["libasan", "libubsan", "__asan_init", "__ubsan_handle"],
        "thread-sanitize" => &["libtsan", "__tsan_init"],
        _ => &[],
    }
}

/// Frozen `sanitizer_marked(binary, variant)`: `Some(true)` when an ELF file carries one of the
/// variant's markers, `Some(false)` when the file was read fully and has none (a non-ELF file
/// cannot carry instrumentation), `None` when the claim cannot be checked (unknown variant,
/// unreadable, empty or larger than 256 MiB).
pub fn sanitizer_marked(binary: &Path, variant: &str) -> Option<bool> {
    let markers = markers(variant);
    if markers.is_empty() {
        return None;
    }
    let size = std::fs::metadata(binary).ok()?.len();
    if size == 0 || size > MAX_BINARY_BYTES {
        return None;
    }
    let payload = std::fs::read(binary).ok()?;
    if !payload.starts_with(b"\x7fELF") {
        return Some(false);
    }
    let pattern = markers
        .iter()
        .map(|marker| regex::escape(marker))
        .collect::<Vec<_>>()
        .join("|");
    Some(
        regex::bytes::Regex::new(&pattern)
            .expect("literal markers")
            .is_match(&payload),
    )
}

/// Frozen `ctest_binaries(root, build_dir)`: the existing files every generated
/// `CTestTestfile.cmake` under the build directory names as an `add_test` command (relative
/// commands resolve against the file's own directory), in sorted test-file order, each once.
/// Like Python 3.10 `rglob`, directory symlinks are not followed.
pub fn ctest_binaries(root: &Path, build_dir: &str) -> Vec<PathBuf> {
    let base = root.join(build_dir);
    if !base.is_dir() {
        return vec![];
    }
    let mut test_files = vec![];
    let mut pending = vec![base];
    while let Some(directory) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(&directory) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            if kind.is_dir() {
                pending.push(path);
            } else if entry.file_name() == "CTestTestfile.cmake" {
                test_files.push(path);
            }
        }
    }
    // Python orders `Path` objects by their parts.
    test_files.sort_by(|left, right| left.components().cmp(right.components()));
    let add_test = Regex::new(r#"add_test\s*\(\s*(?:\[=*\[)?[^\s\)\]"]+\]?=*\s*"?([^\s")]+)"#)
        .expect("constant regex");
    let mut found: Vec<PathBuf> = vec![];
    for test_file in test_files {
        let Ok(bytes) = std::fs::read(&test_file) else {
            continue;
        };
        let prefix = &bytes[..bytes.len().min(MAX_TESTFILE_BYTES + 1)];
        let text = String::from_utf8_lossy(prefix);
        for capture in add_test.captures_iter(&text) {
            let command = Path::new(&capture[1]);
            let candidate = if command.is_absolute() {
                command.to_path_buf()
            } else {
                test_file.parent().unwrap_or(Path::new("")).join(command)
            };
            if candidate.is_file() && !found.contains(&candidate) {
                found.push(candidate);
            }
        }
    }
    found
}

/// Frozen `SanitizeProvider.plan`: one ANALYZE run of the suite (`options.argv`, already
/// absolute) in the component root under the variant's options overlay, 1800 s, the default
/// 1,000,000-character output bound, never cached.
pub(crate) fn plan(plan: &mut ValidationPlan) -> Result<(), ValidationError> {
    let request = &plan.context;
    if request.options.argv.is_empty() {
        return Err(ValidationError(
            "frozen next sanitizer step names no suite argv".into(),
        ));
    }
    let mut task = planning::task(request, "suite", vec![]);
    task.id = crate::next_suite::step_id(request);
    task.argv.clone_from(&request.options.argv);
    task.kind = TaskKind::Analyze;
    task.timeout_seconds = Some(f64::from(TIMEOUT_SECONDS));
    task.output_limit_bytes = Some(OUTPUT_LIMIT as usize);
    task.cacheable = false;
    plan.tasks.push(task);
    Ok(())
}

/// Frozen `SanitizeProvider` option overlay: each variant option appended to the inherited
/// value with `:` (`_env`), read from the run's environment.
pub fn option_overlay(
    kind: ValidationKind,
    environment: &BTreeMap<String, String>,
) -> BTreeMap<String, String> {
    let options: &[(&str, &str)] = if kind == ValidationKind::ThreadSanitizer {
        &[("TSAN_OPTIONS", "halt_on_error=0")]
    } else {
        &[
            ("ASAN_OPTIONS", "detect_leaks=1"),
            ("UBSAN_OPTIONS", "halt_on_error=1"),
        ]
    };
    options
        .iter()
        .map(|(key, option)| {
            let inherited = environment.get(*key).map(String::as_str).unwrap_or("");
            let value = [inherited, option]
                .into_iter()
                .filter(|part| !part.is_empty())
                .collect::<Vec<_>>()
                .join(":");
            ((*key).to_string(), value)
        })
        .collect()
}

/// Frozen `_anchor(spec)`: the run's cwd relative to the project when it is not the root, else
/// the first argv path under the project, else the executable's file name.
fn anchor(plan: &ValidationPlan, argv: &[String], cwd: &str) -> String {
    let root = &plan.context.root;
    for candidate in std::iter::once(cwd).chain(argv.iter().map(String::as_str)) {
        // Python `relative_to` is lexical: a relative argument is never under the root.
        if let Ok(relative) = Path::new(candidate).strip_prefix(root) {
            let relative = relative.to_string_lossy().replace('\\', "/");
            if !relative.is_empty() && relative != "." {
                return relative;
            }
        }
    }
    argv.first()
        .and_then(|executable| Path::new(executable).file_name())
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// Frozen `MAX_SANITIZER_SOURCE_BYTES`.
const MAX_SOURCE_BYTES: u64 = 8 * 1024 * 1024;

/// `plan` with the project files a sanitizer report names indexed as well.
pub(crate) fn with_named_sources<'a>(
    plan: &'a ValidationPlan,
    transcript: &str,
) -> std::borrow::Cow<'a, ValidationPlan> {
    let additional = named_project_sources(plan, transcript);
    if additional.is_empty() {
        return std::borrow::Cow::Borrowed(plan);
    }
    let mut copy = plan.clone();
    copy.source_coordinates = plan.source_coordinates.with_additional(&additional);
    std::borrow::Cow::Owned(copy)
}

/// Project files a sanitizer report line names (the last location of each frame, runtime-error
/// and ERROR/WARNING/SUMMARY line, as the parser reads them) that the run's snapshot does not
/// hold — a test helper or vendored file outside the component's sources. Frozen
/// `_safe_source_location` reads any project file a frame names; these are read the same
/// bounded way — lexically under the project, no symlink below the root, a regular file of at
/// most 8 MiB without NUL bytes — so their lines are confirmed like snapshot sources. A file
/// that cannot be read names no location, as in frozen. All such reads of one report share one
/// 8 MiB budget (frozen has none; a report naming more is located only up to it).
fn named_project_sources(plan: &ValidationPlan, transcript: &str) -> Vec<ici_domain::SourceInput> {
    use std::io::Read;
    let mut inputs: Vec<ici_domain::SourceInput> = vec![];
    let mut seen = std::collections::BTreeSet::new();
    let mut budget = MAX_SOURCE_BYTES;
    for path in crate::sanitizer::named_project_paths(transcript, &plan.context.root) {
        if plan.source_coordinates.holds(&path) || !seen.insert(path.clone()) {
            continue;
        }
        let Some(mut file) = open_contained(&plan.context.root, &path) else {
            continue;
        };
        let Ok(metadata) = file.metadata() else {
            continue;
        };
        if !metadata.is_file() || metadata.len() > MAX_SOURCE_BYTES.min(budget) {
            continue;
        }
        let mut bytes = vec![];
        if (&mut file)
            .take(MAX_SOURCE_BYTES + 1)
            .read_to_end(&mut bytes)
            .is_err()
            || bytes.len() as u64 > MAX_SOURCE_BYTES.min(budget)
            || bytes.contains(&0)
        {
            continue;
        }
        budget -= bytes.len() as u64;
        inputs.push(ici_domain::SourceInput { path, bytes });
    }
    inputs
}

/// Frozen `_open_contained`: the project root opened as given, every component under it with
/// `O_NOFOLLOW`, the file itself non-blocking (a FIFO never blocks the read).
fn open_contained(root: &Path, relative: &str) -> Option<std::fs::File> {
    use nix::{
        fcntl::{OFlag, open, openat},
        sys::stat::Mode,
    };
    let directory_flags = OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_CLOEXEC;
    let mut directory = open(root, directory_flags, Mode::empty()).ok()?;
    let mut parts: Vec<&str> = relative.split('/').collect();
    let name = parts.pop().filter(|name| !name.is_empty())?;
    for part in parts {
        directory = openat(
            &directory,
            part,
            directory_flags | OFlag::O_NOFOLLOW,
            Mode::empty(),
        )
        .ok()?;
    }
    let descriptor = openat(
        &directory,
        name,
        OFlag::O_RDONLY | OFlag::O_NOFOLLOW | OFlag::O_NONBLOCK | OFlag::O_CLOEXEC,
        Mode::empty(),
    )
    .ok()?;
    Some(std::fs::File::from(descriptor))
}

/// Frozen `any(marker.match(line) for line in transcript.splitlines() for marker in _MARKERS)`.
fn has_report_marker(transcript: &str) -> bool {
    crate::make_check::lines(transcript)
        .into_iter()
        .any(crate::sanitizer::is_report_marker)
}

pub(crate) fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let step = crate::next_suite::step_id(&plan.context);
    let task = plan
        .tasks
        .iter()
        .find(|task| task.id == step)
        .ok_or_else(|| ValidationError(format!("sanitizer step {step} absent")))?;
    let outcome = outcomes
        .get(&step)
        .ok_or_else(|| ValidationError(format!("sanitizer step {step} was not executed")))?;
    if let Some(reason) = crate::next_elf::unfinished(&step, outcome, TIMEOUT_SECONDS, OUTPUT_LIMIT)
    {
        return Err(ValidationError(reason));
    }
    let provider = plan.kind.provider();
    // The suite's protocol is stdout, the sanitizer transcript stderr: stderr first.
    let transcript = format!(
        "{}\n{}",
        String::from_utf8_lossy(&outcome.stderr),
        String::from_utf8_lossy(&outcome.stdout)
    );
    let measurement = |count: usize| Measurement {
        name: format!("{provider}.diagnostics"),
        value: count as f64,
        evidence: EvidenceLevel::Measured,
        unit: "diagnostics".into(),
        numerator: None,
        denominator: None,
    };
    let finding = |rule: &str, message: &str, location: SourceSpan, details: &[&str]| {
        let mut item = Finding::new(rule, message, location, provider);
        item.fingerprint = crate::next_suite::process_fingerprint(plan, provider, details);
        item.severity = "high".into();
        item.confidence = "high".into();
        item.evidence = EvidenceLevel::Measured;
        item.task_id = Some(step.clone());
        item.component_id = Some(plan.context.component_id.clone());
        item
    };
    let anchor = || SourceSpan::line(anchor(plan, &task.argv, &task.cwd), 1);
    if has_report_marker(&transcript) {
        let parse_plan = with_named_sources(plan, &transcript);
        let diagnostics =
            crate::sanitizer::parse_diagnostics(&transcript, &parse_plan).map_err(|error| {
                ValidationError(format!(
                    "sanitizer report could not be normalized: {}",
                    crate::sanitizer::frozen_parse_error(&transcript, &error.0)
                ))
            })?;
        if diagnostics.is_empty() {
            return Err(ValidationError(
                "transcript had a sanitizer report marker but no complete diagnostic".into(),
            ));
        }
        let count = diagnostics.len();
        for diagnostic in diagnostics {
            let located = diagnostic.primary_location.is_some();
            let mut item = finding(
                &diagnostic.rule_id,
                &diagnostic.message,
                diagnostic.primary_location.clone().unwrap_or_else(anchor),
                &[&diagnostic.rule_id, &diagnostic.message],
            );
            item.native_rule_id = diagnostic.defect.clone();
            item.related_locations = diagnostic
                .related_locations
                .into_iter()
                .filter(|span| span.path != "[external]")
                .collect();
            if !located {
                item.limitations = vec!["the diagnostic named no project location".into()];
            }
            output.observation.findings.push(item);
        }
        output.observation.measurements.push(measurement(count));
        output.summary = format!("{count} sanitizer diagnostics");
        return Ok(());
    }
    let code = outcome.exit_code.unwrap_or_default();
    if code != 0 {
        let exit = format!("exit-{code}");
        output.observation.findings.push(finding(
            "ici.sanitize.suite-failure",
            &format!(
                "suite exited {code} with no sanitizer diagnostic in its output \u{2014} the run failed without the sanitizer reporting a defect"
            ),
            anchor(),
            &["suite", &exit],
        ));
        output.summary = format!("suite exited {code}");
        return Ok(());
    }
    output.observation.measurements.push(measurement(0));
    output.summary = "0 sanitizer diagnostics".into();
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn write(path: &Path, data: &[u8]) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, data).unwrap();
    }

    /// Frozen `sanitizer_marked`: marker in an ELF (true), ELF without one or a script (false),
    /// empty or unreadable (unproven), the other variant's markers (false).
    #[test]
    fn sanitizer_marked_answers_three_ways_like_frozen() {
        let root = tempfile::tempdir().unwrap();
        let path = |name: &str| root.path().join(name);
        write(&path("asan"), b"\x7fELF....libasan.so.8\0");
        write(&path("ubsan"), b"\x7fELF__ubsan_handle_add_overflow");
        write(&path("tsan"), b"\x7fELF__tsan_init");
        write(&path("plain"), b"\x7fELF nothing");
        write(&path("script"), b"#!/bin/sh\n# libasan\n");
        write(&path("empty"), b"");
        assert_eq!(sanitizer_marked(&path("asan"), "sanitize"), Some(true));
        assert_eq!(sanitizer_marked(&path("ubsan"), "sanitize"), Some(true));
        assert_eq!(sanitizer_marked(&path("tsan"), "sanitize"), Some(false));
        assert_eq!(
            sanitizer_marked(&path("tsan"), "thread-sanitize"),
            Some(true)
        );
        assert_eq!(
            sanitizer_marked(&path("asan"), "thread-sanitize"),
            Some(false)
        );
        assert_eq!(sanitizer_marked(&path("plain"), "sanitize"), Some(false));
        assert_eq!(sanitizer_marked(&path("script"), "sanitize"), Some(false));
        assert_eq!(sanitizer_marked(&path("empty"), "sanitize"), None);
        assert_eq!(sanitizer_marked(&path("absent"), "sanitize"), None);
        assert_eq!(sanitizer_marked(&path("asan"), "coverage"), None);
        std::fs::set_permissions(path("asan"), std::fs::Permissions::from_mode(0o000)).unwrap();
        if std::fs::read(path("asan")).is_err() {
            assert_eq!(sanitizer_marked(&path("asan"), "sanitize"), None);
        }
    }

    /// Frozen `ctest_binaries`: every CTestTestfile under the build tree, plain and bracket
    /// test names as frozen's pattern reads them, relative commands against the file's
    /// directory, missing files dropped, each binary once.
    #[test]
    fn ctest_binaries_read_add_test_commands_like_frozen() {
        let root = tempfile::tempdir().unwrap();
        let base = root.path();
        write(
            &base.join("out/CTestTestfile.cmake"),
            format!(
                "subdirs(\"sub\")\nadd_test(unit \"{}/out/unit\")\nadd_test(gone \"gone\")\n\
                 add_test(again \"unit\")\n",
                base.display()
            )
            .as_bytes(),
        );
        write(
            &base.join("out/sub/CTestTestfile.cmake"),
            b"add_test(sub \"tool\" \"--x\")\nadd_test([=[bracket]=] \"tool\")\n",
        );
        write(&base.join("out/unit"), b"x");
        write(&base.join("out/sub/tool"), b"x");
        assert_eq!(
            ctest_binaries(base, "out"),
            vec![base.join("out/unit"), base.join("out/sub/tool")]
        );
        assert!(ctest_binaries(base, "missing").is_empty());
        // Frozen's pattern takes `]` as the command of a CMP0110 bracket name.
        write(
            &base.join("br/CTestTestfile.cmake"),
            format!("add_test([=[unit]=] \"{}/out/unit\")\n", base.display()).as_bytes(),
        );
        assert!(ctest_binaries(base, "br").is_empty());
    }

    #[test]
    fn option_overlay_appends_to_the_inherited_options() {
        let environment = BTreeMap::from([("ASAN_OPTIONS".to_string(), "abort=1".to_string())]);
        assert_eq!(
            option_overlay(ValidationKind::Sanitizer, &environment),
            BTreeMap::from([
                ("ASAN_OPTIONS".into(), "abort=1:detect_leaks=1".into()),
                ("UBSAN_OPTIONS".into(), "halt_on_error=1".into()),
            ])
        );
        assert_eq!(
            option_overlay(ValidationKind::ThreadSanitizer, &BTreeMap::new()),
            BTreeMap::from([("TSAN_OPTIONS".into(), "halt_on_error=0".into())])
        );
    }
}
