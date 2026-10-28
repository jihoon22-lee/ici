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
