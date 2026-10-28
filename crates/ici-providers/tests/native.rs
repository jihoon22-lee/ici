//! Explicit external-tool gate. Run --ignored with all pinned paths; absence is a failure.
use ici_domain::{TaskKind, TaskState};
use ici_execution::{Cancellation, Runner};
use ici_providers::{
    ProviderKind, ProviderPlan, ProviderRequest, observe_full, plan, resolver::ToolRequest,
};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    process::Command,
};

fn required(name: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| panic!("required native fixture tool {name} is missing"))
}
fn prepare(root: &Path, kind: ProviderKind, variable: &str, targets: &[&str]) -> ProviderRequest {
    let tool = ToolRequest {
        id: kind.name().into(),
        explicit: Some(PathBuf::from(required(variable))),
        project_root: root.into(),
        pack_root: None,
        path_env: None,
    }
    .resolve()
    .unwrap();
    let mut request = ProviderRequest::new(
        kind,
        tool,
        root,
        root,
        targets.iter().map(|s| (*s).into()).collect(),
        format!("project.{}", kind.name()),
    );
    request.component_id = Some("project".into());
    request.analysis_unit_ids = vec!["native-fixture".into()];
    request
}
fn execute(plan: &ProviderPlan) -> ici_domain::AnalysisOutput {
    let mut plan = plan.clone();
    let inputs: Vec<_> = plan
        .context
        .targets
        .iter()
        .filter_map(|path| {
            fs::read(plan.context.workspace_root.join(path))
                .ok()
                .map(|bytes| ici_domain::SourceInput {
                    path: path.clone(),
                    bytes,
                })
        })
        .collect();
    ici_providers::attach_sources(&mut plan, ici_providers::source_coordinates(&inputs));
    let mut environment: BTreeMap<_, _> = std::env::vars().collect();
    if let Ok(libraries) = std::env::var("ICI_TEST_CPP_LIBRARY_PATH") {
        environment.insert("LD_LIBRARY_PATH".into(), libraries);
    }
    let outcome = Runner::with_environment(environment).run(&plan.task, &Cancellation::default());
    let output = observe_full(&plan, &outcome);
    assert_eq!(
        output.observation.state,
        TaskState::Succeeded,
        "provider={:?}\nobservation={:?}\nstdout={}\nstderr={}",
        plan.kind,
        output.observation,
        String::from_utf8_lossy(&outcome.stdout),
        String::from_utf8_lossy(&outcome.stderr)
    );
    output
}
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn ruff_lint_native_exclude_and_fix_configuration_are_honored_read_only() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"), "missing_name\n").unwrap();
    fs::write(root.path().join("ignored.py"), "ignored_missing\n").unwrap();
    fs::write(
        root.path().join("ruff.toml"),
        "fix=true\nexclude=['ignored.py']\n[lint]\nselect=['F821']\n",
    )
    .unwrap();
    let request = prepare(
        root.path(),
        ProviderKind::RuffLint,
        "ICI_TEST_RUFF",
        &["main.py", "ignored.py"],
    );
    let output = execute(&plan(&request).unwrap());
    assert_eq!(output.observation.findings.len(), 1);
    assert_eq!(output.observation.findings[0].native_rule_id, "F821");
    assert_eq!(
        fs::read_to_string(root.path().join("main.py")).unwrap(),
        "missing_name\n"
    );
    assert!(!root.path().join(".ruff_cache").exists());
    assert!(
        !output
            .targets
            .iter()
            .any(|target| target.file_path == "ignored.py" && target.status == "PASS")
    );
}
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn ruff_format_reports_without_writing_or_formatting_excluded_file() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"), "value=1\n").unwrap();
    fs::write(root.path().join("ignored.py"), "other=2\n").unwrap();
    fs::write(root.path().join("ruff.toml"), "exclude=['ignored.py']\n").unwrap();
    let output = execute(
        &plan(&prepare(
            root.path(),
            ProviderKind::RuffFormat,
            "ICI_TEST_RUFF",
            &["main.py", "ignored.py"],
        ))
        .unwrap(),
    );
    assert_eq!(output.observation.findings.len(), 1);
    assert_eq!(
        output.observation.findings[0].primary_location.path,
        "main.py"
    );
    assert_eq!(
        fs::read_to_string(root.path().join("main.py")).unwrap(),
        "value=1\n"
    );
}
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn ty_sarif_keeps_findings_and_native_rules() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"), "value: int = 'bad'\n").unwrap();
    let mut request = prepare(root.path(), ProviderKind::Ty, "ICI_TEST_TY", &["main.py"]);
    request.options.python_version = Some("3.10".into());
    request.options.advisory = true;
    let planned = plan(&request).unwrap();
    assert!(planned.advisory);
    let output = execute(&planned);
    assert_eq!(
        output.observation.findings[0].native_rule_id,
        "invalid-assignment"
    );
    fs::write(
        root.path().join("pyproject.toml"),
        "[tool.ty.rules]\ninvalid-assignment='ignore'\n",
    )
    .unwrap();
    assert!(
        execute(&plan(&request).unwrap())
            .observation
            .findings
            .is_empty()
    );
}
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn mypy_native_configuration_and_precise_diagnostics_without_cache_write() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"), "value: int = 'bad'\n").unwrap();
    fs::write(
        root.path().join("mypy.ini"),
        "[mypy]\npython_version=3.10\n",
    )
    .unwrap();
    let request = prepare(
        root.path(),
        ProviderKind::Mypy,
        "ICI_TEST_MYPY",
        &["main.py"],
    );
    let output = execute(&plan(&request).unwrap());
    assert_eq!(output.observation.findings[0].native_rule_id, "assignment");
    assert_eq!(
        output.observation.findings[0].primary_location.start_line,
        1
    );
    assert!(!root.path().join(".mypy_cache").exists());
    fs::write(
        root.path().join("mypy.ini"),
        "[mypy]\npython_version=3.10\ndisable_error_code=assignment\n",
    )
    .unwrap();
    assert!(
        execute(&plan(&request).unwrap())
            .observation
            .findings
            .is_empty()
    );
}
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn cargo_clippy_discloses_and_executes_build_script_with_bounded_jobs() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("src")).unwrap();
    fs::write(
        root.path().join("Cargo.toml"),
        "[package]\nname='ici-native-fixture'\nversion='0.1.0'\nedition='2021'\n",
    )
    .unwrap();
    fs::write(
        root.path().join("src/lib.rs"),
        "pub fn answer() -> i32 { let unused = 1; 42 }\n",
    )
    .unwrap();
    fs::write(
        root.path().join("build.rs"),
        "fn main() { std::fs::write(\"ran-build-script\", \"yes\").unwrap(); }\n",
    )
    .unwrap();
    assert!(
        Command::new(required("ICI_TEST_CARGO"))
            .args(["generate-lockfile", "--offline"])
            .env("RUSTUP_AUTO_INSTALL", "0")
            .current_dir(root.path())
            .status()
            .unwrap()
            .success()
    );
    let request = prepare(
        root.path(),
        ProviderKind::Clippy,
        "ICI_TEST_CARGO",
        &["src/lib.rs", "build.rs"],
    );
    let planned = plan(&request).unwrap();
    assert_eq!(planned.task.kind, TaskKind::Prepare);
    let output = execute(&planned);
    assert!(
        output
            .observation
            .findings
            .iter()
            .any(|item| item.native_rule_id == "unused_variables")
    );
    assert_eq!(
        fs::read_to_string(root.path().join("ran-build-script")).unwrap(),
        "yes"
    );
    assert!(root.path().join(".ici/rust-v1/cargo-target").exists());
}
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn eslint_native_json_rules_and_ignored_files_are_not_silent_passes() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.js"), "missing_value;\n").unwrap();
    fs::write(
        root.path().join("eslint.config.mjs"),
        "export default [{rules:{'no-undef':'error'}}];\n",
    )
    .unwrap();
    let request = prepare(
        root.path(),
        ProviderKind::Eslint,
        "ICI_TEST_ESLINT",
        &["main.js"],
    );
    let output = execute(&plan(&request).unwrap());
    assert_eq!(output.observation.findings[0].native_rule_id, "no-undef");
    fs::write(
        root.path().join("eslint.config.mjs"),
        "export default [{rules:{'no-undef':'off'}}];\n",
    )
    .unwrap();
    assert!(
        execute(&plan(&request).unwrap())
            .observation
            .findings
            .is_empty()
    );
}
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn typescript_composite_keeps_metadata_isolated_and_never_emits_javascript() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("main.ts"),
        "const value: number = 'bad';\n",
    )
    .unwrap();
    fs::write(
        root.path().join("tsconfig.json"),
        r#"{"compilerOptions":{"strict":true,"composite":true},"include":["main.ts"]}"#,
    )
    .unwrap();
    let mut request = prepare(root.path(), ProviderKind::Tsc, "ICI_TEST_TSC", &["main.ts"]);
    request.options.project_config = Some("tsconfig.json".into());
    let planned = plan(&request).unwrap();
    assert_eq!(planned.task.kind, TaskKind::Prepare);
    let output = execute(&planned);
    assert_eq!(output.observation.findings[0].native_rule_id, "2322");
    assert!(
        root.path()
            .join(".ici/rust-v1/typescript/analysis.tsbuildinfo")
            .exists()
    );
    assert!(!root.path().join("main.js").exists());
    assert!(!root.path().join("main.d.ts").exists());
}
fn compiler_request(root: &Path, variable: &str) -> ProviderRequest {
    fs::write(
        root.join("main.cpp"),
        "int answer() { int unused = 1; return 42; }\n",
    )
    .unwrap();
    let mut request = prepare(root, ProviderKind::Compiler, variable, &["main.cpp"]);
    request.options.compiler_argv = vec![
        request.tool.path.to_string_lossy().into_owned(),
        "-Wall".into(),
        "-Wextra".into(),
        "-c".into(),
        "main.cpp".into(),
        "-o".into(),
        "main.o".into(),
    ];
    request.options.source_token = Some("main.cpp".into());
    request
}
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn real_gcc_and_clang_diagnostics_preserve_flags_without_building_artifacts() {
    for variable in ["ICI_TEST_GXX", "ICI_TEST_CLANG"] {
        let root = tempfile::tempdir().unwrap();
        let request = compiler_request(root.path(), variable);
        let output = execute(&plan(&request).unwrap());
        assert!(
            output
                .observation
                .findings
                .iter()
                .any(|finding| finding.native_rule_id.contains("unused-variable")),
            "{output:?}"
        );
        assert!(!root.path().join("main.o").exists());
    }
}
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn clang_tidy_replays_native_compile_database() {
    let root = tempfile::tempdir().unwrap();
    let compiler = compiler_request(root.path(), "ICI_TEST_CLANG");
    let database = serde_json::json!([{"directory":root.path(),"file":"main.cpp","arguments":compiler.options.compiler_argv}]);
    fs::write(
        root.path().join("compile_commands.json"),
        database.to_string(),
    )
    .unwrap();
    let mut request = prepare(
        root.path(),
        ProviderKind::ClangTidy,
        "ICI_TEST_TIDY",
        &["main.cpp"],
    );
    request.options.database_dir = Some(root.path().to_string_lossy().into_owned());
    let output = execute(&plan(&request).unwrap());
    assert!(
        output
            .observation
            .findings
            .iter()
            .any(|finding| finding.native_rule_id.contains("unused-variable")),
        "{output:?}"
    );
}
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn clazy_reports_qt_hash_namespace_from_native_context() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "#include <QtCore/QHash>\nnamespace Project { struct Key {}; }\nuint qHash(const Project::Key &, uint seed = 0) { return seed; }\n").unwrap();
    let headers = required("ICI_TEST_QT_HEADERS");
    let arguments = vec![
        required("ICI_TEST_CLANG"),
        "-std=c++17".into(),
        "-fPIC".into(),
        format!("-I{headers}"),
        format!("-I{headers}/QtCore"),
        "-c".into(),
        "main.cpp".into(),
        "-o".into(),
        "main.o".into(),
    ];
    fs::write(
        root.path().join("compile_commands.json"),
        serde_json::json!([{"directory":root.path(),"file":"main.cpp","arguments":arguments}])
            .to_string(),
    )
    .unwrap();
    let mut request = prepare(
        root.path(),
        ProviderKind::Clazy,
        "ICI_TEST_CLAZY",
        &["main.cpp"],
    );
    request.options.database_dir = Some(root.path().to_string_lossy().into_owned());
    request.options.clazy_checks = Some("qhash-namespace".into());
    let output = execute(&plan(&request).unwrap());
    assert!(
        output
            .observation
            .findings
            .iter()
            .any(|finding| finding.native_rule_id.contains("qhash-namespace")),
        "{output:?}"
    );
}
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn ruff_explicit_inventory_proves_included_scope_and_rejects_changed_configuration() {
    use ici_providers::{
        native_config::discover_native_configuration,
        plan::{apply_ruff_inventory, ruff_inventory_task},
    };
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"), "value = 1\n").unwrap();
    fs::write(root.path().join("ignored.py"), "missing\n").unwrap();
    fs::write(
        root.path().join("ruff.toml"),
        "exclude=['ignored.py']\n[lint]\nselect=['F821']\n",
    )
    .unwrap();
    let request = prepare(
        root.path(),
        ProviderKind::RuffLint,
        "ICI_TEST_RUFF",
        &["main.py", "ignored.py"],
    );
    let evidence = discover_native_configuration(
        ProviderKind::RuffLint,
        root.path(),
        root.path(),
        &request.targets,
    )
    .unwrap();
    assert!(evidence.complete);
    let mut planned = plan(&request).unwrap();
    let inventory = Runner::default().run(
        &ruff_inventory_task(&planned).unwrap(),
        &Cancellation::default(),
    );
    apply_ruff_inventory(&mut planned, &inventory, &evidence).unwrap();
    assert!(planned.scope_complete && planned.task.cacheable);
    let output = execute(&planned);
    assert!(output.observation.findings.is_empty());
    assert_eq!(
        output
            .targets
            .iter()
            .find(|item| item.file_path == "main.py")
            .unwrap()
            .status,
        "PASS"
    );
    assert_eq!(
        output
            .targets
            .iter()
            .find(|item| item.file_path == "ignored.py")
            .unwrap()
            .status,
        "SKIP"
    );
    fs::write(
        root.path().join("ruff.toml"),
        "[lint]\nselect=['F821','F841']\n",
    )
    .unwrap();
    assert!(apply_ruff_inventory(&mut planned, &inventory, &evidence).is_err());
}
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn native_configuration_failures_do_not_turn_into_clean_observations() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"), "value = 1\n").unwrap();
    fs::write(
        root.path().join("ruff.toml"),
        "required-version='==0.0.1'\n",
    )
    .unwrap();
    let request = prepare(
        root.path(),
        ProviderKind::RuffLint,
        "ICI_TEST_RUFF",
        &["main.py"],
    );
    let planned = plan(&request).unwrap();
    let outcome = Runner::default().run(&planned.task, &Cancellation::default());
    assert_eq!(outcome.exit_code, Some(2));
    let observation = observe_full(&planned, &outcome).observation;
    assert_eq!(observation.state, TaskState::Failed);
    assert!(observation.findings.is_empty());
    fs::write(root.path().join("main.ts"), "const value = 1;\n").unwrap();
    let mut request = prepare(root.path(), ProviderKind::Tsc, "ICI_TEST_TSC", &["main.ts"]);
    request.options.project_config = Some("missing-tsconfig.json".into());
    let planned = plan(&request).unwrap();
    let outcome = Runner::default().run(&planned.task, &Cancellation::default());
    let observation = observe_full(&planned, &outcome).observation;
    assert_eq!(observation.state, TaskState::Failed);
    assert!(observation.findings.is_empty());
}

#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn native_unicode_and_bom_columns_use_codepoints_across_utf16_and_byte_tools() {
    for bom in [false, true] {
        let root = tempfile::tempdir().unwrap();
        let sources = [
            ("main.js", "const s = '😀'; missing();\n"),
            ("main.ts", "const s = '😀'; const value: string = 42;\n"),
            ("main.py", "s = '😀'; value: str = 42\n"),
            (
                "main.cpp",
                "int main(){ const char* s=\"😀\"; return missing; }\n",
            ),
        ];
        for (name, source) in sources {
            let mut bytes = if bom { vec![0xef, 0xbb, 0xbf] } else { vec![] };
            bytes.extend_from_slice(source.as_bytes());
            fs::write(root.path().join(name), bytes).unwrap();
        }
        fs::write(
            root.path().join("eslint.config.mjs"),
            "export default [{rules:{'no-undef':'error'}}];\n",
        )
        .unwrap();
        for (kind, variable, path, column) in [
            (ProviderKind::Eslint, "ICI_TEST_ESLINT", "main.js", 16),
            (ProviderKind::Tsc, "ICI_TEST_TSC", "main.ts", 22),
            (ProviderKind::Mypy, "ICI_TEST_MYPY", "main.py", 23),
            (ProviderKind::Ty, "ICI_TEST_TY", "main.py", 23),
            (ProviderKind::Compiler, "ICI_TEST_GXX", "main.cpp", 39),
            (ProviderKind::Compiler, "ICI_TEST_CLANG", "main.cpp", 39),
        ] {
            let mut request = prepare(root.path(), kind, variable, &[path]);
            if kind == ProviderKind::Compiler {
                request.options.compiler_argv = vec![required(variable), "-c".into(), path.into()];
                request.options.source_token = Some(path.into());
            }
            let output = execute(&plan(&request).unwrap());
            let finding = output
                .observation
                .findings
                .iter()
                .find(|finding| finding.primary_location.path == path)
                .unwrap();
            assert_eq!(
                finding.primary_location.start_column,
                Some(column),
                "{kind:?}, BOM={bom}: {finding:?}"
            );
            if kind == ProviderKind::Eslint {
                assert_eq!(finding.primary_location.end_column, Some(22));
            }
        }
    }
}

#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn actual_gcc_and_clang_fixits_keep_unicode_exclusive_insertions_including_bom() {
    for (variable, json_mode) in [
        ("ICI_TEST_GXX", false),
        ("ICI_TEST_GXX", true),
        ("ICI_TEST_CLANG", false),
    ] {
        for bom in [false, true] {
            let root = tempfile::tempdir().unwrap();
            let mut request = compiler_request(root.path(), variable);
            let body = "int main(){ const char* s=\"λ😀\"; return s[0] }\n";
            let text = format!("{}{body}", if bom { "\u{feff}" } else { "" });
            fs::write(root.path().join("main.cpp"), &text).unwrap();
            if json_mode {
                request
                    .options
                    .compiler_argv
                    .push("-fdiagnostics-format=json".into());
            }
            let plan = plan(&request).unwrap();
            assert_eq!(
                plan.task
                    .argv
                    .iter()
                    .any(|arg| arg == "-fdiagnostics-parseable-fixits"),
                !json_mode
            );
            let output = execute(&plan);
            let expected = body[..body.find(" }").unwrap()].chars().count() as u64 + 1;
            let edits = output.extra["finding_details"]
                .as_object()
                .unwrap()
                .values()
                .flat_map(|detail| detail["fixes"].as_array().unwrap())
                .flat_map(|fix| fix["replacements"].as_array().unwrap())
                .filter(|edit| edit["replacement"] == ";")
                .collect::<Vec<_>>();
            assert!(
                !edits.is_empty(),
                "{variable} JSON={json_mode} BOM={bom}: {output:?}"
            );
            for edit in edits {
                assert_eq!(
                    edit["column_kind"], "unicodeCodePoints",
                    "{variable} JSON={json_mode} BOM={bom}: {edit}"
                );
                assert_eq!(edit["end_exclusive"], true);
                assert_eq!(
                    edit["start_column"], expected,
                    "{variable} JSON={json_mode} BOM={bom}: {edit}"
                );
                assert_eq!(edit["end_column"], edit["start_column"]);
            }
            assert_eq!(
                fs::read_to_string(root.path().join("main.cpp")).unwrap(),
                text
            );
            assert!(!root.path().join("main.o").exists());
        }
    }
}
