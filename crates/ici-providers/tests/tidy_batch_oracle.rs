//! Real clang-tidy 21 `-p` batch transcripts of frozen `ici next verify` cpp.tidy
//! (`tests/rust-parity/cpp/capture_tidy_batch.py`, one process per component) replayed through the
//! native next tidy observer.
//!
//! The expectations are literals read off the raw transcripts, not copied from the frozen result:
//! frozen fails every batch whose cumulative "N warnings generated." summary repeats, while the
//! R3 correction accepts exactly that real shape, and the merge of an identical primary keeps
//! its notes.
use ici_domain::{TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::{
    ProviderKind, ProviderPlan, ProviderRequest, observe_full, plan,
    resolver::{ResolvedTool, ToolSource},
};
use serde_json::Value;
use std::{collections::BTreeSet, fs};

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/tidy-batch-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    assert!(
        oracle["clang_tidy_version"]
            .as_str()
            .unwrap()
            .contains("LLVM version 21.1.8")
    );
    oracle
}

/// The case's workspace on disk (relative spellings are resolved against it) and the native
/// next tidy plan of its frozen argv.
fn planned(case: &Value, next: bool) -> (tempfile::TempDir, ProviderPlan) {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().canonicalize().unwrap().join("project");
    for (name, text) in case["files"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    let call = &case["calls"][0];
    let argv: Vec<&str> = call["argv"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item.as_str().unwrap())
        .collect();
    assert_eq!(argv[0], "<base>/bin/clang-tidy");
    assert_eq!(argv[1..3], ["-p", "<root>"]);
    assert_eq!(call["cwd"], "<root>");
    let sources: Vec<String> = argv[3..]
        .iter()
        .map(|item| item.strip_prefix("<root>/").unwrap().to_owned())
        .collect();
    let tool = ResolvedTool {
        id: "clang-tidy".into(),
        path: "/usr/bin/clang-tidy".into(),
        source: ToolSource::Explicit,
        digest: sha256("clang-tidy"),
        version: None,
        configuration: vec![],
    };
    let mut request = ProviderRequest::new(
        ProviderKind::ClangTidy,
        tool,
        &root,
        &root,
        sources.clone(),
        "app.cpp.tidy",
    );
    request.options.database_dir = Some(root.display().to_string());
    request.options.next_tidy = next;
    let mut planned = plan(&request).unwrap();
    let directories: BTreeSet<_> = case["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| {
            let directory = row[0].as_str().unwrap();
            if directory == "." {
                root.clone()
            } else {
                root.join(directory)
            }
        })
        .collect();
    planned.context.command_directories = directories.into_iter().collect();
    // The native argv is the frozen one element by element.
    let mut expected = vec!["/usr/bin/clang-tidy".to_owned(), "-p".into()];
    expected.push(root.display().to_string());
    expected.extend(
        sources
            .iter()
            .map(|source| root.join(source).display().to_string()),
    );
    assert_eq!(planned.task.argv, expected);
    assert_eq!(planned.task.cwd, root.display().to_string());
    assert_eq!(planned.task.timeout_seconds, Some(900.0));
    assert!(!planned.task.cacheable);
    // The CLI attaches every snapshotted workspace file, headers included.
    let inputs: Vec<_> = case["files"]
        .as_object()
        .unwrap()
        .keys()
        .filter(|path| path.ends_with(".cpp") || path.ends_with(".h"))
        .map(|path| ici_domain::SourceInput {
            path: path.clone(),
            bytes: fs::read(root.join(path)).unwrap(),
        })
        .collect();
    ici_providers::attach_sources(&mut planned, ici_providers::source_coordinates(&inputs));
    (work, planned)
}

fn outcome(planned: &ProviderPlan, case: &Value, stdout: &str, stderr: &str) -> TaskOutcome {
    let root = planned.context.workspace_root.display().to_string();
    let base = planned
        .context
        .workspace_root
        .parent()
        .unwrap()
        .display()
        .to_string();
    let text = |value: &str| value.replace("<root>", &root).replace("<base>", &base);
    let mut outcome = TaskOutcome::empty(&planned.task.id);
    outcome.state = TaskState::Succeeded;
    outcome.exit_code = Some(case["calls"][0]["exit"].as_i64().unwrap() as i32);
    outcome.stdout = text(stdout).into_bytes();
    outcome.stderr = text(stderr).into_bytes();
    outcome
}

type Row = (String, String, u32, Option<u32>, String, Vec<String>);

fn observe(
    case: &Value,
    stdout: &str,
    stderr: &str,
    next: bool,
) -> (TaskState, Vec<Row>, Vec<String>) {
    let (_work, planned) = planned(case, next);
    let output = observe_full(&planned, &outcome(&planned, case, stdout, stderr));
    let rows = output
        .observation
        .findings
        .iter()
        .map(|finding| {
            (
                finding.rule_id.clone(),
                finding.primary_location.path.clone(),
                finding.primary_location.start_line,
                finding.primary_location.start_column,
                finding.message.clone(),
                finding
                    .related_locations
                    .iter()
                    .map(|span| {
                        format!(
                            "{}:{}:{} {}",
                            span.path,
                            span.start_line,
                            span.start_column.unwrap_or(0),
                            span.label
                        )
                    })
                    .collect(),
            )
        })
        .collect();
    (
        output.observation.state,
        rows,
        output.observation.limitations.clone(),
    )
}

fn replay(case: &Value) -> (TaskState, Vec<Row>, Vec<String>) {
    let call = &case["calls"][0];
    observe(
        case,
        call["stdout"].as_str().unwrap(),
        call["stderr"].as_str().unwrap(),
        true,
    )
}

fn row(rule: &str, path: &str, line: u32, column: u32, message: &str, notes: &[&str]) -> Row {
    (
        format!("clang_tidy.{rule}"),
        path.into(),
        line,
        Some(column),
        message.into(),
        notes.iter().map(|note| (*note).to_owned()).collect(),
    )
}

const DIVIDE: &str = "clang-analyzer-core.DivideZero";
const BY_ZERO: &str = "warning: Division by zero";

#[test]
fn real_batches_are_one_finding_per_primary_with_the_frozen_identity() {
    let oracle = oracle();
    let cases = &oracle["cases"];
    let succeeded = |name: &str, expected: Vec<Row>| {
        let (state, rows, limitations) = replay(&cases[name]);
        assert_eq!(state, TaskState::Succeeded, "{name}: {limitations:?}");
        assert_eq!(rows, expected, "{name}");
    };
    succeeded("clean", vec![]);
    succeeded("stdlib", vec![]);
    succeeded(
        "default",
        vec![
            row(
                DIVIDE,
                "app/a.cpp",
                1,
                36,
                BY_ZERO,
                &[
                    "app/a.cpp:1:16 note: 'z' initialized to 0",
                    "app/a.cpp:1:36 note: Division by zero",
                ],
            ),
            row(
                "clang-diagnostic-unused-variable",
                "app/a.cpp",
                2,
                15,
                "warning: unused variable 'unused'",
                &[],
            ),
        ],
    );
    succeeded(
        "config",
        vec![row(
            "readability-braces-around-statements",
            "app/a.cpp",
            1,
            22,
            "warning: statement should be inside braces",
            &[],
        )],
    );
    // Corrections: frozen FAILED each of these on the repeated cumulative summary.
    succeeded(
        "nested-config",
        vec![row(
            DIVIDE,
            "app/sub/b.cpp",
            1,
            53,
            BY_ZERO,
            &[
                "app/sub/b.cpp:1:20 note: Assuming 'x' is 0",
                "app/sub/b.cpp:1:16 note: Taking false branch",
                "app/sub/b.cpp:1:33 note: 'z' initialized to 0",
                "app/sub/b.cpp:1:53 note: Division by zero",
            ],
        )],
    );
    // The shared header's warning is printed once, with the first TU's path (clang-tidy keeps
    // one of the identical diagnostics of the two TUs; the other TU's notes are not printed).
    succeeded(
        "shared-header",
        vec![row(
            DIVIDE,
            "app/h.h",
            1,
            46,
            BY_ZERO,
            &[
                "app/a.cpp:2:18 note: Calling 'div0'",
                "app/h.h:1:26 note: 'z' initialized to 0",
                "app/h.h:1:46 note: Division by zero",
            ],
        )],
    );
    // The same warning text at two places in two sources stays two findings.
    succeeded(
        "distinct",
        vec![
            row(
                DIVIDE,
                "app/a.cpp",
                1,
                36,
                BY_ZERO,
                &[
                    "app/a.cpp:1:16 note: 'z' initialized to 0",
                    "app/a.cpp:1:36 note: Division by zero",
                ],
            ),
            row(
                DIVIDE,
                "app/b.cpp",
                2,
                36,
                BY_ZERO,
                &[
                    "app/b.cpp:2:16 note: 'z' initialized to 0",
                    "app/b.cpp:2:36 note: Division by zero",
                ],
            ),
        ],
    );
    succeeded(
        "repeat-after-clean",
        vec![row(
            DIVIDE,
            "app/b.cpp",
            1,
            36,
            BY_ZERO,
            &[
                "app/b.cpp:1:16 note: 'z' initialized to 0",
                "app/b.cpp:1:36 note: Division by zero",
            ],
        )],
    );
    // Each row's own macro, include directory and working directory: `b.cpp` is spelled
    // relative to `app/sub`, the directory of its command, and located there.
    succeeded(
        "flags-cwd",
        vec![
            row(
                DIVIDE,
                "app/a.cpp",
                2,
                37,
                BY_ZERO,
                &[
                    "app/a.cpp:2:17 note: 'z' initialized to 0",
                    "app/a.cpp:2:37 note: Division by zero",
                ],
            ),
            row(
                DIVIDE,
                "app/sub/b.cpp",
                3,
                26,
                BY_ZERO,
                &["app/sub/b.cpp:3:26 note: Division by zero"],
            ),
            row(
                "clang-diagnostic-division-by-zero",
                "app/sub/b.cpp",
                3,
                26,
                "warning: remainder by zero is undefined",
                &[],
            ),
        ],
    );
    // One source with two rows is passed twice like frozen: four commands, `[3/2]` and `[4/2]`.
    succeeded(
        "two-rows",
        vec![
            row(
                DIVIDE,
                "app/m.cpp",
                2,
                37,
                BY_ZERO,
                &[
                    "app/m.cpp:2:17 note: 'z' initialized to 0",
                    "app/m.cpp:2:37 note: Division by zero",
                ],
            ),
            row(
                DIVIDE,
                "app/m.cpp",
                5,
                37,
                BY_ZERO,
                &[
                    "app/m.cpp:5:17 note: 'z' initialized to 0",
                    "app/m.cpp:5:37 note: Division by zero",
                ],
            ),
        ],
    );
    // A real nonzero exit stays FAILED with frozen's limitation and no partial findings.
    let (state, rows, limitations) = replay(&cases["broken"]);
    assert_eq!(state, TaskState::Failed);
    assert!(rows.is_empty(), "{rows:?}");
    assert_eq!(limitations[0], "app.cpp.tidy failed with exit code 1");
    assert!(
        limitations
            .iter()
            .any(|item| item.contains("Error while processing ")),
        "the raw stderr stays as evidence: {limitations:?}"
    );
}

#[test]
fn the_raw_frozen_identity_of_a_real_batch_finding() {
    let oracle = oracle();
    let (_work, planned) = planned(&oracle["cases"]["default"], true);
    let call = &oracle["cases"]["default"]["calls"][0];
    let output = observe_full(
        &planned,
        &outcome(
            &planned,
            &oracle["cases"]["default"],
            call["stdout"].as_str().unwrap(),
            call["stderr"].as_str().unwrap(),
        ),
    );
    // sha256("clang-tidy\0clang-analyzer-core.DivideZero\0app/a.cpp\01\036\0warning: Division by
    // zero"), frozen `tidy.py::_fingerprint`.
    assert_eq!(
        output.observation.findings[0].fingerprint,
        "sha256:f1e50a118ab79cd3e62a4ad4f9665393b290e78f37aec2121890936b043be98c"
    );
}

/// Only the real shape of repeated summaries is accepted; every other repetition, a missing
/// summary or an unaccounted one still fails the parse.
#[test]
fn summary_shapes_outside_the_real_batch_still_fail() {
    let oracle = oracle();
    let distinct = &oracle["cases"]["distinct"];
    let stdout = distinct["calls"][0]["stdout"].as_str().unwrap();
    let progress =
        |index: usize, name: &str| format!("[{index}/3] Processing file <root>/app/{name}.cpp.");
    let (p1, p2, p3) = (progress(1, "a"), progress(2, "b"), progress(3, "c"));
    let (p1, p2, p3) = (p1.as_str(), p2.as_str(), p3.as_str());
    let (one, two) = ("1 warning generated.", "2 warnings generated.");
    let trailer = "Suppressed 1 warnings (1 in non-user code).";
    let failing: Vec<(Vec<&str>, &str)> = vec![
        (
            vec![p1, two, p2, one, p3, two],
            "clang-tidy generated-warning total decreased",
        ),
        (
            vec![p1, one, one, p2, two, p3, two],
            "clang-tidy emitted duplicate generated-warning summaries",
        ),
        (
            vec![p1, one, p2, two, p3],
            "clang-tidy command after a warning has no generated-warning summary",
        ),
        (
            vec![p1, one, p2, p3, two],
            "clang-tidy command after a warning has no generated-warning summary",
        ),
        (
            vec![one, p1, one, p2, two, p3, two],
            "clang-tidy generated-warning summary precedes the first processed file",
        ),
        (
            vec![p1, one, p2, two, p3, two, trailer, trailer],
            "clang-tidy emitted duplicate suppression summaries",
        ),
        (
            vec![p1, one, trailer, p2, two, p3, two],
            "clang-tidy suppression summary precedes a processed file",
        ),
        (
            vec![
                p1,
                one,
                p2,
                two,
                p3,
                "99999999999999999999 warnings generated.",
            ],
            "clang-tidy generated-warning count is out of range",
        ),
        (
            vec![p1, one, p2, two, p3, two, "something else"],
            "unrecognized C++ diagnostic output: something else",
        ),
    ];
    for (lines, reason) in failing {
        let stderr = lines.join("\n") + "\n";
        let (state, rows, limitations) = observe(distinct, stdout, &stderr, true);
        assert_eq!(state, TaskState::Failed, "{stderr}");
        assert!(rows.is_empty());
        assert_eq!(
            limitations[0],
            format!("cannot parse clang-tidy diagnostics: {reason}"),
            "{stderr}"
        );
    }
    // Positive control: the real stderr, and the same with a suppression trailer at the end.
    for lines in [
        vec![p1, one, p2, two, p3, two],
        vec![p1, one, p2, two, p3, two, trailer],
    ] {
        let stderr = lines.join("\n") + "\n";
        let (state, rows, _) = observe(distinct, stdout, &stderr, true);
        assert_eq!(state, TaskState::Succeeded, "{stderr}");
        assert_eq!(rows.len(), 2);
    }
    // One source (no progress lines): frozen's single summary rule stands.
    let default = &oracle["cases"]["default"];
    let stdout = default["calls"][0]["stdout"].as_str().unwrap();
    let (state, _, limitations) = observe(
        default,
        stdout,
        "2 warnings generated.\n2 warnings generated.\n",
        true,
    );
    assert_eq!(state, TaskState::Failed);
    assert_eq!(
        limitations[0],
        "cannot parse clang-tidy diagnostics: clang-tidy emitted duplicate generated-warning summaries"
    );
    // A summary that no diagnostic or suppression accounts for (frozen accounting).
    let (state, _, limitations) = observe(distinct, "", &format!("{p1}\n{one}\n"), true);
    assert_eq!(state, TaskState::Failed);
    assert_eq!(
        limitations[0],
        "cannot parse clang-tidy diagnostics: clang-tidy generated-warning summary has no diagnostic accounting"
    );
}

/// An identical primary repeated in one next batch is one finding that keeps the notes it did
/// not already have, in transcript order; a different primary at the same place stays apart,
/// and the merge is the next batch's only.
#[test]
fn an_identical_primary_is_one_finding_with_every_distinct_note() {
    let oracle = oracle();
    let shared = &oracle["cases"]["shared-header"];
    let stdout = "app/h.h:1:46: warning: Division by zero [clang-analyzer-core.DivideZero]\n\
                  app/a.cpp:2:18: note: Calling 'div0'\n\
                  app/h.h:1:46: note: Division by zero\n\
                  app/h.h:1:46: warning: Division by zero [clang-analyzer-core.DivideZero]\n\
                  app/b.cpp:2:18: note: Calling 'div0'\n\
                  app/h.h:1:46: note: Division by zero\n\
                  app/h.h:1:46: warning: Division by zero [clang-analyzer-core.DivisionZero]\n\
                  app/h.h:1:46: warning: Division by nothing [clang-analyzer-core.DivideZero]\n";
    let stderr = "[1/2] Processing file <root>/app/a.cpp.\n2 warnings generated.\n\
                  [2/2] Processing file <root>/app/b.cpp.\n4 warnings generated.\n";
    let (state, rows, limitations) = observe(shared, stdout, stderr, true);
    assert_eq!(state, TaskState::Succeeded, "{limitations:?}");
    assert_eq!(
        rows,
        vec![
            row(
                DIVIDE,
                "app/h.h",
                1,
                46,
                BY_ZERO,
                &[
                    "app/a.cpp:2:18 note: Calling 'div0'",
                    "app/h.h:1:46 note: Division by zero",
                    "app/b.cpp:2:18 note: Calling 'div0'",
                ]
            ),
            row(
                "clang-analyzer-core.DivisionZero",
                "app/h.h",
                1,
                46,
                BY_ZERO,
                &[]
            ),
            row(
                DIVIDE,
                "app/h.h",
                1,
                46,
                "warning: Division by nothing",
                &[]
            ),
        ]
    );
    let (_, rows, _) = observe(shared, stdout, stderr, false);
    assert_eq!(rows.len(), 4, "outside the next batch every primary stays");
}

/// A relative spelling that names a file under several of the batch's command directories,
/// or under none, is not attributed to either: the parse fails instead of guessing.
#[test]
fn a_relative_location_of_a_batch_is_the_one_file_it_names() {
    let oracle = oracle();
    let flags = &oracle["cases"]["flags-cwd"];
    let (_work, planned) = planned(flags, true);
    let root = planned.context.workspace_root.clone();
    // `b.cpp` now exists under both command directories.
    fs::write(root.join("b.cpp"), "int other();\n").unwrap();
    let call = &flags["calls"][0];
    let output = observe_full(
        &planned,
        &outcome(
            &planned,
            flags,
            call["stdout"].as_str().unwrap(),
            call["stderr"].as_str().unwrap(),
        ),
    );
    assert_eq!(output.observation.state, TaskState::Failed);
    assert_eq!(
        output.observation.limitations[0],
        "cannot parse clang-tidy diagnostics: relative clang-tidy location b.cpp names 2 files under the batch's compile directories"
    );
    fs::remove_file(root.join("b.cpp")).unwrap();
    fs::remove_file(root.join("app/sub/b.cpp")).unwrap();
    let output = observe_full(
        &planned,
        &outcome(
            &planned,
            flags,
            call["stdout"].as_str().unwrap(),
            call["stderr"].as_str().unwrap(),
        ),
    );
    assert_eq!(output.observation.state, TaskState::Failed);
    assert!(
        output.observation.limitations[0]
            .ends_with("names 0 files under the batch's compile directories"),
        "{:?}",
        output.observation.limitations
    );
}
