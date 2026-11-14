//! Legacy `ici cognitive` / `ici complexity` over a C++ source that is not UTF-8 against frozen
//! v0.13.0 (`tests/rust-parity/cpp/metric-sources-oracle.json`, `capture_metric_sources.py`):
//! frozen reads every source strictly first, so cognitive keeps only a
//! `CppCognitiveSourceError` per unreadable source ("Cognitive complexity analysis did not
//! complete") and complexity one `CppComplexityAnalysisError` at the project root with no
//! function, both ERROR NOT_RUN; dup's source intake reports only the first such source
//! (`SourceInputError` 'invalid-utf8: Analysis source is not valid UTF-8'), and so does dead's
//! (frozen `DeadCodeEngine._collect_sources`, read even without a compilation database), which
//! then stays required. The `unterminated` frozen cases (brace-heuristic spans) are replayed in
//! full by `legacy_engine_extras.rs::legacy_cpp_heuristic_metrics_equal_frozen_except_allowed`
//! (`heuristic-metrics-oracle.json`) and here on the captured fields. The `empty` tree (a
//! C++ project with no source) replays frozen's not-applicable `ici exception` / `ici type`.
//! Every dup/dead case also compares the whole frozen engine `extra` (every frozen key and
//! value; a native-only key must be a named native evidence key), including the
//! `SourceInputError` intake stops.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn copy(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

#[test]
fn legacy_cpp_metrics_stop_at_a_non_utf8_source_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/metric-sources-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    for command in ["cognitive", "complexity", "dup", "dead"] {
        let expected = &oracle["cases"][format!("{command}:unreadable")];
        let (_, mut actual) = run(&oracle, "unreadable", command);
        // Accepted policy (root decision, reporting command:ici verify): a required check whose
        // evidence did not complete exits 3 natively where frozen exited 1 (complexity, dup and
        // a dead analysis whose intake failed are required by default, cognitive is not).
        if command != "cognitive" {
            assert_eq!(actual["exit_code"], 3, "{command}");
            actual["exit_code"] = expected["exit_code"].clone();
        }
        assert_eq!(actual, *expected, "{command}");
        // A header that is not UTF-8: the metrics never read it, dup's intake stops at it.
        let expected = &oracle["cases"][format!("{command}:unreadable-header")];
        let (_, mut actual) = run(&oracle, "unreadable-header", command);
        if matches!(command, "dup" | "dead") {
            assert_eq!(actual["exit_code"], 3, "{command}");
            actual["exit_code"] = expected["exit_code"].clone();
        }
        assert_eq!(actual, *expected, "{command} header");
    }
    // A function body that never closes: cognitive's unterminated-range ERROR beside the
    // measured source (exit 1), complexity still measures `open_fn()` (exit 0).
    for command in ["cognitive", "complexity"] {
        let expected = &oracle["cases"][format!("{command}:unterminated")];
        let (_, actual) = run(&oracle, "unterminated", command);
        assert_eq!(actual, *expected, "{command} unterminated");
    }
}

fn leak(path: String) -> &'static str {
    Box::leak(path.into_boxed_str())
}

/// `capture_metric_sources.py` `functions`: `count` identical 14-line C++ function bodies.
fn functions(count: usize) -> Vec<u8> {
    let body: String = (0..12)
        .map(|j| format!("  int v{j} = a * {j} + b - {j};\n"))
        .collect();
    (0..count)
        .map(|i| format!("int f{i}(int a, int b) {{\n{body}  return v0;\n}}\n\n"))
        .collect::<String>()
        .into_bytes()
}

/// `capture_metric_sources.py` GENERATED trees (never committed).
fn generated(tree: &str) -> Option<Vec<(&'static str, Vec<u8>)>> {
    let ok = b"int ok(int x) {\n  if (x) { return 1; }\n  return 0;\n}\n".to_vec();
    match tree {
        "intake-nul" => Some(vec![
            ("src/nul.cpp", b"int a;\0\n".to_vec()),
            ("src/ok.cpp", ok),
        ]),
        "intake-too-large" => {
            let mut big = b"// ".to_vec();
            big.extend(std::iter::repeat_n(b'x', 8 * 1024 * 1024));
            big.push(b'\n');
            Some(vec![("src/big.cpp", big), ("src/ok.cpp", ok)])
        }
        "intake-database" => Some(vec![
            (
                "ici.toml",
                b"[project]\ntype = \"cpp\"\nsource_dirs = [\"src\"]\ncompile_database = \"compile_commands.json\"\n".to_vec(),
            ),
            (
                "compile_commands.json",
                b"[{\"directory\": \".\", \"command\": \"g++ -std=c++17 -c src/ok.cpp -o build/ok.o\", \"file\": \"src/ok.cpp\"}, {\"directory\": \".\", \"command\": \"g++ -std=c++17 -c src/latin.cpp -o build/latin.o\", \"file\": \"src/latin.cpp\"}]\n".to_vec(),
            ),
            ("src/ok.cpp", ok),
            (
                "src/latin.cpp",
                b"// caf\xe9\nstatic int unused() { return 1; }\n".to_vec(),
            ),
        ]),
        "excluded-only" => Some(vec![
            ("src/third_party/v.cpp", ok.clone()),
            ("src/generated/g.cpp", ok),
        ]),
        "excluded-mixed" => Some(vec![("src/_deps/v.cpp", ok.clone()), ("src/ok.cpp", ok)]),
        "dup-records-limit" => Some(vec![(
            "src/a.cpp",
            (0..500_001)
                .map(|index| format!("int v{index};\n"))
                .collect::<String>()
                .into_bytes(),
        )]),
        "dup-indexed-records-limit" => Some(vec![
            ("src/a.cpp", b"x\n".repeat(300_000)),
            ("src/b.cpp", b"x\n".repeat(300_000)),
        ]),
        "dup-window-occurrence-limit" => Some(vec![("src/a.cpp", functions(2_100))]),
        "dup-raw-match-limit" => Some(vec![("src/a.cpp", functions(200))]),
        "intake-inventory" => {
            let mut part = b"// ".to_vec();
            part.extend(std::iter::repeat_n(b'x', 15 * 512 * 1024));
            part.push(b'\n');
            let names: [&'static str; 9] = [
                "src/part0.cpp",
                "src/part1.cpp",
                "src/part2.cpp",
                "src/part3.cpp",
                "src/part4.cpp",
                "src/part5.cpp",
                "src/part6.cpp",
                "src/part7.cpp",
                "src/part8.cpp",
            ];
            Some(names.into_iter().map(|name| (name, part.clone())).collect())
        }
        "intake-too-many-files" => Some(
            (0..2049)
                .map(|index| (leak(format!("src/f{index:04}.cpp")), b"int x;\n".to_vec()))
                .collect(),
        ),
        "intake-too-many-candidates" => Some(
            (0..8192)
                .map(|index| {
                    (
                        leak(format!("src/third_party/v{index:04}.cpp")),
                        b"int x;\n".to_vec(),
                    )
                })
                .chain([("src/ok.cpp", ok)])
                .collect(),
        ),
        _ => None,
    }
}

/// Frozen `read_analysis_sources` stops dup and dead at a NUL character (`invalid-text`), a
/// source over 8 MiB (`too-large`), an owned inventory over 64 MiB (`inventory-too-large`), more
/// than 2048 owned sources (`too-many-files`) and more than 8192 candidates including excluded
/// vendor sources (`too-many-candidates`); both stay required.
#[test]
fn legacy_cpp_dup_and_dead_stop_at_frozen_intake_bounds() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/metric-sources-oracle.json"
    ))
    .unwrap();
    for tree in [
        "intake-nul",
        "intake-too-large",
        "intake-database",
        "intake-inventory",
        "intake-too-many-files",
        "intake-too-many-candidates",
    ] {
        for command in ["dup", "dead"] {
            let expected = &oracle["cases"][format!("{command}:{tree}")];
            let (_, mut actual) = run(&oracle, tree, command);
            // Accepted exit policy (see above): required incomplete evidence exits 3.
            assert_eq!(actual["exit_code"], 3, "{command} {tree}");
            actual["exit_code"] = expected["exit_code"].clone();
            assert_eq!(actual, *expected, "{command} {tree}");
        }
    }
}

/// Native evidence keys a legacy engine `extra` may carry beside frozen's (decisions.md "legacy
/// `extra` 안 native 증거 키"; reporting.json R10/R11).
const NATIVE_EXTRA_KEYS: [&str; 12] = [
    "native_checks",
    "target_scopes",
    "finding_identity_map",
    "parser_identity",
    "source_encodings",
    "excluded_source_counts",
    "module_index",
    "files_checked",
    "python_issues",
    "duplicated_lines",
    "total_code_lines",
    "legacy_findings",
];

/// The native `extra` restricted to frozen's keys (every frozen key and value must match, an
/// absent one reads `null`); any other native key must be a named native evidence key.
fn frozen_extra(case: &str, native: &Value, frozen: &serde_json::Map<String, Value>) -> Value {
    for key in native
        .as_object()
        .into_iter()
        .flat_map(|extra| extra.keys())
    {
        assert!(
            frozen.contains_key(key) || NATIVE_EXTRA_KEYS.contains(&key.as_str()),
            "{case}: native-only extra key {key}"
        );
    }
    Value::Object(
        frozen
            .keys()
            .map(|key| (key.clone(), native[key].clone()))
            .collect(),
    )
}

fn run(oracle: &Value, tree: &str, command: &str) -> (tempfile::TempDir, Value) {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../parity-fixtures/cpp/metric-sources")
        .canonicalize()
        .unwrap();
    {
        let work = tempfile::tempdir().unwrap();
        let root = work.path().canonicalize().unwrap().join("project");
        if let Some(files) = generated(tree) {
            for (path, bytes) in files {
                fs::create_dir_all(root.join(path).parent().unwrap()).unwrap();
                fs::write(root.join(path), bytes).unwrap();
            }
        } else {
            copy(&fixtures.join(tree), &root);
        }
        if !root.join("ici.toml").exists() {
            fs::write(root.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
        }
        let home = work.path().join("home");
        fs::create_dir(&home).unwrap();
        let environment = BTreeMap::from([
            ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
            ("HOME".to_owned(), home.display().to_string()),
            ("LANG".to_owned(), "C.UTF-8".to_owned()),
            ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        ]);
        let invocation = registry::parse(["ici", command, "--report"]).unwrap();
        let output = commands::dispatch(&invocation, &root, &environment).unwrap();
        let report: Value = serde_json::from_str(
            &fs::read_to_string(root.join(format!("{command}_report.json"))).unwrap(),
        )
        .unwrap();
        let targets: Vec<Value> = report["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| {
                json!({"target_name": target["target_name"], "status": target["status"],
                    "message": target["message"], "file_path": target["file_path"],
                    "start_line": target["start_line"], "end_line": target["end_line"]})
            })
            .collect();
        let mut actual = json!({"exit_code": output.exit_code, "status": report["status"],
            "evidence": report["evidence"], "required": report["required"], "summary": report["summary"], "targets": targets});
        if let Some(frozen) = oracle["cases"][format!("{command}:{tree}")]["extra"].as_object() {
            actual["extra"] = frozen_extra(&format!("{command}:{tree}"), &report["extra"], frozen);
        }
        (work, actual)
    }
}

#[test]
fn legacy_cpp_exception_and_type_without_sources_skip_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/metric-sources-oracle.json"
    ))
    .unwrap();
    for command in ["exception", "type", "dup", "dead"] {
        let (_, actual) = run(&oracle, "empty", command);
        assert_eq!(
            actual,
            oracle["cases"][format!("{command}:empty")],
            "{command}"
        );
    }
}

/// Recorded difference (dup.py:100, dead.py:111; decisions.md "16 MiB를 넘는 C++ dead/dup source"):
/// a source over the application's 16 MiB snapshot limit never reaches the frozen intake port, so
/// instead of frozen's `SourceInputError` 'too-large' target (ERROR, exit 1; replayed for 8-16 MiB
/// above) it stays unread and named. Clone and dead-code relations over that scope cannot be
/// partial: those checks are BLOCKED NOT_RUN naming the file and the limit, and the run exits 3.
#[test]
fn legacy_cpp_sources_over_the_snapshot_bound_block_the_checks_relating_them() {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().canonicalize().unwrap().join("project");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(root.join("src/big.cpp"), vec![b'x'; 17 * 1024 * 1024]).unwrap();
    fs::write(root.join("src/ok.cpp"), b"int ok() { return 0; }\n").unwrap();
    fs::write(
        root.join("ici.toml"),
        "[project]\ntype = \"cpp\"\nsource_dirs = [\"src\"]\n",
    )
    .unwrap();
    let home = work.path().join("home");
    fs::create_dir(&home).unwrap();
    let environment = BTreeMap::from([
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
    ]);
    for command in ["dup", "dead"] {
        let invocation = registry::parse(["ici", command, "--report"]).unwrap();
        let output = commands::dispatch(&invocation, &root, &environment).unwrap();
        assert_eq!(output.exit_code, 3, "{command}");
        let report: Value = serde_json::from_str(
            &fs::read_to_string(root.join(format!("{command}_report.json"))).unwrap(),
        )
        .unwrap();
        assert_eq!(report["evidence"], "NOT_RUN", "{command}");
        let check = &report["extra"]["native_checks"][0];
        assert_eq!(check["observation"]["state"], "BLOCKED", "{command}");
        assert_eq!(
            check["limitations"],
            json!(["src/big.cpp was not analyzed: it exceeds the 16 MiB per-file input limit"]),
            "{command}"
        );
    }
}

/// Frozen dead.py:270 / dup.py:121: sources only under excluded vendor/generated directories are
/// a `DeadCode` / `DuplicateCode` SKIP naming the exclusion policy; dup.py:149 / dup.py:207: the
/// bounded C++ normalization (`SourceTokenizationError`, 500,000 indexed records) and comparison
/// (`DuplicateComparisonLimit`) stops.
#[test]
fn legacy_cpp_dup_and_dead_excluded_scope_and_duplicate_limits_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/metric-sources-oracle.json"
    ))
    .unwrap();
    let mut mismatches = vec![];
    for (tree, command) in [
        ("excluded-only", "dup"),
        ("excluded-only", "dead"),
        ("excluded-mixed", "dup"),
        ("excluded-mixed", "dead"),
        ("dup-records-limit", "dup"),
        ("dup-indexed-records-limit", "dup"),
        ("dup-window-occurrence-limit", "dup"),
        ("dup-raw-match-limit", "dup"),
    ] {
        let expected = &oracle["cases"][format!("{command}:{tree}")];
        let (_, mut actual) = run(&oracle, tree, command);
        if expected["exit_code"] == 1 && actual["exit_code"] == 3 {
            // Accepted exit policy (see above): required incomplete evidence exits 3.
            actual["exit_code"] = expected["exit_code"].clone();
        }
        if actual != *expected {
            mismatches.push(format!(
                "{command}:{tree}\n  rust   {actual}\n  frozen {expected}"
            ));
        }
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}
