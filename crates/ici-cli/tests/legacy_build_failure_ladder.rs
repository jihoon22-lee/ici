//! Frozen BuildEngine `_compile_cpp` for a project with no build descriptor: each way a generic
//! `g++` build can fail is a named outcome at the binary it would have written, and `ici build`
//! ends 1 for all of them (`capture_build_failure_ladder.py`, the g++ wrapper in each case stands
//! for the failure). `ici verify` with the build engine must locate the same targets.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path};

const PRIVATE: [&str; 4] = [".ici", ".ici-native", "__pycache__", ".git"];
const TARGET_FIELDS: [&str; 5] = [
    "file_path",
    "start_line",
    "target_name",
    "status",
    "message",
];
const EVIDENCE_FIELDS: [&str; 5] = ["name", "returncode", "timed_out", "truncated", "error"];
const FINDING_FIELDS: [&str; 6] = [
    "rule_id",
    "message",
    "severity",
    "category",
    "tool_name",
    "primary_location",
];

fn listing(base: &Path, directory: &Path, names: &mut Vec<String>) {
    let mut entries: Vec<_> = fs::read_dir(directory)
        .unwrap()
        .map(|entry| entry.unwrap())
        .collect();
    entries.sort_by_key(fs::DirEntry::file_name);
    for entry in entries {
        let name = entry.file_name().to_string_lossy().into_owned();
        let relative = entry
            .path()
            .strip_prefix(base)
            .unwrap()
            .display()
            .to_string();
        if entry.path().is_dir() {
            if !PRIVATE.contains(&name.as_str()) {
                names.push(format!("{relative}/"));
                listing(base, &entry.path(), names);
            }
        } else {
            names.push(relative);
        }
    }
}

struct Project {
    _work: tempfile::TempDir,
    base: std::path::PathBuf,
    tools: std::path::PathBuf,
    environment: BTreeMap<String, String>,
}

/// The console fold and the 200-character cut depend on the project path: the same length as the
/// capture's, under the same directory.
fn work(oracle: &Value) -> tempfile::TempDir {
    let length = oracle["root_length"].as_u64().unwrap() as usize;
    tempfile::Builder::new()
        .prefix(&"x".repeat(length - "/tmp".len() - 1 - 8))
        .rand_bytes(8)
        .tempdir_in("/tmp")
        .unwrap()
}

fn project(oracle: &Value, case: &Value, config: &str) -> Project {
    let work = work(oracle);
    let root = work.path().canonicalize().unwrap();
    let (base, tools) = (root.join("project"), root.join("bin"));
    fs::create_dir_all(base.join("src")).unwrap();
    fs::create_dir(base.join(".git")).unwrap();
    fs::create_dir(&tools).unwrap();
    fs::write(base.join("ici.toml"), config).unwrap();
    match case.get("sources").and_then(Value::as_object) {
        Some(sources) => {
            for (path, text) in sources {
                fs::write(base.join(path), text.as_str().unwrap()).unwrap();
            }
        }
        None => fs::write(
            base.join("src/main.cpp"),
            oracle["source"].as_str().unwrap(),
        )
        .unwrap(),
    }
    let script = match (case.get("body").and_then(Value::as_str), case.get("raw")) {
        (Some(body), _) => Some(format!("#!/bin/bash\n{body}\n")),
        (None, Some(raw)) => Some(raw.as_str().unwrap().to_owned()),
        (None, None) => None,
    };
    let path = match script {
        Some(script) => {
            fs::write(tools.join("g++"), script).unwrap();
            fs::set_permissions(tools.join("g++"), fs::Permissions::from_mode(0o755)).unwrap();
            format!("{}:/usr/bin:/bin", tools.display())
        }
        None => tools.display().to_string(),
    };
    if let Some(setup) = case.get("setup").and_then(Value::as_str) {
        let output = base.join("v1.0.0/x86_64/bin/project");
        fs::create_dir_all(output.parent().unwrap()).unwrap();
        if setup == "symlink" {
            std::os::unix::fs::symlink("elsewhere", &output).unwrap();
        } else {
            fs::create_dir(&output).unwrap();
        }
    }
    let environment = BTreeMap::from([
        ("HOME".to_owned(), root.display().to_string()),
        ("XDG_CONFIG_HOME".to_owned(), root.display().to_string()),
        ("PATH".to_owned(), path),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            root.display().to_string(),
        ),
    ]);
    Project {
        _work: work,
        base,
        tools,
        environment,
    }
}

fn pick(value: &Value, fields: &[&str]) -> Value {
    Value::Object(
        fields
            .iter()
            .map(|key| ((*key).to_owned(), value[*key].clone()))
            .collect(),
    )
}

fn mode(case: &Value) -> String {
    case.get("mode")
        .and_then(Value::as_str)
        .map_or(String::new(), |mode| format!("mode = \"{mode}\"\n"))
}

/// Only complete tokens of frozen's linker temporary object (`/tmp/cc` + six characters + `.o`,
/// whole between non-path characters) become the placeholder native displays for its own objects
/// (decisions.md "직접 g++ 링크 실패 문구의 object 경로"). Anything else stays and is compared.
fn normalize_object(text: &str) -> String {
    let path_char = |c: char| c.is_alphanumeric() || "._-/+~".contains(c);
    let mut output = String::new();
    let mut kept = 0;
    for (index, _) in text.match_indices("/tmp/cc") {
        let name = index + "/tmp/cc".len();
        let token = text[name..]
            .char_indices()
            .nth(6)
            .map(|(offset, _)| name + offset)
            .filter(|end| {
                text[name..*end].chars().all(|c| c.is_ascii_alphanumeric())
                    && text[*end..].starts_with(".o")
                    && !text[*end + 2..].chars().next().is_some_and(path_char)
                    && !text[..index].chars().next_back().is_some_and(path_char)
            });
        if let Some(end) = token {
            output.push_str(&text[kept..index]);
            output.push_str("<object>");
            kept = end + 2;
        }
    }
    output.push_str(&text[kept..]);
    output
}

fn built(oracle: &Value, case: &Value) -> Value {
    let table = if mode(case).is_empty() {
        String::new()
    } else {
        format!("[engines.build]\n{}", mode(case))
    };
    let config = format!("{}{table}", oracle["config"].as_str().unwrap());
    let project = project(oracle, case, &config);
    let invocation = registry::parse(["ici", "build"]).unwrap();
    let output = commands::dispatch(&invocation, &project.base, &project.environment).unwrap();
    let mut files = vec![];
    listing(&project.base, &project.base, &mut files);
    files.sort();
    // Frozen folds long lines anywhere; the capture flattened the cases that print paths.
    let stdout = if case.get("setup").is_some() || case.get("raw").is_some() {
        output
            .stdout
            .replace('\n', "")
            .replace(&project.base.display().to_string(), "<root>")
            .replace(&project.tools.display().to_string(), "<bin>")
    } else {
        output.stdout
    };
    json!({"exit_code": output.exit_code, "stdout": stdout, "files": files})
}

fn verified(oracle: &Value, case: &Value) -> Value {
    let config = format!(
        "{}{}{}",
        oracle["config"].as_str().unwrap(),
        oracle["enable_build"].as_str().unwrap(),
        mode(case),
    );
    let project = project(oracle, case, &config);
    let invocation = registry::parse([
        "ici",
        "verify",
        "--profile",
        "deep",
        "--no-cache",
        "--report",
    ])
    .unwrap();
    commands::dispatch(&invocation, &project.base, &project.environment).unwrap();
    let text = fs::read_to_string(project.base.join("verify_report.json"))
        .unwrap()
        .replace(&project.tools.display().to_string(), "<bin>")
        .replace(&project.base.display().to_string(), "<root>");
    let report: Value = serde_json::from_str(&text).unwrap();
    let engine = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "build")
        .unwrap();
    let list = |key: &str, fields: &[&str]| -> Vec<Value> {
        engine[key]
            .as_array()
            .map(|items| items.iter().map(|item| pick(item, fields)).collect())
            .unwrap_or_default()
    };
    json!({
        "status": engine["status"],
        "summary": engine["summary"],
        "evidence": engine["evidence"],
        "targets": list("targets", &TARGET_FIELDS),
        "tool_evidence": list("tool_evidence", &EVIDENCE_FIELDS),
        "findings": list("findings", &FINDING_FIELDS),
    })
}

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/build-failure-ladder-oracle.json"
    ))
    .unwrap()
}

fn compare(oracle: &Value, group: &str, normalize: bool) -> Vec<String> {
    let mut differences = vec![];
    for (name, case) in oracle[group].as_object().unwrap() {
        let actual = built(oracle, case);
        if actual != case["frozen"]["build"] {
            differences.push(format!(
                "{name} build:\n native {actual}\n frozen {}",
                case["frozen"]["build"]
            ));
        }
        let actual = verified(oracle, case);
        let mut frozen = case["frozen"]["verify"].clone();
        if normalize {
            for (key, field) in [
                ("targets", "message"),
                ("tool_evidence", "error"),
                ("findings", "message"),
            ] {
                if let Some(text) = frozen[key][0][field].as_str().map(normalize_object) {
                    frozen[key][0][field] = json!(text);
                }
            }
        }
        if actual != frozen {
            differences.push(format!(
                "{name} verify:\n native {actual}\n frozen {frozen}"
            ));
        }
    }
    differences
}

#[test]
fn every_generic_gxx_build_failure_equals_frozen() {
    let oracle = oracle();
    let differences = [
        compare(&oracle, "cases", false),
        compare(&oracle, "edge_cases", false),
    ]
    .concat();
    assert!(differences.is_empty(), "{}", differences.join("\n"));
}

/// Decisions.md "직접 g++ 비-ELF 산출물": frozen published these as a built binary. The legacy
/// direct release contract is an ELF executable, so each is its own refusal, located where the
/// binary would be, and nothing is published.
#[test]
fn a_regular_output_that_is_no_elf_executable_is_refused_and_not_published() {
    let oracle = oracle();
    let details = [
        (
            "non-elf-output",
            "is not a supported ELF executable: the file has no ELF header",
        ),
        (
            "corrupt-elf-output",
            "could not be verified as an ELF executable: ",
        ),
        (
            "shared-object-output",
            "is a shared-library, not an executable",
        ),
    ];
    for (name, detail) in details {
        let case = &oracle["corrected_cases"][name];
        assert_eq!(case["frozen"]["build"]["exit_code"], 0, "{name}");
        assert_eq!(case["frozen"]["verify"]["status"], "PASS", "{name}");

        let built_case = built(&oracle, case);
        assert_eq!(built_case["exit_code"], 1, "{name}: {built_case}");
        let stdout = built_case["stdout"].as_str().unwrap().replace('\n', "");
        assert!(stdout.contains(detail), "{name}: {stdout}");
        assert_eq!(
            built_case["files"],
            json!([
                "ici.toml",
                "src/",
                "src/main.cpp",
                "v1.0.0/",
                "v1.0.0/x86_64/",
                "v1.0.0/x86_64/bin/",
                "v1.0.0/x86_64/lib/"
            ]),
            "{name}"
        );

        let verify = verified(&oracle, case);
        assert_eq!(verify["status"], "ERROR", "{name}");
        assert_eq!(verify["evidence"], "NOT_RUN", "{name}");
        let target = &verify["targets"][0];
        assert_eq!(target["file_path"], "v1.0.0/x86_64/bin/project", "{name}");
        assert_eq!(target["target_name"], "BuildError", "{name}");
        let message = target["message"].as_str().unwrap();
        assert!(
            message.starts_with("g++ reported success but .ici/rust-v1/build/"),
            "{name}"
        );
        assert!(message.contains(detail), "{name}: {message}");
        assert_eq!(verify["tool_evidence"][0]["returncode"], 0, "{name}");
        assert_eq!(verify["summary"], target["message"], "{name}");

        // The native result: no success, the run keeps the refusal, and nothing was recorded.
        let project = project(&oracle, case, oracle["config"].as_str().unwrap());
        let invocation = registry::parse(["ici", "build"]).unwrap();
        commands::dispatch(&invocation, &project.base, &project.environment).unwrap();
        let result: Value = serde_json::from_slice(
            &fs::read(project.base.join(".ici/rust-v1/result.json")).unwrap(),
        )
        .unwrap();
        assert_ne!(result["gate"]["selected"], "PASS", "{name}");
        assert!(
            !project.base.join("v1.0.0/x86_64/bin/project").exists(),
            "{name}: published"
        );
        let mut files = vec![];
        listing(
            &project.base.join(".ici/rust-v1"),
            &project.base.join(".ici/rust-v1"),
            &mut files,
        );
        assert!(
            !files.iter().any(|file| file.contains("manifest")),
            "{name}: {files:?}"
        );
        eprintln!(
            "{name}: gate {} exit {}",
            result["gate"]["selected"], built_case["exit_code"]
        );
    }
}

#[test]
fn only_a_complete_linker_temporary_object_is_normalized() {
    let token = "/tmp/ccAbCdEf.o";
    assert_eq!(
        normalize_object(&format!("ld: {token}: in function")),
        "ld: <object>: in function"
    );
    for text in [
        "/tmp/ccAbCd.o: too short",
        "/tmp/ccAbCdEfG.o: too long",
        "/tmp/ccAbCdEf.obj: other suffix",
        "/tmp/ccAbCdEf: unfinished",
        "/tmp/ccAb/dEf.o: a directory",
        "src/tmp/ccAbCdEf.o: a project path",
        "no such token",
    ] {
        assert_eq!(normalize_object(text), text);
    }
}

#[test]
#[ignore = "explicit actual /usr/bin/g++ gate: every source that ran keeps its diagnostic"]
fn every_failed_source_keeps_its_diagnostic_in_the_native_result() {
    let oracle = oracle();
    let case = &oracle["real_cases"]["real-later-source"];
    let project = project(&oracle, case, oracle["config"].as_str().unwrap());
    let invocation = registry::parse(["ici", "build"]).unwrap();
    commands::dispatch(&invocation, &project.base, &project.environment).unwrap();
    let result: Value =
        serde_json::from_slice(&fs::read(project.base.join(".ici/rust-v1/result.json")).unwrap())
            .unwrap();
    let located: Vec<&str> = result["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|finding| finding["rule_id"] == "compiler.diagnostic")
        .map(|finding| finding["primary_location"]["path"].as_str().unwrap())
        .collect();
    assert_eq!(located, ["src/b.cpp", "src/c.cpp"]);
}

#[test]
#[ignore = "real 300 s compile timeout, run with the frozen capture of the same bound"]
fn a_compile_past_the_timeout_equals_frozen() {
    let oracle = oracle();
    let capture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/build-failure-timeout-oracle.json"
    ))
    .unwrap();
    let case = json!({"body": capture["body"]});
    let (build, verify) = std::thread::scope(|scope| {
        let build = scope.spawn(|| built(&oracle, &case));
        let verify = scope.spawn(|| verified(&oracle, &case));
        (build.join().unwrap(), verify.join().unwrap())
    });
    assert_eq!(build, capture["frozen"]["build"]);
    assert_eq!(verify, capture["frozen"]["verify"]);
}

#[test]
#[ignore = "explicit actual /usr/bin/g++ gate: the compiler's own text is the message"]
fn a_real_gxx_diagnostic_is_the_frozen_message() {
    let oracle = oracle();
    let differences = compare(&oracle, "real_cases", true);
    assert!(differences.is_empty(), "{}", differences.join("\n"));
}
