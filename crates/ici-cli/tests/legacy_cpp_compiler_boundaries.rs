//! Frozen legacy C++ compiler-backed function boundaries (`tests/rust-parity/cpp/
//! compiler-boundaries-oracle.json`, `capture_compiler_boundaries.py`): every case is replayed
//! through the real CLI (`commands::dispatch`) in a fresh project built from
//! `parity-fixtures/cpp/compiler-boundaries/<tree>`, the captured `ici.toml` and
//! `compile_commands.json`, and the saved complexity/cognitive results are compared with frozen's
//! in full by the shared legacy-result walk (`support/legacy_result_harness.rs`): every key at
//! every depth, with each allowed difference one normalized path in
//! `support/legacy_result_allowances.rs` (`boundaries/...` labels) and stale allowances failing.
//!
//! Frozen runs the dedicated `clang-tidy --checks=-*,readability-function-size` boundary probe
//! only inside `ici verify` (`ComplexityEngine` / `CognitiveEngine` with an analysis context);
//! the standalone `ici complexity` / `ici cognitive` commands keep the brace-heuristic rows
//! whatever tools exist. Cases: exact boundaries (operator, member with a lambda, namespace,
//! template), a macro-generated function, a scanner-only (`#if 0`) function, a production source
//! without a recorded command, two configurations of one source, `cpp_boundaries = "required"`
//! with a scanner-only function and without clang-tidy, `"off"`, no compilation database, a
//! clang-tidy that fails every boundary run and one whose output the frozen parser rejects, a
//! unit whose recorded compiler is not on PATH (a per-unit replay error, the next unit runs),
//! and complexity-only races: a tool that touches itself after its first run (identity changed
//! before execution), rewrites its source (changed during analysis) or the next unit's source
//! (changed before analysis).
//!
//! Tools: the replay PATH is `<bin>` then a mirror of `/usr/bin` and `/bin` without any
//! `clang*`/`clazy*` executable (the capture host had none), `<bin>` holding the case's
//! `clang-tidy`. The `#[ignore]` replay uses the captured clang-tidy (ICI_TEST_CLANG_TIDY, LLVM
//! 21.1.8); the non-ignored twin uses the host `/usr/bin/clang-tidy` when it exists and otherwise
//! skips (and prints) only the `verify` cases that run clang-tidy.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{
    cell::RefCell,
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
    sync::OnceLock,
};

const ORACLE: &str = include_str!("../../../tests/rust-parity/cpp/compiler-boundaries-oracle.json");

include!("support/legacy_result_harness.rs");

include!("support/legacy_result_allowances.rs");

// The text-projection allowances belong to `legacy_engine_extras.rs`.
const _: &[TextAllowance] = TEXT_ALLOWANCES;

fn workspace() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .unwrap()
        .to_path_buf()
}

/// `/usr/bin` and `/bin` mirrored without clang tools: the capture host had no clang-tidy or
/// clang++ there, and a CI runner's must not take part in a case that names its own tools.
fn tool_path() -> &'static str {
    static MIRROR: OnceLock<(tempfile::TempDir, String)> = OnceLock::new();
    &MIRROR
        .get_or_init(|| {
            let mirror = tempfile::tempdir().unwrap();
            let mut directories = vec![];
            for source in ["/usr/bin", "/bin"] {
                let target = mirror.path().join(source.trim_start_matches('/'));
                fs::create_dir_all(&target).unwrap();
                for entry in fs::read_dir(source).unwrap().flatten() {
                    let name = entry.file_name();
                    let text = name.to_string_lossy();
                    if text.starts_with("clang") || text.starts_with("clazy") {
                        continue;
                    }
                    let _ = std::os::unix::fs::symlink(entry.path(), target.join(&name));
                }
                directories.push(target.display().to_string());
            }
            let path = directories.join(":");
            (mirror, path)
        })
        .1
}

fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap().flatten() {
        let path = entry.path();
        if path.is_dir() {
            copy_tree(&path, &to.join(entry.file_name()));
        } else {
            fs::copy(&path, to.join(entry.file_name())).unwrap();
        }
    }
}

/// The capture's configuration-digest mask: every `configuration`/`configurations` digest (it
/// covers the absolute project directory) becomes `<configuration:N>`, N its first appearance in
/// a sorted-key walk of the result.
fn mask_configurations(value: &Value, seen: &mut BTreeMap<String, String>, key: &str) -> Value {
    match value {
        Value::Object(fields) => {
            let mut names: Vec<&String> = fields.keys().collect();
            names.sort();
            Value::Object(
                names
                    .into_iter()
                    .map(|name| (name.clone(), mask_configurations(&fields[name], seen, name)))
                    .collect(),
            )
        }
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|item| mask_configurations(item, seen, key))
                .collect(),
        ),
        Value::String(text)
            if matches!(key, "configuration" | "configurations")
                && text.len() == 71
                && text.starts_with("sha256:")
                && text[7..].bytes().all(|byte| byte.is_ascii_hexdigit()) =>
        {
            let next = format!("<configuration:{}>", seen.len());
            json!(seen.entry(text.clone()).or_insert(next).clone())
        }
        other => other.clone(),
    }
}

/// One command of one case: its exit code and the saved complexity/cognitive results (the whole
/// engine report for an engine command), masked like the capture.
fn replay(oracle: &Value, case: &Value, command: &str, tidy: Option<&Path>) -> (i32, Vec<Value>) {
    let temporary = tempfile::tempdir().unwrap();
    let base = temporary.path().join("project");
    let bin = temporary.path().join("bin");
    let home = temporary.path().join("home");
    copy_tree(
        &workspace()
            .join("parity-fixtures/cpp/compiler-boundaries")
            .join(case["tree"].as_str().unwrap()),
        &base,
    );
    fs::create_dir_all(base.join(".git")).unwrap();
    fs::create_dir_all(&bin).unwrap();
    fs::create_dir_all(&home).unwrap();
    fs::write(base.join("ici.toml"), case["config"].as_str().unwrap()).unwrap();
    if !case["database"].is_null() {
        let rows = serde_json::to_string(&case["database"]).unwrap();
        fs::write(
            base.join("compile_commands.json"),
            rows.replace("{root}", &base.display().to_string()),
        )
        .unwrap();
    }
    match (case["tools"].as_str().unwrap(), tidy) {
        ("tidy", Some(tidy)) => std::os::unix::fs::symlink(tidy, bin.join("clang-tidy")).unwrap(),
        (
            name @ ("failing-tidy"
            | "garbage-tidy"
            | "touching-tidy"
            | "rewriting-tidy"
            | "next-rewriting-tidy"),
            Some(tidy),
        ) => {
            use std::os::unix::fs::PermissionsExt;
            let script = bin.join("clang-tidy");
            let text = oracle["scripts"][name].as_str().unwrap();
            fs::write(
                &script,
                text.replace("{clang-tidy}", &tidy.display().to_string()),
            )
            .unwrap();
            fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();
        }
        _ => {}
    }
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.display().to_string()),
        ("XDG_CONFIG_HOME".to_owned(), home.display().to_string()),
        (
            "PATH".to_owned(),
            format!("{}:{}", bin.display(), tool_path()),
        ),
        ("LANG".to_owned(), "C.UTF-8".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
    ]);
    let argv = [command, "--report"];
    let invocation = registry::parse(std::iter::once("ici").chain(argv)).unwrap();
    let output = commands::dispatch(&invocation, &base, &environment).unwrap();
    let mut text = fs::read_to_string(base.join(format!("{command}_report.json"))).unwrap();
    if let Some(resolved) = tidy.and_then(|tidy| fs::canonicalize(tidy).ok()) {
        text = text.replace(&resolved.display().to_string(), "<clang-tidy>");
    }
    // The `--header-filter` pattern spells the project through Python `re.escape` (JSON-escaped).
    let escaped: String = base
        .display()
        .to_string()
        .chars()
        .flat_map(|character| {
            let special = "()[]{}?*+-|^$\\.&~# ".contains(character);
            special
                .then_some(['\\', '\\'])
                .into_iter()
                .flatten()
                .chain(std::iter::once(character))
        })
        .collect();
    let text = text
        .replace(&escaped, "<root>")
        .replace(&base.display().to_string(), "<root>")
        .replace(&bin.display().to_string(), "<bin>");
    let report: Value = serde_json::from_str(&text).unwrap();
    let engines = if command == "verify" {
        report["results"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|engine| {
                matches!(
                    engine["engine_name"].as_str(),
                    Some("complexity" | "cognitive")
                )
            })
            .map(|engine| mask_configurations(engine, &mut BTreeMap::new(), ""))
            .collect()
    } else {
        vec![mask_configurations(&report, &mut BTreeMap::new(), "")]
    };
    (output.exit_code, engines)
}

/// Host twin only (incidental host data): the tool versions of the evidence records and of the
/// findings (which name the first record's tool) are the host's g++ / clang-tidy builds, and the
/// clang-tidy argv's projected GCC standard-library roots (`-nostdinc++ -isystem ...` before
/// `-w`) are that host GCC version's directories, not the captured ones.
fn mask_versions(engine: &mut Value) {
    for record in engine["tool_evidence"].as_array_mut().into_iter().flatten() {
        record["version"] = json!("<version>");
        if let Some(argv) = record["argv"].as_array_mut()
            && let Some(start) = argv.iter().position(|argument| argument == "-nostdinc++")
            && let Some(end) = argv.iter().rposition(|argument| argument == "-w")
            && start < end
        {
            argv.splice(start..end, [json!("<gcc-stdlib>")]);
        }
    }
    for finding in engine["findings"].as_array_mut().into_iter().flatten() {
        if finding.get("tool_version").is_some() {
            finding["tool_version"] = json!("<version>");
        }
    }
}

/// decisions.md "compiler·linker·binutils 실제 실행 증거" (RCPP_TOOLS): a `g++ stdlib include
/// search` record is the PATH `g++` process native ran (the replay PATH's `/usr/bin` mirror) with
/// `/dev/null` as input, where
/// frozen recorded its resolved driver and `-` (stdin). Each such record is checked element by
/// element: native argv is frozen's with argv[0] the native path and the final `-` replaced by
/// `/dev/null`, and (captured host only) that path resolves to frozen's driver. It then carries
/// frozen's argv and path. Every other record, the clang-tidy one included, is compared as is.
fn stdlib_search_records(
    label: &str,
    frozen: &Value,
    native: &mut Value,
    host: bool,
) -> Vec<String> {
    const NAME: &str = "g++ stdlib include search";
    let mut out = vec![];
    let Some(frozen_records) = frozen["tool_evidence"].as_array() else {
        return out;
    };
    let Some(records) = native["tool_evidence"].as_array_mut() else {
        return out;
    };
    for (index, (record, frozen_record)) in records.iter_mut().zip(frozen_records).enumerate() {
        if record["name"] != NAME || frozen_record["name"] != NAME {
            continue;
        }
        let path = record["path"].as_str().unwrap_or("").to_owned();
        let argv = record["argv"].as_array().cloned().unwrap_or_default();
        let mut expected = frozen_record["argv"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        let resolved = fs::canonicalize(&path)
            .ok()
            .map(|resolved| resolved.display().to_string());
        let shaped = expected.len() > 1
            && expected[0] == frozen_record["path"]
            && expected.last() == Some(&json!("-"))
            && Path::new(&path)
                == Path::new(tool_path().split(':').next().unwrap_or_default()).join("g++")
            && (host || resolved.as_deref() == frozen_record["path"].as_str());
        if shaped {
            let last = expected.len() - 1;
            expected[0] = json!(path);
            expected[last] = json!("/dev/null");
        }
        if !shaped || argv != expected {
            out.push(format!(
                "{label}: tool_evidence[{index}] {NAME} {path} {argv:?} is not frozen's {} {} \
                 with the PATH g++ (resolving to frozen's driver) and /dev/null",
                frozen_record["path"], frozen_record["argv"]
            ));
            continue;
        }
        record["argv"] = frozen_record["argv"].clone();
        record["path"] = frozen_record["path"].clone();
    }
    out
}

/// Replay every case (`tidy`: the clang-tidy to put on PATH; without it the `verify` runs where
/// frozen ran clang-tidy are skipped and named) and return the unallowed differences.
fn differences(tidy: Option<&Path>, host: bool) -> Vec<String> {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let mut out = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        for command in ["verify", "complexity", "cognitive"] {
            let Some(captured) = case.get(command) else {
                continue;
            };
            // A run whose frozen result depended on an approved clang-tidy: its boundary mode is
            // not `heuristic` (`off`, no database and the engine commands never reach one).
            let runs_tidy = case["tools"] != ""
                && captured["results"].as_array().is_some_and(|engines| {
                    engines
                        .iter()
                        .any(|engine| engine["extra"]["cpp_boundary_mode"] != "heuristic")
                });
            if runs_tidy && tidy.is_none() {
                println!("skipped {name}/{command}: no clang-tidy on this host");
                continue;
            }
            if runs_tidy && host && name == "macro" {
                // Whether Clang reports a macro-generated function at its expansion (the frozen
                // scope exclusion) depends on the clang-tidy release; the captured 21.1.8 does.
                println!("skipped {name}/{command}: clang-tidy-version-dependent macro diagnostic");
                continue;
            }
            let (code, mut native) = replay(&oracle, case, command, tidy);
            let mut frozen = match captured.get("results") {
                Some(results) => results.as_array().unwrap().clone(),
                None => vec![captured["report"].clone()],
            };
            if host {
                frozen.iter_mut().for_each(mask_versions);
                native.iter_mut().for_each(mask_versions);
            }
            let kind = if runs_tidy { "tidy" } else { "plain" };
            let label = format!("boundaries/{kind}/{name}/{command}");
            assert_exit(
                &label,
                &[code],
                &codes(&captured["exit_code"]),
                &frozen,
                &native,
            );
            let names = |engines: &[Value]| -> Vec<Value> {
                engines
                    .iter()
                    .map(|engine| engine["engine_name"].clone())
                    .collect()
            };
            assert_eq!(names(&native), names(&frozen), "{label}: engines");
            for (left, right) in frozen.iter().zip(native.iter_mut()) {
                let engine = left["engine_name"].as_str().unwrap();
                out.extend(stdlib_search_records(
                    &format!("{label}/{engine}"),
                    left,
                    right,
                    host,
                ));
                out.extend(result_differences(
                    &format!("{label}/{engine}"),
                    left,
                    right,
                ));
            }
        }
    }
    out
}

/// Positive and negative (the captured clang-tidy 21.1.8): every case equals frozen except the
/// allowed paths, and no `boundaries` allowance is stale.
#[test]
#[ignore = "explicit actual clang-tidy 21 (ICI_TEST_CLANG_TIDY)"]
fn legacy_cpp_compiler_boundaries_equal_frozen_with_the_captured_clang_tidy() {
    let tidy = PathBuf::from(
        std::env::var("ICI_TEST_CLANG_TIDY").expect("ICI_TEST_CLANG_TIDY names clang-tidy 21"),
    );
    let differences = differences(Some(&tidy), false);
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| entry.0.starts_with("boundaries"));
}

/// The same cases with the host's own `/usr/bin/clang-tidy` (as on the CI runner) when it is the
/// captured LLVM 21 release (any build: the evidence versions and GCC roots are masked);
/// otherwise only the cases that need it are skipped (named on stdout), and the standalone,
/// `off`, no-database, `required` without clang-tidy and every other tool-free run is compared.
#[test]
fn legacy_cpp_compiler_boundaries_match_frozen_with_the_host_clang_tidy() {
    let host = Path::new("/usr/bin/clang-tidy");
    // The probe's `--config` options (`CountMemberInitAsStmt`, `IgnoreMacros`) and its function
    // statement/line counts are those of the captured LLVM 21; another release may reject an
    // option or count differently, which frozen with that tool would show as well (a capture
    // version dependency, not a native difference). Such a host runs only the tool-free cases.
    let version = std::process::Command::new(host)
        .arg("--version")
        .output()
        .ok()
        .map(|output| String::from_utf8_lossy(&output.stdout).into_owned())
        .unwrap_or_default();
    let captured = version
        .split("LLVM version ")
        .nth(1)
        .is_some_and(|rest| rest.starts_with("21."));
    if host.exists() && !captured {
        println!(
            "host clang-tidy is not LLVM 21 ({}): clang-tidy cases skipped",
            version.trim()
        );
    }
    let tidy = (host.exists() && captured).then_some(host);
    let differences = differences(tidy, true);
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    if tidy.is_some() {
        assert_not_stale(|entry| entry.0.starts_with("boundaries"));
    } else {
        assert_not_stale(|entry| entry.0 == "boundaries");
    }
}
