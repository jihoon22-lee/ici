//! End-to-end preparation through the common run lifecycle, with explicit host tools.
use ici_cli::{execute, planning, registry};
use ici_domain::TaskState;
use ici_execution::Cancellation;
use std::{collections::BTreeMap, fs};

#[test]
#[ignore = "explicit CMake/GCC preparation and release publication acceptance"]
fn actual_build_prepares_context_then_publishes_measured_binaries_without_source_drift() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(root.path().join("CMakeLists.txt"), "cmake_minimum_required(VERSION 3.16)\nproject(native_fixture LANGUAGES CXX)\nadd_executable(suite main.cpp)\n").unwrap();
    fs::write(
        root.path().join("ici.toml"),
        format!(
            "schema_version=2\nname='fixture'\nversion='v1.2.3'\n[tools.cmake]\npath={}\n",
            serde_json::to_string(&std::env::var("ICI_TEST_CMAKE").expect("explicit CMake"))
                .unwrap()
        ),
    )
    .unwrap();
    let original = fs::read(root.path().join("main.cpp")).unwrap();
    let environment = BTreeMap::from([("PATH".into(), std::env::var("PATH").unwrap())]);
    let invocation = registry::parse(["ici", "build"]).unwrap();
    let preview = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert!(
        !preview.context_preparations.is_empty(),
        "{}",
        preview.text()
    );
    assert!(!root.path().join(".ici").exists());
    assert!(!root.path().join("v1.2.3").exists());
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    let output = &run.outputs["project.cpp.artifact"];
    assert_eq!(
        output.observation.state,
        TaskState::Succeeded,
        "{output:?}\n{:?}",
        run.result
    );
    assert!(
        root.path().join("v1.2.3/x86_64/bin/suite").is_file(),
        "{output:?}"
    );
    assert_eq!(fs::read(root.path().join("main.cpp")).unwrap(), original);
    assert!(
        run.result
            .checks
            .as_ref()
            .unwrap()
            .iter()
            .find(|check| check.check_id == "cpp.artifact")
            .unwrap()
            .evidence_complete,
        "{:?}",
        run.result
    );
    assert!(
        !run.result
            .identity
            .source
            .files
            .iter()
            .any(|path| path.ends_with("/suite"))
    );
}
