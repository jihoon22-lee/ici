//! Legacy binary_compat beside a failed CMake configure with declared artifacts, against the
//! frozen CLI (`tests/rust-parity/cpp/capture_binary_compat_declared_artifacts.py`, root ruling
//! 2026-11-14 (g3)). Every session of `ici verify --profile deep` fails at the configure, so
//! frozen publishes no build-session manifest: a `[engines.binary_compat] artifacts` selection
//! is BinaryCompatibilityEngine's `configured artifact was not published: …` ERROR (sorted,
//! comma-joined, optional or required), and a `[build.artifacts]` declaration (only report and
//! python-wheel kinds) leaves the NOT_APPLICABLE SKIP of the control; beside that declaration
//! (literal or glob; one glob matching nothing publishes nothing) only the selections it does
//! not publish are missing, and a selection of declared records is frozen's non-ELF FAIL (or
//! the NonElf SKIP under `allow_non_elf`). A legacy `type='hybrid'` package (`python.artifact`)
//! publishes no manifest either. Compared per case: the
//! build engine's verdict, the binary_compat engine (status, evidence, required, summary,
//! targets, findings) and the analysis context's manifests by producer and kind.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

#[path = "support/host_tools.rs"]
mod host_tools;

const TARGET_FIELDS: [&str; 5] = [
    "target_name",
    "status",
    "message",
    "file_path",
    "start_line",
];
const FINDING_FIELDS: [&str; 5] = ["rule_id", "severity", "confidence", "category", "message"];

fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

fn pick(value: &Value, fields: &[&str]) -> Value {
    json!(
        fields
            .iter()
            .map(|field| (
                (*field).to_owned(),
                value.get(*field).cloned().unwrap_or(Value::Null)
            ))
            .collect::<serde_json::Map<_, _>>()
    )
}

fn view(engines: &BTreeMap<String, Value>, manifests: &Value) -> Value {
    let compat = &engines["binary_compat"];
    let list = |field: &str, fields: &[&str]| -> Value {
        json!(
            compat[field]
                .as_array()
                .map(|items| items.iter().map(|item| pick(item, fields)).collect())
                .unwrap_or_else(Vec::new)
        )
    };
    let manifests: Vec<Value> = manifests
        .as_array()
        .map(|manifests| {
            manifests
                .iter()
                .flat_map(|manifest| {
                    manifest["artifacts"]
                        .as_array()
                        .cloned()
                        .unwrap_or_default()
                        .into_iter()
                        .map(|record| pick(&record, &["producer", "kind", "id", "path"]))
                })
                .collect()
        })
        .unwrap_or_default();
    // The build engine's `[build.artifacts]` publication after the failed configure (the
    // configure's own BuildError is compared by the preparation-failure replays).
    let declared: Vec<Value> = engines["build"]["targets"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|target| {
            target["target_name"] == "DeclaredArtifacts"
                || target["message"]
                    .as_str()
                    .is_some_and(|message| message.starts_with("Declared artifacts"))
        })
        .map(|target| pick(target, &TARGET_FIELDS))
        .collect();
    json!({
        "build": engines["build"]["status"],
        "declared": declared,
        "binary_compat": {
            "status": compat["status"],
            "evidence": compat["evidence"],
            "required": compat["required"],
            "summary": compat["summary"],
            "targets": list("targets", &TARGET_FIELDS),
            "findings": list("findings", &FINDING_FIELDS),
        },
        "manifests": manifests,
    })
}

fn run(oracle: &Value, name: &str, case: &Value, path: &str) -> Option<String> {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp")
            .join(oracle["fixture"].as_str().unwrap()),
        &root,
    );
    fs::create_dir(root.join(".git")).unwrap();
    fs::create_dir(work.path().join(".hg")).unwrap();
    fs::write(root.join("ici.toml"), case["config"].as_str().unwrap()).unwrap();
    fs::write(
        root.join("CMakeLists.txt"),
        oracle["cmake_lists"].as_str().unwrap(),
    )
    .unwrap();
    for (relative, text) in case["files"].as_object().into_iter().flatten() {
        fs::write(root.join(relative), text.as_str().unwrap()).unwrap();
    }
    // The `python-*` cases are the same tree as a legacy `type='hybrid'` Python package.
    if name.starts_with("python-") {
        for (relative, text) in oracle["hybrid_files"].as_object().unwrap() {
            let path = root.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, text.as_str().unwrap()).unwrap();
        }
    }
    let invocation = registry::parse(["ici", "verify", "--profile", "deep", "--report"]).unwrap();
    let environment = BTreeMap::from([
        ("PATH".to_string(), path.to_string()),
        ("HOME".to_string(), work.path().display().to_string()),
        (
            "GIT_CEILING_DIRECTORIES".to_string(),
            work.path().display().to_string(),
        ),
    ]);
    ici_cli::cpp::set_reentry_binary_for_tests(Some(env!("CARGO_BIN_EXE_ici").into()));
    let output = commands::dispatch(&invocation, &root, &environment);
    ici_cli::cpp::set_reentry_binary_for_tests(None);
    let exit_code = output.unwrap().exit_code;
    let text = fs::read_to_string(root.join("verify_report.json"))
        .unwrap()
        .replace(&root.display().to_string(), "<ROOT>");
    let report: Value = serde_json::from_str(&text).unwrap();
    let engines: BTreeMap<String, Value> = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|engine| {
            (
                engine["engine_name"].as_str().unwrap().to_owned(),
                engine.clone(),
            )
        })
        .collect();
    let frozen = &case["frozen"];
    let frozen_engines: BTreeMap<String, Value> = frozen["engines"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(name, engine)| (name.clone(), engine.clone()))
        .collect();
    let native = view(&engines, &report["analysis_context"]["artifact_manifests"]);
    let expected = view(&frozen_engines, &frozen["context_manifests"]);
    // Accepted policy (decisions.md 'legacy 종료 코드 3의 범위'): a run whose required evidence
    // stays incomplete keeps the ERROR suite and exits 3 where frozen exited 1.
    let exit_ok = json!(exit_code) == frozen["exit_code"]
        || (exit_code == 3
            && frozen["exit_code"] == 1
            && report["suite_status"] == "ERROR"
            && frozen["counters"]["suite_status"] == "ERROR"
            && report["native_run"]["execution"]["required_complete"] == false);
    (native != expected || !exit_ok).then(|| {
        format!(
            "== {name} (exit native {exit_code}, frozen {})\nfrozen: {}\nnative: {}",
            frozen["exit_code"],
            serde_json::to_string_pretty(&expected).unwrap(),
            serde_json::to_string_pretty(&native).unwrap()
        )
    })
}

/// Positive: a selection names an artifact no session published (frozen's ERROR, optional and
/// required, two selections sorted, beside a `[build.artifacts]` declaration only the missing
/// one), or declared records (the non-ELF FAIL). Negative: the control and a `[build.artifacts]` report
/// declaration stay frozen's NOT_APPLICABLE SKIP. Failure: every case's build engine is the
/// configure's ERROR. Needs /usr/bin/cmake and /usr/bin/g++; missing tools are reported, and
/// fail under ICI_REQUIRE_BUILD_ADAPTERS=1.
#[test]
fn legacy_binary_compat_beside_a_failed_configure_follows_frozen_declared_artifacts() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/binary-compat-declared-artifacts-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 19);
    // Independent literals of the capture.
    let compat = |name: &str| &cases[name]["frozen"]["engines"]["binary_compat"];
    assert_eq!(
        compat("selected-two")["summary"],
        "Binary compatibility analysis incomplete: configured artifact was not published: bin/alpha, release:shadow:zeta"
    );
    assert_eq!(compat("selected-artifact")["required"], false);
    assert_eq!(compat("selected-artifact-required")["required"], true);
    assert_eq!(compat("declared-report")["status"], "SKIP");
    assert_eq!(compat("control")["evidence"], "NOT_APPLICABLE");
    assert_eq!(
        compat("declared-selected-declared")["targets"][0]["message"],
        "Selected binary manifest artifact is not an ELF object"
    );
    assert_eq!(compat("declared-selected-allow-non-elf")["status"], "PASS");
    assert_eq!(
        compat("glob-unmatched-selected")["summary"],
        "Binary compatibility analysis incomplete: configured artifact was not published: CMakeLists.txt"
    );
    assert_eq!(compat("python-control")["status"], "SKIP");
    assert_eq!(
        compat("declared-elf-truncated")["summary"],
        "Binary compatibility analysis incomplete: readelf did not produce complete evidence for tool.bin"
    );
    assert_eq!(
        compat("declared-max-artifacts")["summary"],
        "Binary compatibility analysis incomplete: binary artifact count exceeds configured maximum 1"
    );
    assert_eq!(
        compat("declared-readelf-unavailable")["summary"],
        "Binary compatibility analysis incomplete: readelf capability is unavailable"
    );
    assert_eq!(
        compat("declared-selected-mixed")["summary"],
        "Binary compatibility analysis incomplete: configured artifact was not published: release:shadow:calc"
    );
    let required = std::env::var("ICI_REQUIRE_BUILD_ADAPTERS").as_deref() == Ok("1");
    for tool in ["cmake", "g++"] {
        if host_tools::version(&Path::new("/usr/bin").join(tool)).is_none() {
            assert!(!required, "/usr/bin/{tool} is required");
            println!("no /usr/bin/{tool} on this host; the declared-artifact cases did not run");
            return;
        }
    }
    let host = host_tools::mirror(&host_tools::NO_LLVM);
    let mismatches: Vec<String> = cases
        .iter()
        .filter_map(|(name, case)| {
            // A case that hides executables (`readelf`) runs over a mirror without them.
            let hidden: Vec<&str> = case["hidden"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .collect();
            if hidden.is_empty() {
                run(&oracle, name, case, &host.path)
            } else {
                let skipped: Vec<&str> = host_tools::NO_LLVM.into_iter().chain(hidden).collect();
                run(&oracle, name, case, &host_tools::mirror(&skipped).path)
            }
        })
        .collect();
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}

/// One legacy Make `verify --profile deep` over the package-make tree with `makefile` and
/// `config`; `prepare` runs first in the project. Returns the binary_compat and build engines.
fn make_run(makefile: &str, config: &str, path: &str, prepare: &[&str]) -> (Value, Value) {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    let fixture =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity-fixtures/cpp/package-make");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::copy(fixture.join("pyproject.toml"), root.join("pyproject.toml")).unwrap();
    fs::copy(fixture.join("src/main.cpp"), root.join("src/main.cpp")).unwrap();
    fs::create_dir(root.join(".git")).unwrap();
    fs::create_dir(work.path().join(".hg")).unwrap();
    fs::write(root.join("Makefile"), makefile).unwrap();
    fs::write(root.join("ici.toml"), config).unwrap();
    if let Some((program, args)) = prepare.split_first() {
        let status = std::process::Command::new(program)
            .args(args)
            .current_dir(&root)
            .env_clear()
            .env("PATH", path)
            .status()
            .unwrap();
        assert!(status.success(), "{prepare:?}");
    }
    let invocation = registry::parse(["ici", "verify", "--profile", "deep", "--report"]).unwrap();
    let environment = BTreeMap::from([
        ("PATH".to_string(), path.to_string()),
        ("HOME".to_string(), work.path().display().to_string()),
        (
            "GIT_CEILING_DIRECTORIES".to_string(),
            work.path().display().to_string(),
        ),
    ]);
    ici_cli::cpp::set_reentry_binary_for_tests(Some(env!("CARGO_BIN_EXE_ici").into()));
    let output = commands::dispatch(&invocation, &root, &environment);
    ici_cli::cpp::set_reentry_binary_for_tests(None);
    output.unwrap();
    let text = fs::read_to_string(root.join("verify_report.json"))
        .unwrap()
        .replace(&root.display().to_string(), "<project>");
    let report: Value = serde_json::from_str(&text).unwrap();
    let engine = |name: &str| {
        report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == name)
            .cloned()
            .unwrap_or(Value::Null)
    };
    (engine("binary_compat"), engine("build"))
}

/// The binary_compat view of one inspected artifact with its identity and location masked
/// (`<ID>`, `<PATH>`) and the project root as `<project>` (legacy_binary_compat.rs's
/// normalization); fingerprints, which hash the location, are left out.
fn masked(engine: &Value, identity: &str, path: &str) -> Value {
    let text = engine
        .to_string()
        .replace(identity, "<ID>")
        .replace(&format!("\"{path}\""), "\"<PATH>\"");
    let engine: Value = serde_json::from_str(&text).unwrap();
    let mut findings: Vec<Value> = engine["findings"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|finding| {
            pick(
                finding,
                &[
                    "rule_id",
                    "severity",
                    "confidence",
                    "category",
                    "message",
                    "tool_rule_id",
                ],
            )
        })
        .collect();
    findings.sort_by_key(Value::to_string);
    json!({
        "status": engine["status"],
        "evidence": engine["evidence"],
        "required": engine["required"],
        "summary": engine["summary"],
        "targets": engine["targets"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|target| pick(target, &TARGET_FIELDS))
            .collect::<Vec<_>>(),
        "findings": findings,
    })
}

/// Root ruling 2026-11-14 (g3): a real ELF fact is host-dependent, so a declared ELF record is
/// not frozen-captured; its equivalence goes through the frozen-compared built-artifact path
/// (legacy_binary_compat.rs). The same source compiled by the same `/usr/bin/g++` command is
/// selected once as a declared `[build.artifacts]` record beside a failed Make build
/// (`release:project:tool.bin`, the check's readelf/ABI steps over the declared record) and once
/// as the Make build's own output (`release:shadow:app`); with a `max_glibc` floor forcing a
/// violation, both give the same verdict, evidence, targets and findings. Needs /usr/bin/make,
/// g++ and readelf; missing tools are reported, and fail under ICI_REQUIRE_BUILD_ADAPTERS=1.
#[test]
fn legacy_declared_elf_record_is_inspected_like_a_built_artifact() {
    let required = std::env::var("ICI_REQUIRE_BUILD_ADAPTERS").as_deref() == Ok("1");
    for tool in ["make", "g++", "readelf"] {
        if host_tools::version(&Path::new("/usr/bin").join(tool)).is_none() {
            assert!(!required, "/usr/bin/{tool} is required");
            println!("no /usr/bin/{tool} on this host; the declared ELF case did not run");
            return;
        }
    }
    let host = host_tools::mirror(&host_tools::NO_LLVM);
    let config = "[project]\ntype='cpp'\nsource_dirs=['src']\n[build.make]\nenabled=true\n\
        build_argv=['make','all']\n[engines.build]\nenabled=true\n[engines.binary_compat]\n\
        enabled=true\nmax_glibc='2.1'\n";
    let (built, built_build) = make_run(
        "all:\n\tmkdir -p build/ici-make\n\tg++ src/main.cpp -o build/ici-make/app\n",
        &format!("{config}artifacts=['release:shadow:app']\n"),
        &host.path,
        &[],
    );
    let (declared, declared_build) = make_run(
        "all:\n\texit 1\n",
        &format!(
            "{config}artifacts=['release:project:tool.bin']\n[build.artifacts]\nreport=['tool.bin']\n"
        ),
        &host.path,
        &["/usr/bin/g++", "src/main.cpp", "-o", "tool.bin"],
    );
    assert_eq!(built_build["status"], "PASS");
    assert_eq!(declared_build["status"], "ERROR");
    assert_eq!(
        declared["targets"][0]["target_name"],
        "BinaryCompatibility:release:project:tool.bin"
    );
    assert!(
        declared["findings"]
            .as_array()
            .is_some_and(|findings| findings.iter().any(|finding| finding["rule_id"]
                .as_str()
                .is_some_and(|rule| rule.starts_with("ici.binary.")))),
        "the max_glibc floor is a measured violation: {declared}"
    );
    assert_eq!(
        masked(&declared, "release:project:tool.bin", "tool.bin"),
        masked(&built, "release:shadow:app", "app")
    );
}
