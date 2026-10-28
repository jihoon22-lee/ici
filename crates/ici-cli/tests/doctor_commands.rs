//! Actual scope/probe behavior through both public command routes.
use ici_cli::{commands::dispatch, doctor, registry::parse};
use ici_domain::sha256;
use ici_execution::Cancellation;
use serde_json::Value;
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path};

fn script(root: &Path, name: &str, body: &str) {
    let path = root.join(name);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(&path, format!("#!/bin/sh\n{body}\n")).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
}
fn fixture(selected: &[&str], extra: &str) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("app.py"),
        "raise RuntimeError('doctor must never import this source')\n",
    )
    .unwrap();
    let mut config = "schema_version=2\nprofile='deep'\n".to_owned();
    for check in ici_domain::builtin_catalog().checks {
        config.push_str(&format!(
            "[checks.{}]\nenabled={}\nrequired={}\n",
            serde_json::to_string(&check.id).unwrap(),
            selected.contains(&check.id.as_str()),
            selected.contains(&check.id.as_str())
        ));
    }
    config.push_str(extra);
    config.push_str(
        "\n[[components]]\nid='project'\nroot='.'\nlanguages=['python']\nsources=['app.py']\n",
    );
    fs::write(root.path().join("ici.toml"), config).unwrap();
    root
}
fn env() -> BTreeMap<String, String> {
    BTreeMap::from([
        ("PATH".into(), String::new()),
        ("PRIVATE_TOKEN".into(), "must-not-be-inherited".into()),
    ])
}
fn next(root: &Path, environment: &BTreeMap<String, String>) -> Value {
    let output = dispatch(
        &parse(["ici", "next", "doctor", "--json"]).unwrap(),
        root,
        environment,
    )
    .unwrap();
    assert!(output.stderr.is_empty());
    assert_eq!(output.exit_code, 0);
    serde_json::from_str(&output.stdout).unwrap()
}
fn check<'a>(document: &'a Value, id: &str) -> &'a Value {
    document["components"][0]["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["check"] == id)
        .unwrap()
}
#[test]
fn shared_linter_version_is_probed_once_and_no_analysis_or_state_is_written() {
    let root = fixture(
        &["python.line", "python.lint", "python.format"],
        "[tools.ruff]\npath='shared-ruff'\n",
    );
    script(
        root.path(),
        "shared-ruff",
        "if [ \"$#\" != 1 ] || [ \"$1\" != --version ]; then /usr/bin/touch analysis-ran; exit 9; fi\nprintf '%s\\n' \"$1\" >> version-calls\nprintf 'ruff 0.16.4\\n'",
    );
    let config = fs::read(root.path().join("ici.toml")).unwrap();
    let source = fs::read(root.path().join("app.py")).unwrap();
    let document = next(root.path(), &env());
    assert_eq!(document["schema_id"], "ici.next.doctor");
    assert_eq!(document["schema_version"], 1);
    assert_eq!(
        document["capability_inventory"]["counts"]["version_probes"],
        1
    );
    assert_eq!(document["tools"]["ruff"]["version"], "ruff 0.16.4");
    assert_eq!(document["tools"]["ruff"]["complete"], true);
    assert_eq!(check(&document, "python.lint")["status"], "selected");
    assert_eq!(check(&document, "python.format")["status"], "selected");
    assert_eq!(check(&document, "python.line")["tool"], "ici");
    assert_eq!(
        fs::read_to_string(root.path().join("version-calls")).unwrap(),
        "--version\n"
    );
    assert!(!root.path().join("analysis-ran").exists());
    assert!(!root.path().join(".ici").exists());
    assert_eq!(fs::read(root.path().join("ici.toml")).unwrap(), config);
    assert_eq!(fs::read(root.path().join("app.py")).unwrap(), source);
    assert!(
        document["support_matrix"]["entries"]
            .as_array()
            .unwrap()
            .iter()
            .all(|entry| entry["evidence"] == "NOT_RUN" && entry["active_mode"].is_null())
    );
}
#[test]
fn absent_project_python_blocks_its_scope_and_keeps_native_rules_selected() {
    let root = fixture(&["python.line", "python.test"], "");
    let document = next(root.path(), &env());
    assert_eq!(check(&document, "python.line")["status"], "selected");
    let runtime = check(&document, "python.test");
    assert_eq!(runtime["status"], "blocked");
    assert_eq!(runtime["affected_scope"][0], "project.python.test");
    assert!(
        runtime["config_keys"]
            .as_array()
            .unwrap()
            .iter()
            .any(|key| key == "components.project.python.executable")
    );
    assert!(runtime["recheck"].as_str().unwrap().contains("doctor"));
    assert_eq!(document["runtime"]["kind"], "rust-native");
    assert_eq!(document["runtime"]["requires_project_python"], false);
    assert!(document["running_python"].is_null());
    assert!(!root.path().join(".ici").exists());
}
#[test]
fn disabled_tool_checks_do_not_probe_or_become_required() {
    let root = fixture(&["python.line"], "[tools.ruff]\npath='unused-ruff'\n");
    script(
        root.path(),
        "unused-ruff",
        "/usr/bin/touch forbidden-probe\nexit 0",
    );
    let document = next(root.path(), &env());
    assert_eq!(check(&document, "python.lint")["status"], "omitted");
    assert_eq!(check(&document, "python.format")["status"], "omitted");
    assert_eq!(
        document["capability_inventory"]["counts"]["version_probes"],
        0
    );
    assert!(document["tools"].as_object().unwrap().is_empty());
    assert!(
        document["effective_required_tools"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert!(!root.path().join("forbidden-probe").exists());
}
#[test]
fn declared_required_tools_preserve_failure_timeout_and_redacted_diagnostics() {
    let root = fixture(
        &["python.line"],
        "[doctor]\nrequired_tools=['sentinel','slow','missing']\n[tools.sentinel]\npath='failure-tool'\n[tools.slow]\npath='slow-tool'\n[tools.missing]\npath='absent-tool'\n",
    );
    script(
        root.path(),
        "failure-tool",
        "printf 'password=private-value\\n' >&2\nexit 7",
    );
    script(root.path(), "slow-tool", "/bin/sleep 5\nprintf 'slow 1\\n'");
    let document = next(root.path(), &env());
    assert_eq!(document["capability_inventory"]["status"], "WARN");
    assert_eq!(document["tools"]["sentinel"]["available"], true);
    assert_eq!(document["tools"]["sentinel"]["complete"], false);
    assert_eq!(document["tools"]["sentinel"]["probe"]["exit_code"], 7);
    assert_eq!(document["tools"]["slow"]["probe"]["timed_out"], true);
    assert!(
        document["tools"]["slow"]["probe"]["cleanup"]["survivors"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert_eq!(document["tools"]["missing"]["available"], false);
    assert!(
        document["tools"]["missing"]["required_by"]
            .as_array()
            .unwrap()
            .iter()
            .any(|owner| owner == "doctor.config")
    );
    assert!(!document.to_string().contains("private-value"));
    assert!(document.to_string().contains("REDACTED"));
    assert!(!root.path().join(".ici").exists());
}
#[test]
fn legacy_json_and_brief_describe_native_runtime_and_do_not_inherit_private_environment() {
    let root = fixture(
        &["python.line"],
        "[doctor]\nrequired_tools=['sentinel']\n[tools.sentinel]\npath='sentinel-tool'\n",
    );
    script(
        root.path(),
        "sentinel-tool",
        "printf 'sentinel 1 token=printed-secret private=%s\\n' \"$PRIVATE_TOKEN\"",
    );
    let output = dispatch(
        &parse(["ici", "doctor", "--json"]).unwrap(),
        root.path(),
        &env(),
    )
    .unwrap();
    assert!(!output.stdout.contains("printed-secret"));
    assert!(!output.stdout.contains("must-not-be-inherited"));
    let document: Value = serde_json::from_str(&output.stdout).unwrap();
    for key in [
        "system",
        "runtime",
        "running_python",
        "python_candidates",
        "capability_inventory",
        "tools",
        "required_tools",
        "effective_required_tools",
        "support_matrix",
        "analysis_profile",
        "paths",
    ] {
        assert!(document.get(key).is_some(), "{key}");
    }
    assert!(document["paths"].get("nas_shared").is_none());
    let brief = dispatch(
        &parse(["ici", "doctor", "--brief"]).unwrap(),
        root.path(),
        &env(),
    )
    .unwrap();
    assert!(brief.stdout.contains("brief"));
    assert!(brief.stdout.contains("Rust native"));
    assert!(brief.stdout.contains("caps"));
    assert!(!brief.stdout.contains("Engine Capability Matrix"));
    assert!(!brief.stdout.contains("printed-secret"));
}
#[test]
fn native_tool_pack_is_verified_and_explicit_corruption_is_reported() {
    let root = fixture(&["python.lint"], "");
    let pack = root.path().join("pack");
    script(&pack, "bin/ruff", "printf 'ruff fixture-version\\n'");
    let digest = sha256(fs::read(pack.join("bin/ruff")).unwrap());
    fs::write(pack.join("manifest.json"),serde_json::json!({"schema_id":"ici.native-tools","schema_version":1,"tools":{"ruff":{"path":"bin/ruff","sha256":digest,"version":"fixture-version","license":"MIT","target":"x86_64-unknown-linux-musl"}}}).to_string()).unwrap();
    let mut environment = env();
    environment.insert(
        "ICI_NATIVE_TOOL_PACK".into(),
        pack.to_string_lossy().into_owned(),
    );
    let document = next(root.path(), &environment);
    assert_eq!(document["tools"]["ruff"]["origin"], "pack");
    assert_eq!(check(&document, "python.lint")["status"], "selected");
    script(&pack, "bin/ruff", "printf 'changed binary\\n'");
    let document = next(root.path(), &environment);
    assert_eq!(check(&document, "python.lint")["status"], "blocked");
    assert!(document.to_string().contains("checksum"));
}
#[test]
fn empty_workspace_and_cancelled_version_probes_remain_inspectable_without_writes() {
    let empty = tempfile::tempdir().unwrap();
    let document = next(empty.path(), &env());
    assert!(
        document["scope_error"]
            .as_str()
            .unwrap()
            .contains("no supported source")
    );
    assert_eq!(document["runtime"]["kind"], "rust-native");
    assert!(!empty.path().join(".ici").exists());
    let root = fixture(
        &["python.line"],
        "[doctor]\nrequired_tools=['sentinel']\n[tools.sentinel]\npath='sentinel-tool'\n",
    );
    script(
        root.path(),
        "sentinel-tool",
        "/usr/bin/touch cancelled-tool-ran\nprintf 'sentinel 1\\n'",
    );
    let cancellation = Cancellation::default();
    cancellation.cancel();
    let document = doctor::collect(
        &parse(["ici", "next", "doctor", "--json"]).unwrap(),
        root.path(),
        &env(),
        &cancellation,
    )
    .unwrap();
    assert_eq!(document["cancelled"], true);
    assert_eq!(document["tools"]["sentinel"]["complete"], false);
    assert!(!root.path().join("cancelled-tool-ran").exists());
    assert!(!root.path().join(".ici").exists());
}
#[test]
fn invalid_scope_is_an_actionable_error_and_does_not_run_probes() {
    let root = fixture(&["python.line"], "");
    let result = dispatch(
        &parse(["ici", "next", "doctor", "--component", "unknown", "--json"]).unwrap(),
        root.path(),
        &env(),
    );
    assert!(result.unwrap_err().to_string().contains("no component"));
    assert!(!root.path().join(".ici").exists());
}

#[test]
fn runtime_matrix_versions_do_not_compile_or_import_project_code_and_optional_absence_is_local() {
    let root = fixture(
        &["python.line", "python.compat-runtime"],
        "[tools.runtime]\npath='project-python'\n[engines.python_compat]\ninterpreters=['runtime','missing-optional']\nrequired_interpreters=['runtime']\nimports=['app']\n",
    );
    script(
        root.path(),
        "project-python",
        "if [ \"$#\" != 1 ] || [ \"$1\" != --version ]; then /usr/bin/touch project-runtime-work-ran; exit 9; fi\nprintf 'Python 3.10.21\\n'",
    );
    let document = next(root.path(), &env());
    assert_eq!(
        check(&document, "python.compat-runtime")["status"],
        "selected"
    );
    assert_eq!(document["tools"]["python:runtime"]["complete"], true);
    assert_eq!(
        document["tools"]["python:missing-optional"]["available"],
        false
    );
    assert_eq!(
        document["tools"]["python:missing-optional"]["required"],
        false
    );
    assert!(
        document["capability_inventory"]["missing_required"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        document["capability_inventory"]["counts"]["version_probes"],
        1
    );
    assert!(!root.path().join("project-runtime-work-ran").exists());
    assert!(!root.path().join(".ici").exists());
}
#[test]
fn recorded_cpp_context_is_reported_without_replay_configure_build_or_tests() {
    let root = fixture(&["cpp.line", "cpp.compile", "cpp.diagnostics"], "");
    let config_path = root.path().join("ici.toml");
    let config = fs::read_to_string(&config_path)
        .unwrap()
        .replace("languages=['python']", "languages=['cpp']")
        .replace("sources=['app.py']", "sources=['main.cpp']");
    fs::write(config_path, config).unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(
        root.path().join("CMakeLists.txt"),
        "message(FATAL_ERROR \"doctor must not configure this project\")\n",
    )
    .unwrap();
    script(
        root.path(),
        "compiler-wrapper",
        "if [ \"$#\" != 1 ] || [ \"$1\" != --version ]; then /usr/bin/touch compiler-replay-ran; exit 9; fi\nprintf 'g++ wrapper 15.2.0\\n'",
    );
    let driver = root.path().join("compiler-wrapper");
    fs::write(root.path().join("compile_commands.json"),serde_json::json!([{"directory":root.path(),"file":root.path().join("main.cpp"),"arguments":[driver,"-std=c++17","-c",root.path().join("main.cpp"),"-o","main.o"]}]).to_string()).unwrap();
    let document = next(root.path(), &env());
    let inputs = &document["components"][0]["compile_inputs"];
    assert_eq!(inputs["database"], "compile_commands.json");
    assert_eq!(inputs["expected"], 1);
    assert_eq!(inputs["covered"].as_array().unwrap().len(), 1);
    assert!(inputs["missing"].as_array().unwrap().is_empty());
    assert_eq!(
        document["capability_inventory"]["counts"]["version_probes"],
        1
    );
    assert!(!root.path().join("compiler-replay-ran").exists());
    assert!(!root.path().join("main.o").exists());
    assert!(!root.path().join(".ici").exists());
}
#[test]
fn excessive_version_output_is_incomplete_instead_of_ready() {
    let root = fixture(
        &["python.line"],
        "[doctor]\nrequired_tools=['verbose']\n[tools.verbose]\npath='verbose-tool'\n",
    );
    script(
        root.path(),
        "verbose-tool",
        "/usr/bin/head -c 20000 /dev/zero | /usr/bin/tr '\\000' x\nprintf '\\n'",
    );
    let document = next(root.path(), &env());
    assert_eq!(document["tools"]["verbose"]["complete"], false);
    assert_eq!(document["tools"]["verbose"]["probe"]["truncated"], true);
    assert!(document["tools"]["verbose"]["version"].is_null());
}
