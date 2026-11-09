//! Legacy C++ config keys replayed against tests/rust-parity/cpp/config-keys-oracle.json
//! (capture_config_keys.py): every frozen run of a (reference, variant) configuration pair over
//! parity-fixtures/cpp/legacy-scope-fixture is run natively and compared engine by engine, and
//! each case's effect (the variant changed the engine view or not) must be frozen's.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

#[path = "support/host_tools.rs"]
mod host_tools;

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "end_line",
    "file_path",
];
const FINDING_FIELDS: [&str; 9] = [
    "rule_id",
    "message",
    "severity",
    "confidence",
    "category",
    "tool_name",
    "tool_rule_id",
    "primary_location",
    "fingerprint",
];

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

fn pick(value: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .map(|key| ((*key).to_owned(), value[*key].clone()))
            .collect(),
    )
}

/// The capture's `engine_view`.
fn engine_view(engine: &Value) -> Value {
    let list = |name: &str, keys: &[&str]| -> Value {
        engine[name]
            .as_array()
            .map(|items| items.iter().map(|item| pick(item, keys)).collect())
            .unwrap_or_else(|| json!([]))
    };
    json!({
        "engine_name": engine["engine_name"],
        "status": engine["status"],
        "required": engine["required"],
        "evidence": engine["evidence"],
        "summary": engine["summary"],
        "score": engine["score"],
        "max_score": engine["max_score"],
        "targets": list("targets", &FIELDS),
        "findings": list("findings", &FINDING_FIELDS),
    })
}

fn execute(oracle: &Value, id: &Value, environment: &BTreeMap<String, String>) -> Value {
    let (command, files, config) = (
        id[0].as_str().unwrap(),
        id[1].as_str(),
        id[2].as_str().unwrap(),
    );
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp/legacy-scope-fixture"),
        &root,
    );
    if let Some(files) = files {
        for (path, text) in oracle["files"][files].as_object().unwrap() {
            fs::write(root.join(path), text.as_str().unwrap()).unwrap();
        }
    }
    fs::write(
        root.join("ici.toml"),
        format!("{}{config}", oracle["base"].as_str().unwrap()),
    )
    .unwrap();
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let result = commands::dispatch(&invocation, &root, environment).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(root.join(format!("{}_report.json", command.replace('-', "_")))).unwrap(),
    )
    .unwrap();
    if command == "verify" {
        json!({
            "exit_code": result.exit_code,
            "suite_status": report["suite_status"],
            "engines": report["results"].as_array().unwrap().iter().map(engine_view).collect::<Vec<_>>(),
            "incomplete": report["native_run"]["execution"]["required_complete"] == false,
        })
    } else {
        json!({
            "exit_code": result.exit_code,
            "engines": [engine_view(&report)],
            "incomplete": report["native_run"]["execution"]["required_complete"] == false,
        })
    }
}

/// Recorded validation difference shared with the verify matrix: the no-test verdict is
/// MEASURED (high confidence) with no fabricated coverage estimate, TEM summary or score.
/// `host` (the CI twin, any host g++): the text GCC itself writes — a lint target or g++ lint
/// finding's diagnostic message and lines, and the fingerprint and location derived from them —
/// is masked as incidental (another GCC release words or places a diagnostic differently); the
/// verdicts, counts, rule ids, severities and every non-compiler row stay compared.
fn comparable(mut run: Value, frozen: bool, host: bool) -> Value {
    for engine in run["engines"].as_array_mut().unwrap() {
        if host && engine["engine_name"] == "lint" {
            for target in engine["targets"].as_array_mut().unwrap() {
                let message = target["message"].as_str().unwrap_or("");
                if ["warning:", "error:", "note:"]
                    .iter()
                    .any(|prefix| message.starts_with(prefix))
                {
                    for key in ["message", "start_line", "end_line"] {
                        target[key] = json!("<gcc>");
                    }
                }
            }
            for finding in engine["findings"].as_array_mut().unwrap() {
                if finding["tool_name"] == "g++" {
                    for key in ["message", "fingerprint", "primary_location"] {
                        finding[key] = json!("<gcc>");
                    }
                }
            }
        }
        // The legacy message keeps 200 characters of stderr holding the project's absolute path,
        // so its cut depends on where each side ran; `legacy_build_failure_ladder.rs` compares
        // the text whole at a fixed path length.
        if engine["engine_name"] == "build"
            && matches!(engine["status"].as_str(), Some("FAIL" | "WARN"))
        {
            assert_eq!(engine["targets"][0]["target_name"], "CppBinary");
            engine["targets"][0]["message"] = Value::Null;
            for finding in engine["findings"].as_array_mut().unwrap() {
                for key in ["message", "fingerprint"] {
                    finding[key] = Value::Null;
                }
            }
        }
        if engine["engine_name"] == "test" && engine["status"] == "FAIL" {
            for key in ["summary", "score", "max_score"] {
                engine[key] = Value::Null;
            }
            if frozen {
                engine["evidence"] = json!("MEASURED");
                for finding in engine["findings"].as_array_mut().unwrap() {
                    finding["confidence"] = json!("high");
                }
            }
        }
    }
    // Accepted policy (verify matrix): a run whose required evidence stays incomplete keeps an
    // ERROR suite (single command: ERROR engine) and exits 3 where frozen exited 1.
    let status = if run["suite_status"].is_null() {
        run["engines"][0]["status"].clone()
    } else {
        run["suite_status"].clone()
    };
    if !frozen && run["incomplete"] == true && status == "ERROR" && run["exit_code"] == 3 {
        run["exit_code"] = json!(1);
    }
    if let Some(object) = run.as_object_mut() {
        object.remove("incomplete");
    }
    run
}

fn diff(frozen: &Value, native: &Value, path: &str, out: &mut Vec<String>) {
    match (frozen, native) {
        (Value::Object(left), Value::Object(right)) => {
            for key in left
                .keys()
                .chain(right.keys().filter(|key| !left.contains_key(*key)))
            {
                diff(
                    left.get(key).unwrap_or(&Value::Null),
                    right.get(key).unwrap_or(&Value::Null),
                    &format!("{path}/{key}"),
                    out,
                );
            }
        }
        (Value::Array(left), Value::Array(right)) if left.len() == right.len() => {
            for (index, (left, right)) in left.iter().zip(right).enumerate() {
                diff(left, right, &format!("{path}[{index}]"), out);
            }
        }
        _ if frozen != native => out.push(format!("{path}: frozen {frozen} native {native}")),
        _ => {}
    }
}

/// Positive: each key the frozen C++ engines consume (dup thresholds/window/mode, cognitive
/// mode/required/cpp_boundaries=required, complexity cpp_boundaries=required, dead
/// cpp_linker/mode/required, every engine `enabled`, build enabled/mode/required) changes the
/// native engine view exactly as it changes frozen's. Negative: keys with no C++ effect
/// (Python-only modes, ruff_required, test.python, python_semantic, cpp_boundaries=off without
/// clang) leave both views unchanged. Failure: cpp_boundaries/cpp_linker/cpp_unused `required`
/// without their exact input are the frozen ERROR verdicts and exit 1. ICI_TEST_CXX must be the
/// oracle g++, and its directory, /usr/bin and /bin must lack clang-tidy, clazy-standalone and
/// clang++ (the capture ran without them).
#[test]
#[ignore = "explicit actual g++ legacy C++ config-key runs (ICI_TEST_CXX)"]
fn legacy_cpp_config_keys_change_native_engines_like_frozen() {
    let oracle = oracle();
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler");
    assert_eq!(
        host_tools::version(Path::new(&compiler)).as_deref(),
        oracle["gxx"].as_str(),
        "the config-key oracle was captured with another g++"
    );
    let directory = Path::new(&compiler).parent().unwrap().display().to_string();
    for tool in ["clang-tidy", "clazy-standalone", "clang++"] {
        for path in [directory.as_str(), "/usr/bin", "/bin"] {
            assert!(!Path::new(path).join(tool).exists(), "{tool} in {path}");
        }
    }
    let environment = BTreeMap::from([("PATH".to_string(), format!("{directory}:/usr/bin:/bin"))]);
    replay(&oracle, &environment, false);
}

/// The CI twin of the replay above on the host's own toolchain (the GitHub runner): PATH is a
/// mirror of `/usr/bin` and `/bin` without clang*/clazy* (the capture host's tool situation;
/// `support/host_tools.rs`) and g++ is the host's `/usr/bin/g++` of any release, so only the
/// GCC-written diagnostic text is masked (`comparable`). Every run, verdict, exit code and
/// case effect is still compared.
#[test]
fn legacy_cpp_config_keys_change_native_engines_like_frozen_with_the_host_gxx() {
    let oracle = oracle();
    let host = host_tools::mirror(&host_tools::NO_LLVM);
    let environment = BTreeMap::from([("PATH".to_string(), host.path.clone())]);
    replay(&oracle, &environment, true);
}

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/config-keys-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

fn replay(oracle: &Value, environment: &BTreeMap<String, String>, host: bool) {
    let mut native = BTreeMap::new();
    let mut mismatches = vec![];
    for run in oracle["runs"].as_array().unwrap() {
        let id = &run["id"];
        let actual = comparable(execute(oracle, id, environment), false, host);
        let frozen = comparable(run["frozen"].clone(), true, host);
        let mut lines = vec![];
        diff(&frozen, &actual, "", &mut lines);
        if !lines.is_empty() {
            mismatches.push(format!("== {id}\n{}", lines.join("\n")));
        }
        native.insert(id.to_string(), actual);
    }
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 19);
    for (name, case) in cases {
        let id = |side: &str| json!([case["command"], case["files"], case[side]]).to_string();
        let effect = native[&id("reference")] != native[&id("variant")];
        assert_eq!(
            case["effect"], case["expect_effect"],
            "{name}: frozen effect"
        );
        if Value::Bool(effect) != case["effect"] {
            mismatches.push(format!("{name}: native effect {effect} ({})", case["rows"]));
        }
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}
