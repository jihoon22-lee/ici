//! Frozen legacy `ici python-compat --report` wheel discovery (`analysis/_python_packaging.
//! _wheel_paths`), replayed from `tests/rust-parity/validation/wheel-discovery-oracle.json`
//! (`capture_wheel_discovery.py`, frozen v0.13.0): the configured `wheel_globs` select the
//! wheels the package contract inspects, a symlinked, dangling, escaping or non-file candidate
//! stops the analysis with frozen's refusal, and `max_wheels` bounds the matches. Exit code,
//! engine status and summary, every target and every finding rule equal frozen; only the
//! interpreter named by the passing `PythonRuntime:Verified` target is the host's.
use serde_json::{Value, json};
use std::{fs, os::unix::fs::symlink, path::Path, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/wheel-discovery-oracle.json");
const NAME: &str = "demo-1.2.3-py3-none-any.whl";

fn layout(root: &Path, outside: &Path, kind: &str, oracle: &Value) {
    fs::create_dir_all(root.join("demo")).unwrap();
    fs::write(root.join("demo/__init__.py"), "VALUE = 1\n").unwrap();
    fs::write(
        root.join("pyproject.toml"),
        oracle["pyproject"].as_str().unwrap(),
    )
    .unwrap();
    let dist = root.join("dist");
    fs::create_dir(&dist).unwrap();
    use base64::Engine;
    let payload = base64::engine::general_purpose::STANDARD
        .decode(oracle["wheel_base64"].as_str().unwrap())
        .unwrap();
    if matches!(kind, "regular" | "with-text" | "two-wheels") {
        fs::write(dist.join(NAME), &payload).unwrap();
    }
    match kind {
        "with-text" => fs::write(dist.join("notes.txt"), "notes\n").unwrap(),
        "two-wheels" => fs::write(dist.join("demo-1.2.3-py2.py3-none-any.whl"), &payload).unwrap(),
        "symlink-outside" => {
            fs::write(outside.join(NAME), &payload).unwrap();
            symlink(outside.join(NAME), dist.join(NAME)).unwrap();
        }
        "symlink-inside" => {
            fs::create_dir(root.join("keep")).unwrap();
            fs::write(root.join("keep").join(NAME), &payload).unwrap();
            symlink(root.join("keep").join(NAME), dist.join(NAME)).unwrap();
        }
        "dangling" => symlink(root.join("nowhere.whl"), dist.join(NAME)).unwrap(),
        "directory" => fs::create_dir(dist.join(NAME)).unwrap(),
        "dist-symlink-outside" => {
            fs::remove_dir(&dist).unwrap();
            fs::create_dir(outside.join("d")).unwrap();
            fs::write(outside.join("d").join(NAME), &payload).unwrap();
            symlink(outside.join("d"), &dist).unwrap();
        }
        _ => {}
    }
}

fn config(case: &Value, disabled: &Value) -> String {
    let mut text = String::from(
        "[ici]\nprofile = \"standard\"\n[project]\ntype = \"python\"\nsource_dirs = [\".\"]\n\
         [engines]\n[engines.python_compat]\nenabled = true\n",
    );
    text.push_str(&format!("wheel_globs = {}\n", case["wheel_globs"]));
    if let Some(bound) = case["max_wheels"].as_u64() {
        text.push_str(&format!("max_wheels = {bound}\n"));
    }
    for name in disabled.as_array().unwrap() {
        text.push_str(&format!(
            "[engines.{}]\nenabled = false\n",
            name.as_str().unwrap()
        ));
    }
    text
}

fn replay(name: &str) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["cases"][name];
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let (root, outside, home) = (
        base.join("project"),
        base.join("outside"),
        base.join("home"),
    );
    fs::create_dir(&outside).unwrap();
    fs::create_dir(&home).unwrap();
    layout(&root, &outside, frozen["layout"].as_str().unwrap(), &oracle);
    fs::write(
        root.join("ici.toml"),
        config(frozen, &oracle["disabled_engines"]),
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["python-compat", "--report"])
        .current_dir(&root)
        .env_clear()
        .env("HOME", &home)
        .env("XDG_CONFIG_HOME", &home)
        .env("PATH", "/usr/bin:/bin")
        .output()
        .unwrap();
    let text = fs::read_to_string(root.join("python_compat_report.json"))
        .unwrap_or_else(|_| panic!("{name}: {}", String::from_utf8_lossy(&output.stderr)));
    let report: Value =
        serde_json::from_str(&text.replace(&root.display().to_string(), "<ROOT>")).unwrap();
    assert_eq!(
        output.status.code().map(i64::from),
        frozen["exit_code"].as_i64(),
        "{name}: {}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert_eq!(report["status"], frozen["status"], "{name}");
    assert_eq!(report["summary"], frozen["summary"], "{name}");
    let host = |targets: &Value| -> Vec<Value> {
        targets
            .as_array()
            .unwrap()
            .iter()
            .map(|target| {
                let mut kept = json!({});
                for key in ["file_path", "target_name", "status", "message"] {
                    kept[key] = target.get(key).cloned().unwrap_or(Value::Null);
                }
                // The project interpreter frozen ran is the capture host's.
                if kept["target_name"] == "PythonRuntime:Verified" {
                    kept["message"] = json!("<interpreter> passed version and compileall checks");
                }
                kept
            })
            .collect()
    };
    assert_eq!(host(&report["targets"]), host(&frozen["targets"]), "{name}");
    let rules: Vec<&Value> = report["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| &finding["rule_id"])
        .collect();
    let frozen_rules: Vec<&Value> = frozen["finding_rules"].as_array().unwrap().iter().collect();
    assert_eq!(rules, frozen_rules, "{name}");
}

#[test]
fn configured_wheels_are_inspected_and_non_wheel_matches_skipped_like_frozen() {
    replay("regular");
    replay("non-wheel-skipped");
}

#[test]
fn a_regular_wheel_beside_the_bound_is_refused_with_the_frozen_count_message() {
    replay("count-bound");
}

#[test]
fn symlinked_dangling_escaping_and_directory_candidates_stop_the_analysis_like_frozen() {
    for name in [
        "symlink-outside",
        "symlink-inside",
        "dangling",
        "directory",
        "dist-symlink-outside",
    ] {
        replay(name);
    }
}
