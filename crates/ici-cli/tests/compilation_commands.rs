//! Standalone v1 export contracts, independent from analysis and tool availability.
use ici_cli::{
    commands,
    export_context::{self, ExportMetadata},
    registry,
};
use ici_cpp::{ContextRequest, load_compilation_context};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::symlink, path::Path};
fn environment() -> BTreeMap<String, String> {
    BTreeMap::from([("PATH".into(), String::new())])
}
fn fixture(args: &[&str]) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    let mut argv = vec!["/private/compiler/bin/clang++".to_string()];
    argv.extend(
        args.iter()
            .map(|value| value.replace("$ROOT", root.path().to_str().unwrap())),
    );
    argv.extend([
        "-c".into(),
        root.path().join("main.cpp").to_string_lossy().into_owned(),
        "-o".into(),
        root.path().join("main.o").to_string_lossy().into_owned(),
    ]);
    fs::write(root.path().join("compile_commands.json"),serde_json::to_vec(&json!([{"directory":root.path(),"file":root.path().join("main.cpp"),"arguments":argv}])).unwrap()).unwrap();
    root
}
fn invoke(root: &Path, args: &[&str]) -> commands::CommandOutput {
    let invocation = registry::parse(
        ["ici", "export-compilation-context"]
            .into_iter()
            .chain(args.iter().copied()),
    )
    .unwrap();
    commands::dispatch(&invocation, root, &environment()).unwrap()
}
fn payload(root: &Path) -> Value {
    let output = invoke(root, &[]);
    assert_eq!(output.exit_code, 0, "{}", output.stderr);
    serde_json::from_str(&output.stdout).unwrap()
}
#[test]
fn standalone_export_is_process_free_private_and_relocation_stable() {
    let a = fixture(&[
        "-std=c++20",
        "-I$ROOT/include",
        "-isystem",
        "/private/sdk/include",
        "-DROOT=\"$ROOT/assets\"",
        "-DPASSWORD=private-token",
        "-UTRACE",
        "-fplugin=/private/tool/plugin.so",
        "--target=x86_64-unknown-linux-gnu",
    ]);
    let b = fixture(&[
        "-std=c++20",
        "-I$ROOT/include",
        "-isystem",
        "/private/sdk/include",
        "-DROOT=\"$ROOT/assets\"",
        "-DPASSWORD=private-token",
        "-UTRACE",
        "-fplugin=/private/tool/plugin.so",
        "--target=x86_64-unknown-linux-gnu",
    ]);
    let first = payload(a.path());
    let second = payload(b.path());
    let encoded = first.to_string();
    for private in [a.path().to_str().unwrap(), "/private/", "private-token"] {
        assert!(!encoded.contains(private), "{encoded}");
    }
    assert_eq!(first["schema_version"], "ici.compilation-export/v1");
    assert_eq!(first["evidence"], "MEASURED");
    assert_eq!(first["compilation"]["comparison_state"], "inconclusive");
    assert_eq!(
        first["compilation"]["semantic_digest"],
        second["compilation"]["semantic_digest"]
    );
    assert_ne!(
        first["compilation"]["source_bytes_digest"],
        second["compilation"]["source_bytes_digest"]
    );
    let unit = &first["compilation"]["units"][0];
    assert_eq!(unit["compiler"]["family"], "clang");
    assert_eq!(unit["compiler"]["path"], "[external]");
    assert_eq!(unit["undefines"], json!(["TRACE"]));
    assert_eq!(
        unit["invocation"]["target_triple"],
        "x86_64-unknown-linux-gnu"
    );
    assert_eq!(
        unit["invocation"]["unmodeled_options"][0]["name"],
        "-fplugin="
    );
    assert_eq!(unit["defines"][1]["value_state"], "redacted");
    assert!(!a.path().join(".ici").exists());
    assert!(!a.path().join("main.o").exists());
}
#[test]
fn read_only_export_does_not_scan_declared_source_components_or_guess_git_version() {
    let root = fixture(&["-std=c++17"]);
    fs::write(root.path().join("ici.toml"),"schema_version=2\n[workspace]\nname='retained-name'\n[[components]]\nid='missing'\nroot='deleted-source-tree'\nlanguages=['cpp']\n").unwrap();
    assert!(ici_config::resolve_with_environment(root.path(), None, None, &environment()).is_err());
    let value = payload(root.path());
    assert_eq!(value["project"]["name"], "retained-name");
    assert_eq!(value["project"]["version"], "v1.0.0");
    assert_eq!(value["compilation"]["comparison_state"], "comparable");
    assert!(!root.path().join(".ici").exists());
}
#[test]
fn explicit_database_failure_and_ambiguous_selection_never_prepare_or_fallback() {
    let root = fixture(&[]);
    fs::write(
        root.path().join("CMakeLists.txt"),
        "invalid CMake must never run",
    )
    .unwrap();
    for path in [
        "/tmp/compile_commands.json",
        "../compile_commands.json",
        "C:\\build\\compile_commands.json",
        ".",
    ] {
        let output = invoke(root.path(), &["--database", path, "--prepare"]);
        assert_eq!(output.exit_code, 2);
        assert!(output.stdout.is_empty());
    }
    let output = invoke(root.path(), &["--database", "missing.json", "--prepare"]);
    assert_ne!(output.exit_code, 0);
    assert!(output.stderr.contains("compilation"));
    fs::create_dir(root.path().join("build")).unwrap();
    fs::copy(
        root.path().join("compile_commands.json"),
        root.path().join("build/compile_commands.json"),
    )
    .unwrap();
    let output = invoke(root.path(), &["--prepare"]);
    assert_ne!(output.exit_code, 0);
    assert!(output.stderr.contains("ambiguous"));
    assert!(!root.path().join(".ici").exists());
}
#[test]
fn output_is_atomic_bounded_and_preserves_inputs_and_redirected_files() {
    let root = fixture(&[]);
    let db = fs::read(root.path().join("compile_commands.json")).unwrap();
    let source = fs::read(root.path().join("main.cpp")).unwrap();
    fs::write(root.path().join("result.json"), "prior export").unwrap();
    let output = invoke(root.path(), &["-o", "result.json", "--pretty"]);
    assert_eq!(output.exit_code, 0, "{}", output.stderr);
    assert!(output.stdout.is_empty());
    let actual = fs::read_to_string(root.path().join("result.json")).unwrap();
    assert!(actual.starts_with("{\n"));
    assert!(actual.ends_with('\n'));
    fs::hard_link(
        root.path().join("compile_commands.json"),
        root.path().join("linked.json"),
    )
    .unwrap();
    symlink("compile_commands.json", root.path().join("redirected.json")).unwrap();
    symlink(root.path(), root.path().join("redirected-dir")).unwrap();
    for path in [
        "compile_commands.json",
        "main.cpp",
        "ici.toml",
        "linked.json",
        "redirected.json",
        "redirected-dir/export.json",
    ] {
        let output = invoke(root.path(), &["-o", path]);
        assert_eq!(output.exit_code, 2, "{path}: {}", output.stderr);
    }
    assert_eq!(
        fs::read(root.path().join("compile_commands.json")).unwrap(),
        db
    );
    assert_eq!(fs::read(root.path().join("main.cpp")).unwrap(), source);
    let output = invoke(root.path(), &["-o", "exports/nested/context.json"]);
    assert_eq!(output.exit_code, 0, "{}", output.stderr);
    assert!(root.path().join("exports/nested/context.json").is_file());
}
#[test]
fn public_payload_retains_opaque_semantics_duplicates_and_fatal_context_refusal() {
    let root = fixture(&["-O2"]);
    let context = load_compilation_context(root.path(), &ContextRequest::default());
    let metadata = ExportMetadata {
        backend: Some("cmake".into()),
        name: "sample".into(),
        version: "v1.2.3".into(),
        project_type: "cpp".into(),
        generator: Some("Ninja".into()),
    };
    let mut duplicate = context.clone();
    duplicate.units.push(duplicate.units[0].clone());
    let value =
        export_context::compilation_export_payload(root.path(), &metadata, &duplicate).unwrap();
    assert_eq!(value["compilation"]["units"][0]["state"]["duplicate"], true);
    assert_eq!(value["compilation"]["units"][1]["state"]["unit_index"], 1);
    let mut changed = context.clone();
    changed.units[0].argv.push("-fno-exceptions".into());
    let a = export_context::compilation_export_payload(root.path(), &metadata, &context).unwrap();
    let b = export_context::compilation_export_payload(root.path(), &metadata, &changed).unwrap();
    assert_ne!(
        a["compilation"]["semantic_digest"],
        b["compilation"]["semantic_digest"]
    );
    changed.diagnostics.push(ici_cpp::ContextDiagnostic::error(
        "invalid-source",
        "unsafe",
    ));
    assert!(
        export_context::compilation_export_payload(root.path(), &metadata, &changed)
            .unwrap_err()
            .contains("fatal")
    );
    assert!(
        export_context::render_compilation_export(
            &json!({"oversized":"x".repeat(32*1024*1024)}),
            false
        )
        .is_err()
    );
}
#[test]
fn output_preflight_precedes_any_explicit_native_preparation() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(root.path().join("CMakeLists.txt"),"cmake_minimum_required(VERSION 3.16)\nproject(sample LANGUAGES CXX)\nadd_executable(sample main.cpp)\n").unwrap();
    let output = invoke(root.path(), &["--prepare", "-o", "main.cpp"]);
    assert_eq!(output.exit_code, 2);
    assert!(!root.path().join(".ici").exists());
}
#[test]
#[ignore = "explicit actual canonical CMake context preparation"]
fn actual_cmake_export_prepares_only_missing_context_without_running_analysis() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(root.path().join("CMakeLists.txt"),"cmake_minimum_required(VERSION 3.16)\nproject(sample LANGUAGES CXX)\nadd_executable(sample main.cpp)\n").unwrap();
    let invocation = registry::parse(["ici", "export-compilation-context", "--prepare"]).unwrap();
    let environment = BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]);
    let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
    assert_eq!(output.exit_code, 0, "{}", output.stderr);
    let value: Value = serde_json::from_str(&output.stdout).unwrap();
    assert_eq!(value["compilation"]["units"][0]["source"], "main.cpp");
    assert_eq!(value["project"]["backend"], "cmake");
    assert!(value["compilation"]["generator"].is_string());
    assert!(!root.path().join("result.json").exists());
    assert!(!root.path().join("report.html").exists());
}

#[test]
fn frozen_python_v1_compilation_payload_corpus_agrees() {
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/compilation-export-oracle.json"
    ))
    .unwrap();
    assert_eq!(
        corpus["oracle_commit"],
        "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
    );
    let metadata = ExportMetadata {
        backend: Some("cmake".into()),
        name: "sample".into(),
        version: "v1.2.3".into(),
        project_type: "cpp".into(),
        generator: Some("Ninja".into()),
    };
    for case in corpus["cases"].as_array().unwrap() {
        let unit: ici_cpp::CompilationUnit = serde_json::from_value(case["unit"].clone()).unwrap();
        let context = ici_cpp::CompilationContext {
            database_path: Some("compile_commands.json".into()),
            database_digest: Some(format!("sha256:{}", "a".repeat(64))),
            origin: "configured".into(),
            units: vec![unit],
            ..Default::default()
        };
        let actual = export_context::compilation_export_payload(
            Path::new("/fixture/project"),
            &metadata,
            &context,
        )
        .unwrap();
        let mut expected = case["expected"].clone();
        expected["producer"]["version"] = actual["producer"]["version"].clone();
        assert_eq!(actual, expected, "{}", case["name"]);
    }
}

#[test]
fn static_metadata_uses_legacy_top_level_name_version_and_root_qmake_descriptor() {
    let root = fixture(&[]);
    fs::write(
        root.path().join("ici.toml"),
        "name='sample-project'\nversion='2.3.4'\n",
    )
    .unwrap();
    fs::write(root.path().join("sample.pro"), "SOURCES += main.cpp\n").unwrap();
    let value = payload(root.path());
    assert_eq!(
        value["project"],
        json!({"backend":"qmake","name":"sample-project","type":"cpp","version":"v2.3.4"})
    );
    assert!(!root.path().join(".ici").exists());
}

#[test]
#[ignore = "explicit actual Qt5 and Qt6 same-binary compilation export preparation"]
fn actual_qmake_export_prepares_qt5_and_qt6_with_same_native_binary() {
    for variable in ["ICI_TEST_QMAKE", "ICI_TEST_QMAKE6"] {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("main.cpp"),
            "#include <QString>\nint main(){return QString().size();}\n",
        )
        .unwrap();
        fs::write(root.path().join("sample.pro"),"QT += core\nQT -= gui\nCONFIG += console c++17\nCONFIG -= app_bundle\nTEMPLATE=app\nSOURCES += main.cpp\nTARGET=sample\n").unwrap();
        let qmake = std::env::var(variable).expect(variable);
        fs::write(
            root.path().join("ici.toml"),
            format!(
                "schema_version=2\n[tools.qmake]\npath={}\n",
                serde_json::to_string(&qmake).unwrap()
            ),
        )
        .unwrap();
        let output = std::process::Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(["export-compilation-context", "--prepare"])
            .current_dir(root.path())
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let value: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(value["project"]["backend"], "qmake");
        assert_eq!(value["compilation"]["generator"], "qmake");
        assert!(
            value["compilation"]["units"]
                .as_array()
                .unwrap()
                .iter()
                .any(|unit| unit["source"] == "main.cpp")
        );
        assert!(!String::from_utf8_lossy(&output.stdout).contains(root.path().to_str().unwrap()));
        assert!(!root.path().join("report.html").exists());
    }
}
