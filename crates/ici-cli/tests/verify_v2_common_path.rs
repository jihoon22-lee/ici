//! One schema 2 configuration reaches one decision under `verify` and `next verify`: the same
//! checks and states, gate, reasons, rules and exit, with tools, profiles and C++ preparation from
//! the common model. `analyze` is the static part of it and builds nothing. A typed engine command
//! keeps its subject. A declared build is built by the run unless it says `prepare = "explicit"`;
//! an undeclared one is a reported gap. A legacy project keeps frozen's tools and build preparation
//! under the legacy tests.
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

#[derive(Debug, PartialEq)]
struct Decision {
    exit: i32,
    selected: String,
    violations: bool,
    reasons: Vec<String>,
    checks: BTreeMap<String, String>,
    rules: BTreeSet<String>,
}

fn write_tree(root: &Path, files: &[(&str, String)]) {
    fs::create_dir_all(root).unwrap();
    fs::write(root.join(".hg"), "").unwrap();
    for (path, text) in files {
        let path = root.join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }
}

fn run_ici(root: &Path, path: &str, command: &[&str]) -> (i32, String, Decision) {
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(command)
        .current_dir(root)
        .env_clear()
        .env("HOME", root)
        .env("PATH", path)
        .stdin(Stdio::null())
        .output()
        .unwrap();
    let exit = output.status.code().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let saved = fs::read_to_string(root.join(".ici/rust-v1/result.json")).unwrap_or_else(|_| {
        panic!(
            "{command:?} saved no result: {exit} {stdout} {}",
            String::from_utf8_lossy(&output.stderr)
        )
    });
    let saved: Value = serde_json::from_str(&saved).unwrap();
    let text = root.display().to_string();
    let decision = Decision {
        exit,
        selected: saved["gate"]["selected"].as_str().unwrap().into(),
        violations: saved["gate"]["has_violations"].as_bool().unwrap(),
        reasons: saved["gate"]["reasons"]
            .as_array()
            .unwrap()
            .iter()
            .map(|reason| reason.as_str().unwrap().replace(&text, "<root>"))
            .collect(),
        checks: saved["checks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|check| {
                (
                    check["check_id"].as_str().unwrap().to_owned(),
                    check["state"].as_str().unwrap().to_owned(),
                )
            })
            .collect(),
        rules: saved["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| finding["rule_id"].as_str().unwrap().to_owned())
            .collect(),
    };
    (exit, stdout, decision)
}

/// Each command in its own copy, so no result or cache crosses between them.
fn decide(
    files: &[(&str, String)],
    prepare: &dyn Fn(&Path),
    path: &str,
    command: &[&str],
) -> Decision {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap().join("project");
    write_tree(&root, files);
    prepare(&root);
    run_ici(&root, path, command).2
}

/// `verify` and `next verify` agree; `analyze` runs a subset with no test or build check.
fn same_decision(
    files: &[(&str, String)],
    prepare: &dyn Fn(&Path),
    path: &str,
) -> (Decision, Decision) {
    let verify = decide(files, prepare, path, &["verify"]);
    let next = decide(files, prepare, path, &["next", "verify"]);
    assert_eq!(verify, next);
    let analyze = decide(files, prepare, path, &["analyze"]);
    for check in analyze.checks.keys() {
        assert!(verify.checks.contains_key(check), "{check}: {analyze:?}");
        assert!(!check.ends_with(".test") && !check.ends_with(".artifact"));
    }
    (verify, analyze)
}

fn none(_: &Path) {}

fn tool_directory(variable: &str) -> Option<String> {
    let tool = PathBuf::from(std::env::var(variable).ok()?);
    Some(tool.parent()?.display().to_string())
}

fn python_files(config: &str) -> Vec<(&'static str, String)> {
    vec![
        (
            "src/a.py",
            "def typed() -> int:\n    return 'x'\n\n\ndef nested(value):\n    if value:\n        for item in value:\n            if item:\n                while item:\n                    if item > 1:\n                        item -= 1\n                    else:\n                        break\n    return value\n".into(),
        ),
        (
            "pyproject.toml",
            "[project]\nname='q'\nversion='1'\nrequires-python='>=3.10'\n".into(),
        ),
        ("ici.toml", format!("schema_version = 2\n{config}")),
    ]
}

/// The default `ty` provider is advisory in the common model: its findings are reported and do not
/// fail the gate, under `analyze` and a typed `ici type` alike.
const GATE_OF_DEFAULT_TY: &str = "PASS";

/// Off so the run is decided by what is under test and not by a missing interpreter.
const NO_RUNTIME: &str = "[checks.\"python.test\"]\nenabled = false\n[checks.\"python.coverage\"]\nenabled = false\n[checks.\"python.compat-runtime\"]\nenabled = false\n";

#[test]
fn a_missing_default_type_tool_blocks_under_every_command() {
    let files = python_files(NO_RUNTIME);
    let (verify, analyze) = same_decision(&files, &none, "/nonexistent");
    assert_eq!(verify.exit, 3);
    assert_eq!(verify.selected, "INCOMPLETE");
    assert_eq!(verify.checks["python.type"], "BLOCKED");
    assert!(
        verify
            .reasons
            .iter()
            .any(|reason| reason == "project.python.type: ty is not available"),
        "{verify:?}"
    );
    assert_eq!(analyze.selected, "INCOMPLETE");
    assert!(verify.checks.contains_key("python.cognitive"), "{verify:?}");
    for legacy_only in ["python.annotations", "python.compile-db", "python.tsan"] {
        assert!(!verify.checks.contains_key(legacy_only), "{verify:?}");
    }
}

/// A fake `mypy` on PATH is not a substitute for the resolved `ty`, and a declared `mypy` provider
/// that is missing names `mypy`: the command name does not choose the tool.
#[test]
fn the_declared_provider_decides_which_tool_is_missing() {
    let tools = tempfile::tempdir().unwrap();
    let mypy = tools.path().join("mypy");
    fs::write(&mypy, "#!/bin/sh\nexit 0\n").unwrap();
    fs::set_permissions(&mypy, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let path = format!("{}:/nonexistent", tools.path().display());
    let (verify, _) = same_decision(&python_files(NO_RUNTIME), &none, &path);
    assert!(
        verify
            .reasons
            .iter()
            .any(|reason| reason == "project.python.type: ty is not available"),
        "{verify:?}"
    );
    let declared = format!("{NO_RUNTIME}[python]\ntype_provider = \"mypy\"\n");
    let (verify, _) = same_decision(&python_files(&declared), &none, "/nonexistent");
    assert!(
        verify
            .reasons
            .iter()
            .any(|reason| reason == "project.python.type: mypy is not available"),
        "{verify:?}"
    );
}

/// The real tools: the default provider reports through `ty`, a declared one through `mypy`'s own
/// output and not the legacy `mypy.error` view, and an explicit tool path overrides PATH.
#[test]
#[ignore = "needs real ty, Ruff and mypy (ICI_TEST_TY, ICI_TEST_RUFF, ICI_TEST_PYTHON)"]
fn real_type_tools_follow_the_resolved_provider_under_every_command() {
    let ty = std::env::var("ICI_TEST_TY").unwrap();
    let path = format!(
        "{}:{}:{}:/usr/bin:/bin",
        tool_directory("ICI_TEST_TY").unwrap(),
        tool_directory("ICI_TEST_RUFF").unwrap(),
        tool_directory("ICI_TEST_PYTHON").unwrap()
    );
    for (config, rule) in [
        (NO_RUNTIME.to_owned(), "ty.invalid-return-type"),
        (
            format!("profile = \"fast\"\n{NO_RUNTIME}"),
            "ty.invalid-return-type",
        ),
        (
            format!("profile = \"deep\"\n{NO_RUNTIME}"),
            "ty.invalid-return-type",
        ),
        (
            format!("{NO_RUNTIME}[python]\ntype_provider = \"mypy\"\n"),
            "mypy.return-value",
        ),
    ] {
        let files = python_files(&config);
        let (verify, analyze) = same_decision(&files, &none, &path);
        assert!(verify.rules.contains(rule), "{config}: {verify:?}");
        assert!(!verify.rules.contains("mypy.error"), "{verify:?}");
        assert!(analyze.rules.contains(rule), "{config}: {analyze:?}");
    }
    // The engine command keeps its subject and the same provider.
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap().join("project");
    let override_config = format!("{NO_RUNTIME}[tools.ty]\npath = {ty:?}\n");
    write_tree(&root, &python_files(&override_config));
    let (exit, _, typed) = run_ici(&root, "/nonexistent", &["type"]);
    assert_eq!(exit, typed.exit, "{typed:?}");
    assert_eq!(typed.selected, GATE_OF_DEFAULT_TY, "{typed:?}");
    assert_eq!(
        typed.checks.keys().map(String::as_str).collect::<Vec<_>>(),
        ["python.type"]
    );
    assert!(typed.rules.contains("ty.invalid-return-type"), "{typed:?}");
}

const CMAKE: &str = "cmake_minimum_required(VERSION 3.16)\nproject(demo CXX)\nenable_testing()\nset(CMAKE_EXPORT_COMPILE_COMMANDS ON)\nadd_library(lib STATIC src/lib.cpp)\nadd_executable(unit tests/unit.cpp)\ntarget_link_libraries(unit lib)\nadd_test(NAME unit COMMAND unit)\n";
const DECLARED: &str = "schema_version = 2\nname = \"demo\"\nversion = \"1.0.0\"\n[builds.native]\nsystem = \"cmake\"\nproject = \"CMakeLists.txt\"\ndirectory = \"out\"\nvariant = \"release\"\nprepare = \"explicit\"\n[checks.\"cpp.coverage\"]\nenabled = false\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"cpp\"]\nbuild = \"native\"\nsources = [\"src/**\"]\n";

fn cpp_files(library: &str, test: &str, config: &str) -> Vec<(&'static str, String)> {
    vec![
        ("CMakeLists.txt", CMAKE.into()),
        ("src/lib.cpp", library.into()),
        ("tests/unit.cpp", test.into()),
        ("ici.toml", config.into()),
    ]
}

const LIBRARY: &str = "int answer() { return 42; }\n";
const TEST: &str = "int answer();\nint main() { return answer() == 42 ? 0 : 1; }\n";

fn cmake_path() -> String {
    format!(
        "{}:{}:/usr/bin:/bin",
        tool_directory("ICI_TEST_CMAKE").unwrap(),
        tool_directory("ICI_TEST_CXX").unwrap()
    )
}

fn built(root: &Path) {
    let path = cmake_path();
    let cmake = std::env::var("ICI_TEST_CMAKE").unwrap();
    for arguments in [vec!["-S", ".", "-B", "out"], vec!["--build", "out", "-j2"]] {
        let status = Command::new(&cmake)
            .args(arguments)
            .current_dir(root)
            .env_clear()
            .env("HOME", root)
            .env("PATH", &path)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .unwrap();
        assert!(status.success());
    }
}

#[test]
#[ignore = "needs real CMake and g++ (ICI_TEST_CMAKE, ICI_TEST_CXX)"]
fn a_declared_cpp_build_reaches_one_decision_when_clean_or_violating() {
    let path = cmake_path();
    let (clean, static_part) = same_decision(&cpp_files(LIBRARY, TEST, DECLARED), &built, &path);
    assert_eq!(clean.selected, "PASS", "{clean:?}");
    assert_eq!(clean.exit, 0);
    assert_eq!(clean.checks["cpp.test"], "SUCCEEDED");
    assert_eq!(clean.checks["cpp.compile"], "SUCCEEDED");
    assert_eq!(static_part.selected, "PASS", "{static_part:?}");
    for legacy_only in [
        "cpp.type",
        "cpp.resource",
        "cpp.security",
        "cpp.python-compat",
    ] {
        assert!(!clean.checks.contains_key(legacy_only), "{clean:?}");
    }
    let failing = "int answer();\nint main() { return answer() == 41 ? 0 : 1; }\n";
    let (failed, static_part) =
        same_decision(&cpp_files(LIBRARY, failing, DECLARED), &built, &path);
    assert_eq!(failed.selected, "FAIL", "{failed:?}");
    assert_eq!(failed.exit, 1);
    assert!(failed.rules.contains("ctest.failed"), "{failed:?}");
    assert_eq!(static_part.selected, "PASS", "{static_part:?}");
    let branches: String = (0..40)
        .map(|index| format!("  if (t == {index}) {{ t += {index}; }}\n"))
        .collect();
    let duplicated = format!("int answer() {{\n  int t = 0;\n{branches}  return t + 42;\n}}\n");
    let (violating, static_part) =
        same_decision(&cpp_files(&duplicated, TEST, DECLARED), &built, &path);
    assert_eq!(violating.selected, "FAIL", "{violating:?}");
    assert!(violating.rules.contains("dup.type2-clone"), "{violating:?}");
    assert!(
        violating.rules.contains("metrics.complexity"),
        "{violating:?}"
    );
    assert_eq!(static_part.selected, "FAIL", "{static_part:?}");
}

const ICI_BUILDS: &str = "schema_version = 2\nname = \"demo\"\nversion = \"v1.0.0\"\n[builds.native]\nsystem = \"cmake\"\nproject = \"CMakeLists.txt\"\ndirectory = \"out\"\nartifacts = [\"unit\"]\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"cpp\"]\nbuild = \"native\"\nsources = [\"src/**\"]\n";

fn project_with(files: &[(&str, String)]) -> (tempfile::TempDir, PathBuf) {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap().join("project");
    write_tree(&root, files);
    (directory, root)
}

/// The run builds a declared build itself, with the same decision under both commands; `analyze`
/// builds nothing. Success, a real test failure and a real build failure stay three outcomes.
#[test]
#[ignore = "needs real CMake and g++ (ICI_TEST_CMAKE, ICI_TEST_CXX)"]
fn a_declared_build_is_built_by_the_run_and_never_by_analyze() {
    let path = cmake_path();
    let files = cpp_files(LIBRARY, TEST, ICI_BUILDS);
    let (clean, static_part) = same_decision(&files, &none, &path);
    assert_eq!(clean.selected, "PASS", "{clean:?}");
    assert_eq!(clean.exit, 0);
    for check in ["cpp.compile", "cpp.test", "cpp.coverage", "cpp.artifact"] {
        assert_eq!(clean.checks[check], "SUCCEEDED", "{check}: {clean:?}");
    }
    assert_eq!(static_part.selected, "INCOMPLETE", "{static_part:?}");
    assert!(
        static_part
            .reasons
            .iter()
            .any(|reason| reason.contains("is built by `ici verify`")),
        "{static_part:?}"
    );
    let (_directory, root) = project_with(&files);
    let (exit, _, analyzed) = run_ici(&root, &path, &["analyze"]);
    assert_eq!(exit, 3, "{analyzed:?}");
    assert!(!root.join("v1.0.0").exists());
    assert!(!root.join("out").exists());
    assert!(!root.join(".ici/rust-v1/build").exists());
    let (exit, _, built) = run_ici(&root, &path, &["build"]);
    assert_eq!(exit, 0, "{built:?}");
    assert_eq!(built.checks["cpp.artifact"], "SUCCEEDED", "{built:?}");
    assert!(root.join("v1.0.0/x86_64/bin/unit").is_file());
    let (exit, _, tested) = run_ici(&root, &path, &["test"]);
    assert_eq!(exit, 0, "{tested:?}");

    let failing = "int answer();\nint main() { return answer() == 41 ? 0 : 1; }\n";
    let (failed, _) = same_decision(&cpp_files(LIBRARY, failing, ICI_BUILDS), &none, &path);
    assert_eq!(failed.selected, "FAIL", "{failed:?}");
    assert_eq!(failed.exit, 1);
    assert!(failed.rules.contains("ctest.failed"), "{failed:?}");

    let broken = "int answer( { return 42; }\n";
    let files = cpp_files(broken, TEST, ICI_BUILDS);
    let (not_built, _) = same_decision(&files, &none, &path);
    assert_eq!(not_built.selected, "INCOMPLETE", "{not_built:?}");
    assert_eq!(not_built.exit, 3);
    assert!(not_built.violations, "{not_built:?}");
    assert_eq!(not_built.checks["cpp.compile"], "BLOCKED", "{not_built:?}");
    assert!(
        not_built
            .reasons
            .iter()
            .any(|reason| reason.contains("preparation failed")),
        "{not_built:?}"
    );
    let (_directory, root) = project_with(&files);
    let (exit, _, refused) = run_ici(&root, &path, &["build"]);
    assert_eq!(exit, 1, "{refused:?}");
    assert!(refused.rules.contains("build.adapter"), "{refused:?}");
    assert!(!root.join("v1.0.0").exists());
}

/// Reading an existing build tree is not a request to export it: only a build the run made writes
/// local artifacts. `prepare = "explicit"` and a tree that already exists stay untouched, even when
/// an isolated link-reachability build is made beside them.
#[test]
#[ignore = "needs real CMake and g++ (ICI_TEST_CMAKE, ICI_TEST_CXX)"]
fn a_read_only_build_tree_is_never_exported_or_rebuilt() {
    let path = cmake_path();
    let existing = ICI_BUILDS.replace(
        "[[components]]",
        "[checks.\"cpp.coverage\"]\nenabled = false\n[[components]]",
    );
    let explicit = existing.replace(
        "artifacts = [\"unit\"]",
        "artifacts = [\"unit\"]\nprepare = \"explicit\"",
    );
    for config in [&existing, &explicit] {
        let (_directory, root) = project_with(&cpp_files(LIBRARY, TEST, config));
        built(&root);
        let binary = root.join("out/unit");
        let before = fs::read(&binary).unwrap();
        let stamp = fs::metadata(&binary).unwrap().modified().unwrap();
        for command in [vec!["verify"], vec!["next", "verify"], vec!["build"]] {
            let (_, _, decision) = run_ici(&root, &path, &command);
            assert_eq!(
                decision.checks.get("cpp.artifact").map(String::as_str),
                Some("SUCCEEDED"),
                "{config}\n{decision:?}"
            );
            assert!(
                !root.join("v1.0.0").exists(),
                "{command:?} exported a read-only tree: {config}"
            );
        }
        assert_eq!(fs::read(&binary).unwrap(), before, "{config}");
        assert_eq!(
            fs::metadata(&binary).unwrap().modified().unwrap(),
            stamp,
            "{config}"
        );
    }
}

/// An isolated link-reachability build beside an existing tree answers the linker scope only: it
/// exports nothing and leaves the user's tree alone.
#[test]
#[ignore = "needs real CMake, g++, readelf and addr2line (ICI_TEST_CMAKE, ICI_TEST_CXX)"]
fn a_linker_build_beside_an_existing_tree_exports_nothing() {
    let path = cmake_path();
    let config = "schema_version = 2\nname = \"demo\"\nversion = \"v1.0.0\"\n[engines.dead]\ncpp_unused = \"off\"\ncpp_linker = \"required\"\n[builds.native]\nsystem = \"cmake\"\nproject = \"CMakeLists.txt\"\ndirectory = \"out\"\nartifacts = [\"one\"]\n[checks.\"cpp.dead\"]\nenabled = true\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"cpp\"]\nbuild = \"native\"\nsources = [\"*.cpp\"]\n";
    let (_directory, root) = project_with(&linker_tree(LINKER_DEAD, config));
    built(&root);
    let binary = root.join("out/one");
    let before = fs::read(&binary).unwrap();
    let (exit, _, dead) = run_ici(&root, &path, &["dead"]);
    assert_eq!(exit, 1, "{dead:?}");
    assert!(
        dead.rules.contains("dead.cpp-linker-discarded-function"),
        "{dead:?}"
    );
    let (_, _, verify) = run_ici(&root, &path, &["verify"]);
    assert_eq!(verify.checks["cpp.dead"], "SUCCEEDED", "{verify:?}");
    assert_eq!(verify.checks["cpp.artifact"], "SUCCEEDED", "{verify:?}");
    assert!(
        verify
            .reasons
            .iter()
            .all(|reason| !reason.contains("inputs changed")),
        "{verify:?}"
    );
    assert!(!root.join("v1.0.0").exists());
    assert_eq!(fs::read(&binary).unwrap(), before);
}

/// The local artifacts a run wrote are its own to replace: an identical re-run is safe, and a file
/// the user changed or owns is reported and kept.
#[test]
#[ignore = "needs real CMake and g++ (ICI_TEST_CMAKE, ICI_TEST_CXX)"]
fn local_artifacts_never_overwrite_what_the_run_does_not_own() {
    let path = cmake_path();
    let (_directory, root) = project_with(&cpp_files(LIBRARY, TEST, ICI_BUILDS));
    let source = fs::read(root.join("src/lib.cpp")).unwrap();
    let published = root.join("v1.0.0/x86_64/bin/unit");
    let (exit, _, first) = run_ici(&root, &path, &["build"]);
    assert_eq!(exit, 0, "{first:?}");
    let produced = fs::read(&published).unwrap();
    let (exit, _, again) = run_ici(&root, &path, &["build"]);
    assert_eq!(exit, 0, "{again:?}");
    assert_eq!(fs::read(&published).unwrap(), produced);
    assert_eq!(fs::read(root.join("src/lib.cpp")).unwrap(), source);
    fs::write(&published, "changed by the user\n").unwrap();
    let (exit, _, refused) = run_ici(&root, &path, &["build"]);
    assert_ne!(exit, 0, "{refused:?}");
    assert_eq!(fs::read(&published).unwrap(), b"changed by the user\n");

    let (_directory, root) = project_with(&cpp_files(LIBRARY, TEST, ICI_BUILDS));
    let unowned = root.join("v1.0.0/x86_64/bin/unit");
    fs::create_dir_all(unowned.parent().unwrap()).unwrap();
    fs::write(&unowned, "not from this tool\n").unwrap();
    let (exit, _, refused) = run_ici(&root, &path, &["build"]);
    assert_ne!(exit, 0, "{refused:?}");
    assert_eq!(fs::read(&unowned).unwrap(), b"not from this tool\n");
}

const LINKER_KEPT: &str =
    "__attribute__((noinline,visibility(\"hidden\"))) int kept_for_one(){return 1;}\n";
const LINKER_DEAD: &str = "__attribute__((noinline,visibility(\"hidden\"))) int kept_for_one(){return 1;}\n__attribute__((noinline,visibility(\"hidden\"))) int dead_everywhere(){return 2;}\n";

fn linker_tree(common: &str, config: &str) -> Vec<(&'static str, String)> {
    vec![
        (
            "CMakeLists.txt",
            "cmake_minimum_required(VERSION 3.16)\nproject(linker_cli LANGUAGES CXX)\nset(CMAKE_EXPORT_COMPILE_COMMANDS ON)\nadd_library(common OBJECT common.cpp)\nadd_executable(one one.cpp $<TARGET_OBJECTS:common>)\nadd_executable(two two.cpp $<TARGET_OBJECTS:common>)\n".into(),
        ),
        ("common.cpp", common.into()),
        (
            "one.cpp",
            "int kept_for_one(); int main(){return kept_for_one();}\n".into(),
        ),
        ("two.cpp", "int main(){return 0;}\n".into()),
        ("ici.toml", config.into()),
    ]
}

/// Opt-in linker reachability works on a schema 2 project with a declared build: a symbol no target
/// keeps is a violation, a clean tree passes, and without a build it is reported as unavailable.
#[test]
#[ignore = "needs real CMake, g++, readelf and addr2line (ICI_TEST_CMAKE, ICI_TEST_CXX)"]
fn linker_reachability_runs_on_a_declared_build() {
    let path = cmake_path();
    let config = "schema_version = 2\nname = \"demo\"\nversion = \"v1.0.0\"\n[engines.dead]\ncpp_unused = \"off\"\ncpp_linker = \"required\"\n[builds.native]\nsystem = \"cmake\"\nproject = \"CMakeLists.txt\"\n[checks.\"cpp.dead\"]\nenabled = true\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"cpp\"]\nbuild = \"native\"\nsources = [\"*.cpp\"]\n";
    let (_directory, root) = project_with(&linker_tree(LINKER_DEAD, config));
    let (exit, _, found) = run_ici(&root, &path, &["dead"]);
    assert_eq!(exit, 1, "{found:?}");
    assert_eq!(found.selected, "FAIL", "{found:?}");
    assert!(
        found.rules.contains("dead.cpp-linker-discarded-function"),
        "{found:?}"
    );
    let (verify, analyze) = same_decision(&linker_tree(LINKER_DEAD, config), &none, &path);
    assert!(
        verify.rules.contains("dead.cpp-linker-discarded-function"),
        "{verify:?}"
    );
    assert_eq!(analyze.checks["cpp.dead"], "BLOCKED", "{analyze:?}");
    let (_directory, root) = project_with(&linker_tree(LINKER_KEPT, config));
    let (exit, _, clean) = run_ici(&root, &path, &["dead"]);
    assert_eq!(exit, 0, "{clean:?}");
    assert_eq!(clean.selected, "PASS", "{clean:?}");
}

/// The build was declared but not run, or never declared: static checks still run, the reason names
/// what is missing, and ici builds nothing and falls back to no direct compiler.
#[test]
fn a_cpp_project_without_evidence_is_incomplete_and_says_what_to_declare() {
    let path = "/nonexistent";
    let undeclared = "schema_version = 2\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"cpp\"]\nsources = [\"src/**\"]\n";
    let (verify, analyze) = same_decision(&cpp_files(LIBRARY, TEST, undeclared), &none, path);
    assert_eq!(verify.exit, 3);
    assert_eq!(verify.selected, "INCOMPLETE");
    assert_eq!(verify.checks["cpp.compile"], "BLOCKED");
    assert_eq!(verify.checks["cpp.complexity"], "SUCCEEDED");
    assert!(
        verify
            .reasons
            .iter()
            .any(|reason| reason.contains("declare [builds.<id>]")),
        "{verify:?}"
    );
    assert_eq!(analyze.selected, "INCOMPLETE");
    let (_directory, root) = project_with(&cpp_files(LIBRARY, TEST, undeclared));
    let (exit, _, build) = run_ici(&root, path, &["build"]);
    assert_eq!(exit, 3, "{build:?}");
    assert!(
        build
            .reasons
            .iter()
            .any(|reason| reason.contains("[builds.<id>]")),
        "{build:?}"
    );
    // A declared build the run cannot start says which tool is missing and never passes.
    let (missing, _) = same_decision(&cpp_files(LIBRARY, TEST, ICI_BUILDS), &none, path);
    assert_eq!(missing.selected, "INCOMPLETE", "{missing:?}");
    assert_eq!(missing.exit, 3);
    assert_eq!(missing.checks["cpp.compile"], "BLOCKED", "{missing:?}");
    assert_eq!(missing.checks["cpp.complexity"], "SUCCEEDED", "{missing:?}");
    assert!(
        missing
            .reasons
            .iter()
            .any(|reason| reason.contains("cannot be prepared") && reason.contains("cmake")),
        "{missing:?}"
    );
    // `prepare = "explicit"` leaves the build to the user, so nothing was built and the reason says
    // how to get one.
    let (declared, _) = same_decision(&cpp_files(LIBRARY, TEST, DECLARED), &none, path);
    assert_eq!(declared.selected, "INCOMPLETE");
    assert!(
        declared
            .reasons
            .iter()
            .any(|reason| reason.contains("yours to run")
                && reason.contains("out/compile_commands.json")),
        "{declared:?}"
    );
}

/// Commands whose engine has no C++ check answer "not applicable" rather than refusing the run, and
/// a C++ linker policy nothing can prepare is reported, not silently dropped.
#[test]
fn typed_engine_commands_keep_their_subject_on_a_cpp_project() {
    let undeclared = "schema_version = 2\n[engines.dead]\ncpp_unused = \"off\"\ncpp_linker = \"required\"\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"cpp\"]\nsources = [\"src/**\"]\n";
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap().join("project");
    write_tree(&root, &cpp_files(LIBRARY, TEST, undeclared));
    for (command, check) in [
        ("type", "cpp.type"),
        ("security", "cpp.security"),
        ("resource", "cpp.resource"),
    ] {
        let (exit, _, decision) = run_ici(&root, "/nonexistent", &[command]);
        assert_eq!(exit, 2, "{command}: {decision:?}");
        assert_eq!(decision.checks[check], "SUCCEEDED", "{decision:?}");
    }
    let (exit, _, dead) = run_ici(&root, "/nonexistent", &["dead"]);
    assert_eq!(exit, 3, "{dead:?}");
    assert_eq!(dead.checks["cpp.dead"], "BLOCKED", "{dead:?}");
    assert!(
        dead.reasons
            .iter()
            .any(|reason| reason.contains("link-reachability")),
        "{dead:?}"
    );
    // `ici lint` pulls in the compile step its diagnostics need, so its reason is the missing
    // database and not an internal missing capability.
    let (exit, _, lint) = run_ici(&root, "/nonexistent", &["lint"]);
    assert_eq!(exit, 3, "{lint:?}");
    assert!(
        lint.reasons
            .iter()
            .all(|reason| !reason.contains("which no selected check produces")),
        "{lint:?}"
    );
    assert!(
        lint.reasons
            .iter()
            .any(|reason| reason.contains("declare [builds.<id>]")),
        "{lint:?}"
    );
}

/// C++ metrics are estimated, so a threshold breach alone does not fail the common gate: the engine
/// command's exit agrees with what it saved and says so beside its failed line.
#[test]
fn an_estimated_engine_finding_exits_with_the_saved_gate() {
    let branches: String = (0..40)
        .map(|index| format!("  if (t == {index}) {{ t += {index}; }}\n"))
        .collect();
    let source = format!("int answer() {{\n  int t = 0;\n{branches}  return t + 42;\n}}\n");
    let config = "schema_version = 2\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"cpp\"]\nsources = [\"src/**\"]\n";
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap().join("project");
    write_tree(&root, &cpp_files(&source, TEST, config));
    let (exit, stdout, decision) = run_ici(&root, "/nonexistent", &["complexity"]);
    assert_eq!(decision.selected, "PASS", "{decision:?}");
    assert_eq!(exit, 0, "{stdout}");
    assert!(
        decision.rules.contains("metrics.complexity"),
        "{decision:?}"
    );
    assert!(
        stdout.contains("Gate: PASS — estimated findings do not count against the gate"),
        "{stdout}"
    );
    // A measured engine failure still exits 1.
    let (exit, _, decision) = run_ici(&root, "/nonexistent", &["dup"]);
    assert_eq!(decision.selected, "FAIL", "{decision:?}");
    assert_eq!(exit, 1);
}

/// Typing a command selects a family; it does not re-enable a check the project turned off. A
/// disabled check neither runs nor brings its missing tool into the run, a disabled dependency is
/// named rather than re-enabled, and a family left with nothing to run is refused, not passed.
#[test]
fn an_explicit_enabled_false_survives_a_typed_command() {
    let component = "[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"python\"]\nsources = [\"src/**\"]\n";
    let python = |config: &str| -> Vec<(&'static str, String)> {
        vec![
            ("src/a.py", "def one():\n    return 1\n".into()),
            ("ici.toml", format!("schema_version = 2\n{config}")),
        ]
    };
    // A check turned off is absent; its sibling in the family runs and reports its own gap.
    let off = format!("[checks.\"python.coverage\"]\nenabled = false\n{component}");
    let (_directory, root) = project_with(&python(&off));
    let (exit, _, tested) = run_ici(&root, "/nonexistent", &["test"]);
    assert!(!tested.checks.contains_key("python.coverage"), "{tested:?}");
    assert!(tested.checks.contains_key("python.test"), "{tested:?}");
    assert!(
        tested
            .reasons
            .iter()
            .all(|reason| !reason.contains("python.coverage")),
        "{tested:?}"
    );
    assert_eq!(exit, tested.exit);
    // Everything in the family off: a refusal naming that nothing was checked, never a PASS.
    let all_off = format!("[checks.\"python.complexity\"]\nenabled = false\n{component}");
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap().join("project");
    write_tree(&root, &python(&all_off));
    let refused = Command::new(env!("CARGO_BIN_EXE_ici"))
        .arg("complexity")
        .current_dir(&root)
        .env_clear()
        .env("HOME", &root)
        .env("PATH", "/nonexistent")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    assert_eq!(refused.status.code(), Some(2));
    assert!(
        String::from_utf8_lossy(&refused.stderr).contains("no component selected a check"),
        "{}",
        String::from_utf8_lossy(&refused.stderr)
    );
    assert!(!root.join(".ici/rust-v1/result.json").exists());
    // The engine table turns the family off the same way.
    let engine_off = format!("[engines.complexity]\nenabled = false\n{component}");
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap().join("project");
    write_tree(&root, &python(&engine_off));
    let refused = Command::new(env!("CARGO_BIN_EXE_ici"))
        .arg("complexity")
        .current_dir(&root)
        .env_clear()
        .env("HOME", &root)
        .env("PATH", "/nonexistent")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    assert_eq!(refused.status.code(), Some(2));
    // A component's own choice outranks the project's, in both directions.
    let component_on = "[checks.\"python.complexity\"]\nenabled = false\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"python\"]\nsources = [\"src/**\"]\n[components.checks.\"python.complexity\"]\nenabled = true\n";
    let (_directory, root) = project_with(&python(component_on));
    let (_, _, on) = run_ici(&root, "/nonexistent", &["complexity"]);
    assert_eq!(on.checks["python.complexity"], "SUCCEEDED", "{on:?}");
    // A default-off family is still chosen by typing it: only an explicit off keeps it off.
    let (_directory, root) = project_with(&cpp_files(
        LIBRARY,
        TEST,
        "schema_version = 2\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"cpp\"]\nsources = [\"src/**\"]\n",
    ));
    let (_, _, dead) = run_ici(&root, "/nonexistent", &["dead"]);
    assert!(dead.checks.contains_key("cpp.dead"), "{dead:?}");
    // A disabled dependency is not re-enabled: the check that needs it names it.
    let (_directory, root) = project_with(&[
        ("src/lib.cpp", "int answer() { return 42; }\n".into()),
        (
            "ici.toml",
            "schema_version = 2\n[checks.\"cpp.compile\"]\nenabled = false\n[builds.native]\ndirectory = \"build\"\nprepare = \"explicit\"\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"cpp\"]\nsources = [\"src/**\"]\nbuild = \"native\"\n".into(),
        ),
    ]);
    let source = root.join("src/lib.cpp");
    fs::create_dir_all(root.join("build")).unwrap();
    fs::write(
        root.join("build/compile_commands.json"),
        serde_json::to_string(&serde_json::json!([{
            "directory": root.join("build"),
            "arguments": ["/usr/bin/g++", "-c", source, "-o", "lib.o"],
            "file": source,
        }]))
        .unwrap(),
    )
    .unwrap();
    let (exit, _, lint) = run_ici(&root, "/nonexistent", &["lint"]);
    assert_eq!(exit, 3, "{lint:?}");
    assert!(!lint.checks.contains_key("cpp.compile"), "{lint:?}");
    assert_eq!(lint.checks["cpp.diagnostics"], "BLOCKED", "{lint:?}");
    assert!(
        lint.reasons
            .iter()
            .any(|reason| reason
                .contains("cpp.compile produces it and is disabled (enabled = false)")),
        "{lint:?}"
    );
}

/// A declared C++ build with coverage off runs its tests and is not held back by coverage alone.
#[test]
#[ignore = "needs real CMake and g++ (ICI_TEST_CMAKE, ICI_TEST_CXX)"]
fn a_disabled_coverage_does_not_decide_a_typed_test_run() {
    let path = cmake_path();
    let (_directory, root) = project_with(&cpp_files(LIBRARY, TEST, DECLARED));
    built(&root);
    let (exit, _, tested) = run_ici(&root, &path, &["test"]);
    assert_eq!(exit, 0, "{tested:?}");
    assert_eq!(tested.selected, "PASS", "{tested:?}");
    assert_eq!(tested.checks["cpp.test"], "SUCCEEDED", "{tested:?}");
    assert!(!tested.checks.contains_key("cpp.coverage"), "{tested:?}");
    let failing = "int answer();\nint main() { return answer() == 41 ? 0 : 1; }\n";
    let (_directory, root) = project_with(&cpp_files(LIBRARY, failing, DECLARED));
    built(&root);
    let (exit, _, failed) = run_ici(&root, &path, &["test"]);
    assert_eq!(exit, 1, "{failed:?}");
    assert!(failed.rules.contains("ctest.failed"), "{failed:?}");
}
