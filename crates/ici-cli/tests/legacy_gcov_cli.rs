//! Actual GCC/gcov 7 (no JSON format) through the common CLI: the plan shows the probe-gated
//! format condition and the run reads the legacy text report. No tool is installed here.
use ici_cli::{execute, planning, registry};
use ici_domain::TaskState;
use ici_execution::Cancellation;
use std::{collections::BTreeMap, fs};

fn required(name: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| panic!("explicit native tool {name} required"))
}

#[test]
#[ignore = "explicit actual GCC/gcov 7.4.0 text coverage through the common CLI"]
fn old_gcov_plan_names_the_text_condition_and_the_run_reads_text_evidence() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("main.c"),
        "int never(){return 4;}\nint branch(int value){if(value)return 1;return 0;}\nint main(){return branch(1)==1?0:1;}\n",
    )
    .unwrap();
    fs::write(
        root.path().join("CMakeLists.txt"),
        "cmake_minimum_required(VERSION 3.16)\nproject(native LANGUAGES C)\nenable_testing()\nadd_executable(suite main.c)\ntarget_compile_options(suite PRIVATE --coverage -O0)\ntarget_link_options(suite PRIVATE --coverage)\nadd_test(NAME green COMMAND suite)\n",
    )
    .unwrap();
    // Fixture preparation only: the project's own old compiler builds its instrumented suite.
    let compiler = required("ICI_TEST_LEGACY_CC");
    let library = required("ICI_TEST_LEGACY_LIBDIR");
    let cmake = required("ICI_TEST_CMAKE");
    for args in [
        vec![
            "-S".to_owned(),
            ".".into(),
            "-B".into(),
            "build".into(),
            format!("-DCMAKE_C_COMPILER={compiler}"),
        ],
        vec![
            "--build".into(),
            "build".into(),
            "--parallel".into(),
            "2".into(),
        ],
    ] {
        let output = std::process::Command::new(&cmake)
            .args(&args)
            .env("LD_LIBRARY_PATH", &library)
            .current_dir(root.path())
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
    }
    let mut config = String::from("schema_version=2\n");
    for check in ici_domain::builtin_catalog()
        .checks
        .iter()
        .filter(|check| check.language == "cpp")
    {
        let on = matches!(check.id.as_str(), "cpp.test" | "cpp.coverage");
        config.push_str(&format!("[checks.\"{}\"]\nenabled={on}\n", check.id));
    }
    for (name, variable) in [
        ("ctest", "ICI_TEST_CTEST"),
        ("gcov", "ICI_TEST_LEGACY_GCOV"),
    ] {
        config.push_str(&format!(
            "[tools.{name}]\npath={}\n",
            serde_json::to_string(&required(variable)).unwrap()
        ));
    }
    config.push_str("[builds.native]\nsystem='cmake'\ndirectory='build'\nartifacts=['suite']\n[[components]]\nid='project'\nroot='.'\nlanguages=['cpp']\nsources=['main.c']\nbuild='native'\n");
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    let preview = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert!(!root.path().join(".ici").exists());
    let text = preview.text();
    for line in [
        "project.cpp.coverage.report: only when project.cpp.coverage.capability selects gcov JSON format",
        "project.cpp.coverage.text-report: only when project.cpp.coverage.capability selects gcov text format",
    ] {
        assert!(text.contains(line), "{text}");
    }
    let document = preview.document().to_string();
    assert!(document.contains("\"gcov_format\":\"text\""), "{document}");
    assert!(document.contains("\"gcov_format\":\"json\""), "{document}");
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    let output = &run.outputs["project.cpp.coverage"];
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    let extra = output
        .extra
        .as_object()
        .unwrap()
        .values()
        .find(|value| value.get("gcov_capability").is_some())
        .unwrap();
    assert_eq!(extra["gcov_capability"]["version"], "7.4.0");
    assert_eq!(extra["gcov_capability"]["format"], "text");
    assert_eq!(extra["coverage_provenance"]["format"], "gcov-text");
    for (key, value) in [
        ("stmts", 3),
        ("covered", 2),
        ("nb", 2),
        ("cb", 1),
        ("functions", 3),
        ("covered_functions", 2),
    ] {
        assert_eq!(extra["coverage_totals"][key], value, "{key}");
    }
    assert_eq!(
        run.outputs["project.cpp.test"].observation.state,
        TaskState::Succeeded
    );
}
