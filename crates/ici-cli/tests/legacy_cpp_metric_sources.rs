//! Legacy `ici cognitive` / `ici complexity` over a C++ source that is not UTF-8 against frozen
//! v0.13.0 (`tests/rust-parity/cpp/metric-sources-oracle.json`, `capture_metric_sources.py`):
//! frozen reads every source strictly first, so cognitive keeps only a
//! `CppCognitiveSourceError` per unreadable source ("Cognitive complexity analysis did not
//! complete") and complexity one `CppComplexityAnalysisError` at the project root with no
//! function, both ERROR NOT_RUN; dup's source intake reports only the first such source
//! (`SourceInputError` 'invalid-utf8: Analysis source is not valid UTF-8'), and so does dead's
//! (frozen `DeadCodeEngine._collect_sources`, read even without a compilation database), which
//! then stays required. The `unterminated` frozen cases (brace-heuristic spans) are an
//! open difference recorded on `_cpp_cognitive.py:649`, not replayed here. The `empty` tree (a
//! C++ project with no source) replays frozen's not-applicable `ici exception` / `ici type`.
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
}

fn leak(path: String) -> &'static str {
    Box::leak(path.into_boxed_str())
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
        let actual = json!({"exit_code": output.exit_code, "status": report["status"],
            "evidence": report["evidence"], "required": report["required"], "summary": report["summary"], "targets": targets});
        (work, actual)
    }
}

#[test]
fn legacy_cpp_exception_and_type_without_sources_skip_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/metric-sources-oracle.json"
    ))
    .unwrap();
    for command in ["exception", "type"] {
        let (_, actual) = run(&oracle, "empty", command);
        assert_eq!(
            actual,
            oracle["cases"][format!("{command}:empty")],
            "{command}"
        );
    }
}

/// Recorded difference (dup.py:100, dead.py:111): a source over the application's 16 MiB
/// snapshot bound never reaches the frozen intake port, so instead of frozen's `SourceInputError`
/// 'too-large' target (ERROR, exit 1; replayed for 8-16 MiB above) the whole run is refused
/// fail-closed: the check is BLOCKED NOT_RUN with 'input snapshot failed' and exits 3.
#[test]
fn legacy_cpp_sources_over_the_snapshot_bound_block_the_run() {
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
            json!(["input snapshot failed"]),
            "{command}"
        );
    }
}
