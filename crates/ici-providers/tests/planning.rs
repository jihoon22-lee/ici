use ici_domain::{TaskKind, sha256};
use ici_providers::{
    ProviderKind, ProviderRequest,
    native_config::discover_native_configuration,
    plan,
    plan::{ruff_inventory_task, transform_compiler_argv},
    resolver::{ResolvedTool, ToolSource},
};
use std::{fs, path::Path};
fn request(root: &Path, kind: ProviderKind) -> ProviderRequest {
    ProviderRequest::new(
        kind,
        ResolvedTool {
            id: kind.name().into(),
            path: "/tool/native".into(),
            source: ToolSource::Explicit,
            digest: sha256("fixture"),
            version: None,
            configuration: vec![],
        },
        root,
        root,
        vec!["main.py".into()],
        "project.check",
    )
}
#[test]
fn planning_never_runs_or_creates_outputs_and_declares_native_effects() {
    let root = tempfile::tempdir().unwrap();
    for kind in [
        ProviderKind::RuffLint,
        ProviderKind::RuffFormat,
        ProviderKind::Ty,
        ProviderKind::Mypy,
        ProviderKind::Eslint,
        ProviderKind::Clippy,
        ProviderKind::Tsc,
    ] {
        let planned = plan(&request(root.path(), kind)).unwrap();
        assert_eq!(planned.task.env_overlay["RAYON_NUM_THREADS"], "2");
        assert_eq!(planned.task.env_overlay["OMP_NUM_THREADS"], "2");
        assert!(!planned.scope_complete && !planned.task.cacheable);
        assert_eq!(
            planned.task.kind == TaskKind::Prepare,
            matches!(kind, ProviderKind::Clippy | ProviderKind::Tsc)
        );
        assert!(!planned.task.argv.iter().any(|arg| {
            ["--fix", "--add-noqa", "--fix-only", "--unsafe-fixes"].contains(&arg.as_str())
        }));
    }
    assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
    let inventory =
        ruff_inventory_task(&plan(&request(root.path(), ProviderKind::RuffLint)).unwrap()).unwrap();
    assert_eq!(inventory.kind, TaskKind::Probe);
    assert!(inventory.argv.contains(&"--show-files".into()));
    assert_eq!(inventory.id, "project.check.files");
}
#[test]
fn nested_component_inputs_keep_workspace_source_and_component_configuration_roots() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ProviderKind::RuffLint);
    request.cwd = root.path().join("packages/backend");
    request.targets = vec!["packages/backend/main.py".into()];
    request.input_refs = vec!["ruff.toml".into(), "/shared/types.pyi".into()];
    let planned = plan(&request).unwrap();
    assert_eq!(
        planned.task.input_refs,
        vec![
            "/shared/types.pyi".to_string(),
            root.path()
                .join("packages/backend/main.py")
                .to_string_lossy()
                .into_owned(),
            root.path()
                .join("packages/backend/ruff.toml")
                .to_string_lossy()
                .into_owned(),
        ]
    );
}
#[test]
fn compiler_transform_keeps_native_unknown_flags_and_refuses_unexpanded_response() {
    let args: Vec<_> = [
        "g++",
        "-Iproject/include",
        "-DMODE=2",
        "-mabi=sysv",
        "-c",
        "source.cpp",
        "-o",
        "artifact.o",
        "-MFdep.d",
        "--coverage",
    ]
    .into_iter()
    .map(str::to_string)
    .collect();
    let result = transform_compiler_argv(&args, "source.cpp").unwrap();
    assert!(result.argv.contains(&"-mabi=sysv".into()));
    assert!(result.argv.contains(&"-Iproject/include".into()));
    assert!(!result.argv.contains(&"-c".into()) && !result.argv.contains(&"artifact.o".into()));
    assert!(result.argv.contains(&"-fsyntax-only".into()));
    assert_eq!(result.argv.last().map(String::as_str), Some("source.cpp"));
    assert!(
        transform_compiler_argv(
            &["g++".into(), "@args.rsp".into(), "source.cpp".into()],
            "source.cpp"
        )
        .is_err()
    );
    assert!(
        transform_compiler_argv(&["unknown-cc".into(), "source.cpp".into()], "source.cpp").is_err()
    );
}
#[test]
fn ruff_recursive_and_nested_configuration_content_changes_identity() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("pkg")).unwrap();
    fs::write(root.path().join("base.toml"), "[lint]\nselect=['F821']\n").unwrap();
    fs::write(root.path().join("ruff.toml"), "extend='base.toml'\n").unwrap();
    fs::write(root.path().join("pkg/ruff.toml"), "extend='../ruff.toml'\n").unwrap();
    let targets = vec!["pkg/main.py".into()];
    let first =
        discover_native_configuration(ProviderKind::RuffLint, root.path(), root.path(), &targets)
            .unwrap();
    assert!(first.complete);
    assert_eq!(first.files.len(), 3);
    assert!(
        first
            .input_refs()
            .iter()
            .all(|reference| Path::new(reference).is_file())
    );
    fs::write(
        root.path().join("base.toml"),
        "[lint]\nselect=['F821','F841']\n",
    )
    .unwrap();
    let second =
        discover_native_configuration(ProviderKind::RuffLint, root.path(), root.path(), &targets)
            .unwrap();
    assert_ne!(first.digest(), second.digest());
    fs::write(
        root.path().join("pkg/ruff.toml"),
        "[lint]\nselect=['I001']\n",
    )
    .unwrap();
    assert!(
        !discover_native_configuration(ProviderKind::RuffLint, root.path(), root.path(), &targets)
            .unwrap()
            .complete
    );
}
#[test]
fn eslint_relative_plugin_and_lockfile_changes_are_captured_without_executing_config() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("eslint.config.mjs"),
        "import plugin from './plugin.mjs'; throw new Error('must never execute during plan');\n",
    )
    .unwrap();
    fs::write(root.path().join("plugin.mjs"), "export default {};\n").unwrap();
    fs::write(root.path().join("package-lock.json"), "{}").unwrap();
    let first = discover_native_configuration(
        ProviderKind::Eslint,
        root.path(),
        root.path(),
        &["main.js".into()],
    )
    .unwrap();
    assert_eq!(first.files.len(), 3);
    assert!(!first.complete);
    fs::write(
        root.path().join("plugin.mjs"),
        "export default {rules:{}};\n",
    )
    .unwrap();
    let second = discover_native_configuration(
        ProviderKind::Eslint,
        root.path(),
        root.path(),
        &["main.js".into()],
    )
    .unwrap();
    assert_ne!(first.digest(), second.digest());
    fs::write(
        root.path().join("package-lock.json"),
        "{\"lockfileVersion\":3}",
    )
    .unwrap();
    assert_ne!(
        second.digest(),
        discover_native_configuration(
            ProviderKind::Eslint,
            root.path(),
            root.path(),
            &["main.js".into()]
        )
        .unwrap()
        .digest()
    );
}
#[test]
fn typescript_relative_extends_are_captured_and_jsonc_is_explicitly_unconfirmed() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("tsconfig.json"),
        "{\"extends\":\"./base.json\"}",
    )
    .unwrap();
    fs::write(
        root.path().join("base.json"),
        "{\"extends\":\"./strict.json\"}",
    )
    .unwrap();
    fs::write(
        root.path().join("strict.json"),
        "{\"compilerOptions\":{\"strict\":true}}",
    )
    .unwrap();
    let evidence = discover_native_configuration(
        ProviderKind::Tsc,
        root.path(),
        root.path(),
        &["main.ts".into()],
    )
    .unwrap();
    assert_eq!(evidence.files.len(), 3);
    assert!(!evidence.complete);
    fs::write(
        root.path().join("tsconfig.json"),
        "{//native comment\n\"strict\":true}",
    )
    .unwrap();
    let evidence = discover_native_configuration(
        ProviderKind::Tsc,
        root.path(),
        root.path(),
        &["main.ts".into()],
    )
    .unwrap();
    assert!(!evidence.complete);
    assert!(
        evidence
            .limitations
            .iter()
            .any(|line| line.contains("JSONC"))
    );
}
