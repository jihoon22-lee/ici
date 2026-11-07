use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs};
fn fixture() -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"), "def useful():\n    return 1\n").unwrap();
    let mut config = "schema_version=2\n".to_string();
    for check in ici_domain::builtin_catalog()
        .checks
        .iter()
        .filter(|check| check.language == "python")
    {
        config.push_str(&format!(
            "[checks.\"{}\"]\nenabled={}\n",
            check.id,
            check.id == "python.line"
        ));
    }
    fs::write(root.path().join("ici.toml"), config).unwrap();
    root
}
fn call(
    root: &std::path::Path,
    args: &[&str],
) -> ici_cli::commands::Result<ici_cli::commands::CommandOutput> {
    commands::dispatch(
        &registry::parse(args.iter().copied()).unwrap(),
        root,
        &BTreeMap::from([("PATH".into(), String::new())]),
    )
}
#[test]
fn historical_family_and_verify_exports_use_one_native_result_without_python() {
    let root = fixture();
    let line = call(root.path(), &["ici", "line", "--report"]).unwrap();
    assert_eq!(line.exit_code, 0, "{} {}", line.stdout, line.stderr);
    let family: Value =
        serde_json::from_slice(&fs::read(root.path().join("line_report.json")).unwrap()).unwrap();
    assert_eq!(family["schema_version"], "ici.result/v3");
    assert_eq!(family["engine_name"], "line");
    let verify = call(
        root.path(),
        &[
            "ici",
            "verify",
            "--report",
            "--html",
            "report.html",
            "--sarif",
            "report.sarif",
            "--write-baseline",
            "baseline.json",
            "--github-summary",
        ],
    )
    .unwrap();
    assert_eq!(verify.exit_code, 0, "{} {}", verify.stdout, verify.stderr);
    assert!(verify.stdout.contains("Result:"));
    let baseline: Value =
        serde_json::from_slice(&fs::read(root.path().join("baseline.json")).unwrap()).unwrap();
    assert_eq!(baseline["native_run"]["schema_id"], "ici.next.run");
    assert!(
        fs::read_to_string(root.path().join("report.html"))
            .unwrap()
            .contains("main.py")
    );
    let compared = call(
        root.path(),
        &[
            "ici",
            "verify",
            "--baseline",
            "baseline.json",
            "--fail-on-new",
        ],
    )
    .unwrap();
    assert_eq!(
        compared.exit_code, 0,
        "{} {}",
        compared.stdout, compared.stderr
    );
    let diff = call(root.path(), &["ici", "diff", "baseline.json"]).unwrap();
    assert_eq!(diff.exit_code, 0, "{}", diff.stdout);
    assert!(diff.stdout.contains("0 new"));
    assert!(diff.stdout.contains("baseline baseline.json → result.json"));
}

#[test]
fn explicit_browser_and_github_summary_use_saved_outputs_and_preserve_existing_summary() {
    use std::os::unix::fs::PermissionsExt;
    let root = fixture();
    let summary = root.path().join("summary.md");
    fs::write(&summary, "earlier job\n").unwrap();
    let opener = root.path().join("xdg-open");
    fs::write(
        &opener,
        "#!/bin/sh\nprintf '%s\\n%s\\n' \"$1\" \"$DISPLAY\" > browser-invocation\n",
    )
    .unwrap();
    fs::set_permissions(&opener, fs::Permissions::from_mode(0o755)).unwrap();
    let environment = BTreeMap::from([
        ("PATH".into(), root.path().to_string_lossy().into_owned()),
        (
            "GITHUB_STEP_SUMMARY".into(),
            summary.to_string_lossy().into_owned(),
        ),
        ("DISPLAY".into(), ":7".into()),
    ]);
    let invocation = registry::parse(["ici", "verify", "--open", "--github-summary"]).unwrap();
    let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
    assert_eq!(output.exit_code, 0, "{} {}", output.stdout, output.stderr);
    // The detached opener's redirection creates the file before printf fills it.
    let limit = std::time::Instant::now() + std::time::Duration::from_secs(5);
    let mut opened = String::new();
    while std::time::Instant::now() < limit {
        opened = fs::read_to_string(root.path().join("browser-invocation")).unwrap_or_default();
        if opened.lines().count() == 2 {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    assert!(opened.contains("verify_report.html"), "{opened:?}");
    assert!(opened.contains(":7"));
    let summary = fs::read_to_string(summary).unwrap();
    assert!(summary.starts_with("earlier job\n"));
    assert!(summary.contains("line"));
    // The frozen historical layout, appended as `\n<markdown>\n` (legacy_summary.rs pins bytes).
    let appended = &summary["earlier job\n".len()..];
    assert!(appended.starts_with("\n## "), "{appended}");
    assert!(appended.contains("`ici` Verification Report — **`"));
    assert!(
        appended.contains("| Engine | Status | Summary | Score / Metrics | Cache | Duration |")
    );
    assert!(appended.contains("Detailed Targets & Locations"));
    assert!(appended.ends_with("\n"));
}
#[test]
fn output_aliases_and_unowned_destinations_are_refused_before_native_state() {
    for args in [
        vec!["ici", "verify", "--html", "main.py"],
        vec!["ici", "verify", "--html", "same", "--sarif", "same"],
        vec![
            "ici",
            "verify",
            "--report",
            "--write-baseline",
            "verify_report.json",
        ],
        vec!["ici", "verify", "--html", "user.html"],
    ] {
        let root = fixture();
        fs::write(root.path().join("user.html"), "user content").unwrap();
        assert!(call(root.path(), &args).is_err());
        assert!(!root.path().join(".ici").exists());
        assert_eq!(
            fs::read_to_string(root.path().join("user.html")).unwrap(),
            "user content"
        );
    }
}
#[test]
fn incompatible_historical_baseline_never_writes_requested_replacement() {
    let root = fixture();
    fs::write(
        root.path().join("old.json"),
        include_bytes!("../../../tests/fixtures/ici-next/legacy-v3-suite.json"),
    )
    .unwrap();
    let output = call(
        root.path(),
        &[
            "ici",
            "verify",
            "--baseline",
            "old.json",
            "--fail-on-new",
            "--write-baseline",
            "replacement.json",
        ],
    )
    .unwrap();
    // Frozen gate failure path: exit 1 with the incompatibility and the regenerate command.
    assert_eq!(output.exit_code, 1, "{}", output.stdout);
    assert!(
        output.stdout.contains("incompatible") || output.stdout.contains("regenerate it with"),
        "{}",
        output.stdout
    );
    assert!(!root.path().join("replacement.json").exists());
    assert!(
        root.path().join(ici_cli::DEFAULT_RESULT).exists(),
        "{}",
        output.stdout
    );
}

#[test]
fn legacy_annotation_evidence_survives_missing_type_tool_without_claiming_type_completion() {
    let root = legacy_project("[engines.type]\nwarn_on_missing_annotation=true\n");
    let output = call(root.path(), &["ici", "type", "--report"]).unwrap();
    // Frozen's AST fallback beside a missing mypy is a WARN, exit 0 (exit 3 was a schema 2 fixture
    // running the legacy mypy view).
    assert_eq!(output.exit_code, 0, "{} {}", output.stdout, output.stderr);
    let result: Value =
        serde_json::from_slice(&fs::read(root.path().join("type_report.json")).unwrap()).unwrap();
    assert!(result.to_string().contains("ESTIMATED"), "{result}");
    assert!(
        result
            .to_string()
            .contains("missing return type annotation"),
        "{result}"
    );
    let native =
        ici_execution::storage::read_result(&root.path().join(ici_cli::DEFAULT_RESULT)).unwrap();
    let checks = native.checks.unwrap();
    assert!(
        checks
            .iter()
            .any(|check| check.check_id == "python.annotations" && check.evidence_complete),
        "{checks:?}\n{result}"
    );
    assert!(
        checks
            .iter()
            .any(|check| check.check_id == "python.type" && !check.evidence_complete)
    );
}

fn legacy_project(extra: &str) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("src")).unwrap();
    fs::write(
        root.path().join("src/main.py"),
        "def f(x):\n    return x + 1\n",
    )
    .unwrap();
    fs::write(
        root.path().join("ici.toml"),
        format!("[project]\ntype='python'\nsource_dirs=['src']\n{extra}"),
    )
    .unwrap();
    root
}
fn engine<'a>(report: &'a Value, name: &str) -> Option<&'a Value> {
    report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == name)
}

/// Frozen `verify` runs the same engine bodies as `ici lint`/`ici type`: without Ruff and mypy
/// both fall back to their estimated AST passes, and `--profile fast` disables the descriptors
/// outside the fast profile (test, cognitive). The suite and exit follow those engine verdicts.
#[test]
fn verify_shapes_missing_ruff_and_mypy_like_the_family_commands_and_honors_profiles() {
    let root = legacy_project("");
    let output = call(root.path(), &["ici", "verify", "-r", "--profile", "fast"]).unwrap();
    assert_eq!(output.exit_code, 0, "{} {}", output.stdout, output.stderr);
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join("verify_report.json")).unwrap()).unwrap();
    assert_eq!(report["suite_status"], "WARN");
    let lint = engine(&report, "lint").unwrap();
    assert_eq!(
        (&lint["status"], &lint["evidence"], &lint["summary"]),
        (
            &Value::from("WARN"),
            &Value::from("ESTIMATED"),
            &Value::from("Ruff is unavailable; AST syntax fallback is ESTIMATED")
        )
    );
    let typed = engine(&report, "type").unwrap();
    assert_eq!(typed["evidence"], "ESTIMATED");
    assert!(
        typed["summary"]
            .as_str()
            .unwrap()
            .ends_with("Mypy is unavailable; AST fallback is ESTIMATED")
    );
    for excluded in ["test", "cognitive", "sanitize", "build"] {
        assert!(engine(&report, excluded).is_none(), "{excluded}");
    }
    assert_eq!(report["warned_count"], 2);
    // The family command gives the identical engine verdict.
    let single = call(root.path(), &["ici", "lint", "-r"]).unwrap();
    assert_eq!(single.exit_code, 0);
    let family: Value =
        serde_json::from_slice(&fs::read(root.path().join("lint_report.json")).unwrap()).unwrap();
    assert_eq!(family["summary"], lint["summary"]);
}

/// `ruff_required` keeps the frozen tool error in verify too: the suite is ERROR, never a pass.
#[test]
fn verify_required_missing_ruff_is_a_suite_error() {
    let root = legacy_project("[engines.lint]\nruff_required=true\n");
    let output = call(root.path(), &["ici", "verify", "-r", "--profile", "fast"]).unwrap();
    assert_ne!(output.exit_code, 0);
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join("verify_report.json")).unwrap()).unwrap();
    assert_eq!(report["suite_status"], "ERROR");
    let lint = engine(&report, "lint").unwrap();
    assert_eq!(lint["status"], "ERROR");
    assert_eq!(lint["evidence"], "NOT_RUN");
}
