//! `ici next plan` human output against the frozen v0.13.0 capture
//! (`tests/rust-parity/oracle/plan-text-oracle.json`, `capture_plan_text.py`, root decision E6).
//!
//! Every case replays the captured tree through the binary under the capture's environment
//! (empty HOME, PATH=/usr/bin:/bin without the C++ analysers the capture host lacked) and diffs
//! stdout line by line against frozen. A line may
//! differ only when a reviewed difference below names it for that case with its reason; every
//! reviewed difference must still be needed, so a fixed difference cannot linger.
use serde_json::Value;
use std::{fs, path::Path, process::Command, sync::OnceLock};

const FROZEN_PYTHON: &str = "/home/jihoon/projects/ici/.venv/bin/python";

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/oracle/plan-text-oracle.json"
    ))
    .unwrap()
}

#[derive(Clone, Copy, PartialEq, Debug)]
enum Side {
    Frozen,
    Native,
}

/// (cases, side, line prefix, reason).
type Reviewed = (&'static [&'static str], Side, &'static str, &'static str);

const TY: &str = "decisions.md 타입 기본값: an unconfigured type check runs ty (advisory), not mypy; the missing tool is named as frozen names one (`<tool> is not available`)";
const STEPS: &str = "native execution model (root decision D5; validation.json capability:runtime-integration): python.test runs the coverage probe and the visible conditional branches under the owned .ici/rust-v1 run directory, coverage reads that run's data, and compat-runtime is one check whose ADR-0008 metadata probe and compileall steps print as one chain";
const ALL: &[&str] = &[
    "python/default",
    "python/profile-fast",
    "python/profile-deep",
    "mixed/default",
    "mixed/python",
];
const ENV: &[&str] = &["python-env/default", "python-env/profile-fast"];

const REVIEWED: &[Reviewed] = &[
    (
        ALL,
        Side::Frozen,
        "  app.python.type: blocked — mypy is not available",
        TY,
    ),
    (
        ALL,
        Side::Native,
        "  app.python.type: blocked — ty is not available",
        TY,
    ),
    (
        ENV,
        Side::Frozen,
        "  app.python.type: blocked — mypy is not available",
        TY,
    ),
    (
        ENV,
        Side::Native,
        "  app.python.type: blocked — ty is not available",
        TY,
    ),
    (ENV, Side::Frozen, "demo: 17 check(s)", STEPS),
    (ENV, Side::Native, "demo: 16 check(s)", STEPS),
    (ENV, Side::Frozen, "  app.python.test: ", STEPS),
    (ENV, Side::Native, "  app.python.test: ", STEPS),
    (ENV, Side::Native, "    app.python.test.", STEPS),
    (ENV, Side::Frozen, "  app.python.coverage: ", STEPS),
    (ENV, Side::Native, "  app.python.coverage: ", STEPS),
    (ENV, Side::Native, "    app.python.coverage.", STEPS),
    (ENV, Side::Frozen, "  app.python.compat-runtime.", STEPS),
    (ENV, Side::Native, "  app.python.compat-runtime: ", STEPS),
    (ENV, Side::Native, "shared:", STEPS),
    (ENV, Side::Native, "  app.python.test runs once for ", STEPS),
];

/// Lines only in `frozen` and lines only in `native` (longest common subsequence).
fn diff<'a>(frozen: &[&'a str], native: &[&'a str]) -> Vec<(Side, &'a str)> {
    let (n, m) = (frozen.len(), native.len());
    let mut table = vec![vec![0usize; m + 1]; n + 1];
    for i in (0..n).rev() {
        for j in (0..m).rev() {
            table[i][j] = if frozen[i] == native[j] {
                table[i + 1][j + 1] + 1
            } else {
                table[i + 1][j].max(table[i][j + 1])
            };
        }
    }
    let (mut i, mut j, mut out) = (0, 0, vec![]);
    while i < n || j < m {
        if i < n && j < m && frozen[i] == native[j] {
            i += 1;
            j += 1;
        } else if j < m && (i == n || table[i][j + 1] >= table[i + 1][j]) {
            out.push((Side::Native, native[j]));
            j += 1;
        } else {
            out.push((Side::Frozen, frozen[i]));
            i += 1;
        }
    }
    out
}

/// `/usr/bin:/bin` as the capture host had it: the frozen capture had no clang-tidy or Clazy on
/// PATH (its plan says `clang-tidy is not available`), while CI installs both into /usr/bin. The
/// mirror links every other entry, so each remaining tool resolves exactly as before.
fn capture_path() -> &'static str {
    static MIRROR: OnceLock<(tempfile::TempDir, String)> = OnceLock::new();
    &MIRROR
        .get_or_init(|| {
            let mirror = tempfile::tempdir().unwrap();
            let mut directories = vec![];
            for source in ["/usr/bin", "/bin"] {
                let target = mirror
                    .path()
                    .join(source.trim_start_matches('/').replace('/', "-"));
                fs::create_dir(&target).unwrap();
                for entry in fs::read_dir(source).unwrap().flatten() {
                    let name = entry.file_name();
                    let text = name.to_string_lossy();
                    if text.starts_with("clang-tidy") || text.starts_with("clazy") {
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

fn replay(tree: &Value, argv: &[String], python: &str) -> (i32, String, String) {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("project");
    let home = temporary.path().join("home");
    fs::create_dir(&home).unwrap();
    for (name, text) in tree.as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap().replace(FROZEN_PYTHON, python)).unwrap();
    }
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(argv)
        .current_dir(&root)
        .env_clear()
        .env("HOME", &home)
        .env("PATH", capture_path())
        .env("LANG", "C.UTF-8")
        .output()
        .unwrap();
    assert!(
        !root.join(".ici").exists(),
        "a plan creates no state directory"
    );
    let text = |bytes: &[u8]| {
        String::from_utf8(bytes.to_vec())
            .unwrap()
            .replace(root.to_str().unwrap(), "<root>")
            .replace(python, FROZEN_PYTHON)
    };
    (
        output.status.code().unwrap(),
        text(&output.stdout),
        text(&output.stderr),
    )
}

fn compare(names: &[&str], python: &str) -> Vec<String> {
    let oracle = oracle();
    let mut used = vec![false; REVIEWED.len()];
    let mut report = vec![];
    for name in names {
        let case = &oracle["cases"][*name];
        let argv: Vec<String> = serde_json::from_value(case["argv"].clone()).unwrap();
        let tree = &oracle["trees"][case["tree"].as_str().unwrap()];
        let (exit, stdout, stderr) = replay(tree, &argv, python);
        assert_eq!(exit, case["exit_code"].as_i64().unwrap() as i32, "{name}");
        assert_eq!(stderr, case["stderr"].as_str().unwrap(), "{name}");
        let frozen: Vec<&str> = case["stdout"].as_str().unwrap().lines().collect();
        let native: Vec<&str> = stdout.lines().collect();
        for (side, line) in diff(&frozen, &native) {
            let reviewed = REVIEWED.iter().position(|(cases, reviewed, prefix, _)| {
                cases.contains(name) && *reviewed == side && line.starts_with(prefix)
            });
            match reviewed {
                Some(index) => used[index] = true,
                None => report.push(format!(
                    "{name}: unreviewed {side:?} line {line:?}\n{stdout}"
                )),
            }
        }
    }
    assert!(report.is_empty(), "{}", report.join("\n"));
    REVIEWED
        .iter()
        .zip(used)
        .filter(|((cases, ..), used)| !used && cases.iter().any(|case| names.contains(case)))
        .map(|((_, side, prefix, _), _)| format!("{side:?} {prefix}"))
        .collect()
}

/// Python-only, Python + C++ without a build, --python / --component selections, profiles and a
/// declared CMake build: frozen layout (no header for a component without checks, `builds:`,
/// scope coverage gaps without --require-full, `no checks in the requested languages` note,
/// `<tool> is not available`) with only the reviewed differences.
#[test]
fn plan_text_matches_frozen_layout_without_a_project_interpreter() {
    let mut names: Vec<&str> = ALL.to_vec();
    names.extend(["mixed/component-lib", "build/default"]);
    let unused = compare(&names, FROZEN_PYTHON);
    assert!(unused.is_empty(), "stale reviewed differences: {unused:?}");
}

/// A declared project interpreter: process argv lines, the `dependencies:` section, and profile
/// `fast` keeping python.coverage (frozen: `coverage json` is not a mutating prepare step).
#[test]
#[ignore = "explicit actual project interpreter (ICI_TEST_PYTHON)"]
fn plan_text_with_a_declared_interpreter_matches_frozen_dependencies_and_fast_profile() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON names a Python");
    assert!(Path::new(&python).is_file(), "{python}");
    let unused = compare(ENV, &python);
    assert!(unused.is_empty(), "stale reviewed differences: {unused:?}");
}

/// Failure: an unreviewed drift is reported, and the diff is exact about which side holds it.
#[test]
fn the_line_diff_names_each_side_of_a_drift() {
    let frozen = ["a", "b", "c"];
    let native = ["a", "x", "c", "d"];
    assert_eq!(
        diff(&frozen, &native),
        [
            (Side::Native, "x"),
            (Side::Frozen, "b"),
            (Side::Native, "d")
        ]
    );
    assert!(diff(&frozen, &frozen).is_empty());
}
