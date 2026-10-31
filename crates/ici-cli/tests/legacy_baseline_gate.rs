//! Baseline decision (ADR-0008 "readable is not comparable"): native-written baselines compare
//! new/unchanged/resolved plus frozen severity regressions; pyz-era v3 baselines stay readable
//! but are refused per comparison with the exact regenerate command, never mass-reported as new,
//! and `--fail-on-new` against them fails like the frozen gate (exit 1).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const CHECKS: [&str; 16] = [
    "python.cognitive",
    "python.compat",
    "python.compat-runtime",
    "python.complexity",
    "python.coverage",
    "python.cycle",
    "python.dead",
    "python.dup",
    "python.exception",
    "python.format",
    "python.line",
    "python.lint",
    "python.resource",
    "python.security",
    "python.test",
    "python.type",
];

/// Only native checks without external tools; their findings are legacy-policy advisories, so
/// the run itself passes and the exit code isolates the baseline gate.
fn project() -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    let mut config = "schema_version=2\n".to_string();
    for check in CHECKS {
        let enabled = matches!(check, "python.line" | "python.complexity");
        config.push_str(&format!("[checks.\"{check}\"]\nenabled={enabled}\n"));
    }
    fs::write(root.path().join("ici.toml"), config).unwrap();
    write_source(root.path(), &["branchy"]);
    root
}
fn write_source(root: &Path, functions: &[&str]) {
    let mut source = String::new();
    for name in functions {
        source.push_str(&format!("def {name}(x):\n"));
        for value in 0..16 {
            source.push_str(&format!("    if x == {value}:\n        return {value}\n"));
        }
        source.push_str("    return -1\n\n");
    }
    fs::write(root.join("main.py"), source).unwrap();
}
fn call(root: &Path, args: &[&str]) -> commands::CommandOutput {
    commands::dispatch(
        &registry::parse(args.iter().copied()).unwrap(),
        root,
        &BTreeMap::from([("PATH".into(), String::new())]),
    )
    .unwrap()
}
/// Console lines wrap at the terminal width; compare their text without whitespace.
fn shows(stdout: &str, text: &str) -> bool {
    let flat = |value: &str| value.split_whitespace().collect::<String>();
    flat(stdout).contains(&flat(text))
}
fn json(path: &Path) -> Value {
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}
fn native(root: &Path) -> Value {
    json(&root.join(ici_cli::DEFAULT_RESULT))
}
fn delta(root: &Path) -> Value {
    json(&root.join("verify_report.json"))["baseline_comparison"].clone()
}

#[test]
fn native_baseline_compares_new_unchanged_resolved_and_frozen_severity_regressions() {
    let root = project();
    let written = call(
        root.path(),
        &["ici", "verify", "--write-baseline", "base.json"],
    );
    assert_eq!(written.exit_code, 0, "{}", written.stdout);
    let base = json(&root.path().join("base.json"));
    let fingerprints: Vec<String> = base["native_run"]["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| finding["fingerprint"].as_str().unwrap().to_owned())
        .collect();
    assert_eq!(fingerprints.len(), 1, "{base:#}");
    let compare = |baseline: &str| {
        call(
            root.path(),
            &[
                "ici",
                "verify",
                "--report",
                "--baseline",
                baseline,
                "--fail-on-new",
            ],
        )
    };

    // Same code: every finding unchanged, gate PASSED, exit 0.
    let same = compare("base.json");
    assert_eq!(same.exit_code, 0, "{}", same.stdout);
    assert_eq!(
        native(root.path())["baseline"]["unchanged"],
        json!(fingerprints)
    );
    let frozen = delta(root.path());
    assert_eq!(frozen["unchanged_count"], 1);
    assert_eq!(frozen["gate_failed"], false);
    assert!(
        shows(&same.stdout, "\"fail_on_new_gate\":\"PASSED\""),
        "{}",
        same.stdout
    );

    // A new complex function: one new gated finding fails --fail-on-new (exit 1).
    write_source(root.path(), &["branchy", "another"]);
    let grown = compare("base.json");
    assert_eq!(grown.exit_code, 1, "{}", grown.stdout);
    let frozen = delta(root.path());
    assert_eq!(frozen["new_count"], 1);
    assert_eq!(frozen["unchanged_count"], 1);
    assert_eq!(frozen["gated_count"], 1);
    assert_eq!(frozen["gate_failed"], true);
    assert!(
        shows(&grown.stdout, "NEW GATED complexity"),
        "{}",
        grown.stdout
    );

    // The original function removed: its finding is resolved (complete matching check scope).
    write_source(root.path(), &["another"]);
    let moved = compare("base.json");
    assert_eq!(
        native(root.path())["baseline"]["resolved"],
        json!(fingerprints)
    );
    let frozen = delta(root.path());
    assert_eq!(frozen["resolved_count"], 1);
    assert_eq!(frozen["new_count"], 1);
    assert_eq!(moved.exit_code, 1, "{}", moved.stdout);

    // Frozen `_is_regressed`: the same fingerprint at a higher severity than the baseline is an
    // unchanged but regressed, gated entry, so --fail-on-new fails although nothing is new.
    write_source(root.path(), &["branchy"]);
    let mut lower = base.clone();
    lower["native_run"]["findings"][0]["severity"] = json!("low");
    fs::write(root.path().join("lower.json"), lower.to_string()).unwrap();
    let regressed = compare("lower.json");
    assert_eq!(regressed.exit_code, 1, "{}", regressed.stdout);
    assert_eq!(native(root.path())["baseline"]["new"], json!([]));
    let frozen = delta(root.path());
    assert_eq!(frozen["regressed_count"], 1);
    assert_eq!(frozen["gated_count"], 1);
    let entry = &frozen["entries"][0];
    assert_eq!(entry["state"], "unchanged");
    assert_eq!(entry["baseline_severity"], "low");
    assert_eq!(entry["current_severity"], "medium");
    assert_eq!(entry["regressed"], true);
    assert!(
        shows(&regressed.stdout, "UNCHANGED GATED complexity")
            && shows(&regressed.stdout, "severity low → medium"),
        "{}",
        regressed.stdout
    );
    // A lower current severity is not a regression.
    let mut higher = base;
    higher["native_run"]["findings"][0]["severity"] = json!("high");
    fs::write(root.path().join("higher.json"), higher.to_string()).unwrap();
    let improved = compare("higher.json");
    assert_eq!(improved.exit_code, 0, "{}", improved.stdout);
    assert_eq!(delta(root.path())["regressed_count"], 0);

    // The next path: a saved next result is a baseline for `ici analyze --baseline`.
    let saved = call(
        root.path(),
        &["ici", "analyze", "--result", "next-base.json"],
    );
    // `ici analyze` applies the modern policy (no legacy advisory mode); only the comparison
    // matters here, and the extra finding does not change its verdict class.
    assert!(
        root.path().join("next-base.json").is_file(),
        "{}",
        saved.stdout
    );
    write_source(root.path(), &["branchy", "another"]);
    let next = call(
        root.path(),
        &["ici", "analyze", "--baseline", "next-base.json"],
    );
    assert_eq!(next.exit_code, saved.exit_code, "{}", next.stdout);
    let baseline = &native(root.path())["baseline"];
    assert_eq!(baseline["state"], "comparable");
    assert_eq!(baseline["unchanged"], json!(fingerprints));
    assert_eq!(baseline["new"].as_array().unwrap().len(), 1);
}

#[test]
fn pyz_era_baseline_is_readable_but_refused_with_the_regenerate_command_and_never_mass_new() {
    let root = project();
    fs::write(
        root.path().join("old.json"),
        include_bytes!("../../../tests/fixtures/ici-next/legacy-v3-suite.json"),
    )
    .unwrap();
    let plain = call(root.path(), &["ici", "verify"]);
    let regenerate = "regenerate it with: ici verify --write-baseline old.json";

    // Without --fail-on-new: the run keeps its own exit, the incompatibility is explicit in the
    // console and the saved JSON, and no current finding is reported as new.
    let compared = call(
        root.path(),
        &[
            "ici",
            "verify",
            "--report",
            "--sarif",
            "out.sarif",
            "--baseline",
            "old.json",
        ],
    );
    assert_eq!(compared.exit_code, plain.exit_code, "{}", compared.stdout);
    let baseline = &native(root.path())["baseline"];
    assert_eq!(baseline["state"], "incompatible");
    assert!(
        baseline["reason"].as_str().unwrap().ends_with(regenerate),
        "{baseline:#}"
    );
    assert_eq!(baseline["new"], json!([]));
    assert_eq!(baseline["unchanged"], json!([]));
    let flat = compared.stdout.split_whitespace().collect::<String>();
    assert!(
        flat.contains(&regenerate.split_whitespace().collect::<String>()),
        "{}",
        compared.stdout
    );
    assert!(
        !compared.stdout.contains("NEW GATED"),
        "{}",
        compared.stdout
    );
    let sarif = json(&root.path().join("out.sarif"));
    assert!(
        sarif["runs"][0]["results"]
            .as_array()
            .unwrap()
            .iter()
            .all(|result| result.get("baselineState").is_none()),
        "{sarif:#}"
    );
    assert!(
        json(&root.path().join("verify_report.json"))["baseline_comparison"].is_null(),
        "no frozen delta is inferred from an incompatible baseline"
    );

    // With --fail-on-new: never a silent pass. Exit 1 like the frozen gate failure, the reason and
    // the regenerate command on stdout, the result saved, the requested replacement not written.
    let gated = call(
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
    );
    assert_eq!(gated.exit_code, 1, "{}", gated.stdout);
    assert!(
        gated.stdout.contains("Baseline gate: FAILED") && gated.stdout.contains(regenerate),
        "{}",
        gated.stdout
    );
    assert!(!root.path().join("replacement.json").exists());
    assert!(root.path().join(ici_cli::DEFAULT_RESULT).exists());
    assert_eq!(
        fs::read(root.path().join("old.json")).unwrap(),
        include_bytes!("../../../tests/fixtures/ici-next/legacy-v3-suite.json")
    );

    // The suggested command really regenerates a comparable baseline.
    let regenerated = call(
        root.path(),
        &["ici", "verify", "--write-baseline", "old.json"],
    );
    assert_eq!(regenerated.exit_code, 0, "{}", regenerated.stdout);
    let again = call(
        root.path(),
        &["ici", "verify", "--baseline", "old.json", "--fail-on-new"],
    );
    assert_eq!(again.exit_code, 0, "{}", again.stdout);
    assert_eq!(native(root.path())["baseline"]["state"], "comparable");

    // The next command names its own regenerate command.
    fs::write(
        root.path().join("old.json"),
        include_bytes!("../../../tests/fixtures/ici-next/legacy-v3-suite.json"),
    )
    .unwrap();
    call(root.path(), &["ici", "analyze", "--baseline", "old.json"]);
    let reason = native(root.path())["baseline"]["reason"].clone();
    assert!(
        reason
            .as_str()
            .unwrap()
            .ends_with("regenerate it with: ici analyze --result old.next.json (then pass --baseline old.next.json)"),
        "{reason}"
    );
}
