use ici_cli::{commands, execute, planning, registry};
use ici_domain::{GateVerdict, TaskState};
use ici_execution::Cancellation;
use serde_json::Value;
use std::{collections::BTreeMap, fs};

fn fixture(source: &str) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("src")).unwrap();
    fs::write(root.path().join("src/main.cpp"), source).unwrap();
    fs::write(
        root.path().join("pyproject.toml"),
        "[project]\nname='legacy-direct-proof'\nversion='1.2.3'\n",
    )
    .unwrap();
    fs::write(
        root.path().join("ici.toml"),
        "[project]\ntype='cpp'\nsource_dirs=['src']\n",
    )
    .unwrap();
    root
}
fn environment() -> BTreeMap<String, String> {
    BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())])
}
#[test]
fn descriptorless_legacy_build_compiles_owned_sources_and_publishes_actual_binary() {
    let root = fixture("int main(){return 0;}\n");
    let invocation = registry::parse(["ici", "build"]).unwrap();
    let plan = planning::prepare(&invocation, root.path(), &environment(), false).unwrap();
    assert_eq!(
        plan.context_preparations.len(),
        1,
        "{:?}",
        plan.plan.limitations
    );
    assert!(plan.context_preparations[0].direct.is_some());
    assert!(!root.path().join(".ici").exists());
    let result = execute::execute(
        &invocation,
        root.path(),
        &environment(),
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        result.result.gate.selected,
        GateVerdict::Pass,
        "{:?}; limitations={:?}; required={:?}; checks={:?}; statuses={:?}",
        result.result.gate,
        result.result.limitations,
        result.required_checks,
        result.result.checks,
        result
            .outputs
            .iter()
            .map(|(id, output)| (id, &output.status, &output.observation.state))
            .collect::<Vec<_>>()
    );
    let output = result
        .outputs
        .values()
        .find(|output| output.extra.get("preparation_evidence").is_some())
        .unwrap();
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert_eq!(
        output.extra["preparation_evidence"]["release"]["system"],
        "direct-gcc"
    );
    assert!(root.path().join("src/main.cpp").is_file());
}
#[test]
fn descriptorless_legacy_compile_failure_is_located_and_keeps_source_bytes() {
    let source = "int main(){ missing_symbol; return 0; }\n";
    let root = fixture(source);
    let invocation = registry::parse(["ici", "build"]).unwrap();
    let result = commands::dispatch(&invocation, root.path(), &environment()).unwrap();
    assert_eq!(result.exit_code, 1, "{} {}", result.stdout, result.stderr);
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join(".ici/rust-v1/result.json")).unwrap())
            .unwrap();
    assert!(report["findings"].as_array().unwrap().iter().any(
        |finding| finding["primary_location"]["path"] == "src/main.cpp"
            && finding["rule_id"] == "compiler.diagnostic"
    ));
    assert_eq!(
        fs::read_to_string(root.path().join("src/main.cpp")).unwrap(),
        source
    );
}

#[test]
fn descriptorless_cpp_test_report_counts_each_executable_once_with_shared_coverage() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("src")).unwrap();
    fs::create_dir_all(root.path().join("tests")).unwrap();
    fs::write(
        root.path().join("src/value.cpp"),
        "int value(){return 2;}\n",
    )
    .unwrap();
    fs::write(
        root.path().join("tests/one.cpp"),
        "int value(); int main(){return value()==2 ? 0 : 1;}\n",
    )
    .unwrap();
    fs::write(
        root.path().join("tests/two.cpp"),
        "int value(); int main(){return value()==3 ? 0 : 1;}\n",
    )
    .unwrap();
    fs::write(
        root.path().join("pyproject.toml"),
        "[project]\nname='cpp-test-proof'\nversion='1.2.3'\n",
    )
    .unwrap();
    fs::write(
        root.path().join("ici.toml"),
        "[project]\ntype='cpp'\nsource_dirs=['src']\n",
    )
    .unwrap();
    let invocation = registry::parse(["ici", "test", "--report"]).unwrap();
    let planned = planning::prepare(&invocation, root.path(), &environment(), false).unwrap();
    assert!(
        planned
            .context_preparations
            .iter()
            .any(|plan| plan.direct.is_some() && plan.build.variant == "coverage")
    );
    let outcome = commands::dispatch(&invocation, root.path(), &environment()).unwrap();
    assert_eq!(
        outcome.exit_code, 1,
        "{} {}",
        outcome.stdout, outcome.stderr
    );
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join("test_report.json")).unwrap()).unwrap();
    assert_eq!(report["extra"]["passed_tests"], 1, "{report:#}");
    assert_eq!(report["extra"]["total_tests"], 2);
    assert_eq!(
        report["extra"]["test_execution"]["physical_suites"]
            .as_object()
            .unwrap()
            .len(),
        2
    );
    assert!(
        report["extra"]["coverage_totals"]["stmts"]
            .as_u64()
            .is_some()
    );
}
