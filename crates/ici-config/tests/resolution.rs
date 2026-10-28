use ici_config::*;
use ici_domain::{Profile, ScopeKind};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};
use tempfile::TempDir;

struct Project {
    temporary: TempDir,
}
impl Project {
    fn new() -> Self {
        let result = Self {
            temporary: TempDir::new().unwrap(),
        };
        fs::create_dir(result.root().join(".git")).unwrap();
        result
    }
    fn root(&self) -> &Path {
        self.temporary.path()
    }
    fn write(&self, path: &str, text: &str) -> PathBuf {
        let path = self.root().join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, text).unwrap();
        path
    }
    fn resolve(&self) -> ResolvedProject {
        resolve_with_environment(self.root(), None, None, &BTreeMap::new()).unwrap()
    }
    fn failure(&self) -> ConfigError {
        resolve_with_environment(self.root(), None, None, &BTreeMap::new()).unwrap_err()
    }
}
fn scope(project: &ResolvedProject) -> Vec<(String, String, Vec<String>, Vec<String>)> {
    project
        .components
        .iter()
        .map(|c| {
            (
                c.id.clone(),
                c.root.clone(),
                c.languages.clone(),
                c.files.clone(),
            )
        })
        .collect()
}

#[test]
fn no_config_minimal_and_init_have_identical_scope_including_root_files() {
    let tree = Project::new();
    tree.write("main.py", "answer = 42\n");
    tree.write("src/lib.py", "x=1\n");
    for directory in [".venv", "node_modules", "build", "target", "dist", ".ici"] {
        tree.write(&format!("{directory}/ignored.py"), "ignored=1\n");
    }
    let original = tree.resolve();
    assert_eq!(original.components[0].files, vec!["main.py", "src/lib.py"]);
    let sub =
        resolve_with_environment(&tree.root().join("src"), None, None, &BTreeMap::new()).unwrap();
    assert_eq!(scope(&original), scope(&sub));
    assert!(!tree.root().join("ici.toml").exists());
    let proposal = scaffold_with_environment(tree.root(), &BTreeMap::new()).unwrap();
    assert_eq!(proposal.source_count, 2);
    assert!(!tree.root().join("ici.toml").exists());
    write_scaffold(&proposal, None).unwrap();
    let initialized = tree.resolve();
    assert_eq!(scope(&original), scope(&initialized));
    assert_eq!(initialized.profile, Profile::Standard);
    assert!(write_scaffold(&proposal, None).is_err());
    assert_eq!(
        fs::read_to_string(tree.root().join("ici.toml")).unwrap(),
        proposal.text
    );
}

#[test]
fn scalar_override_keeps_defaults_and_required_fallback() {
    let tree = Project::new();
    tree.write("main.py", "x=1\n");
    tree.write("build/hidden.py", "x=1\n");
    tree.write(
        "ici.toml",
        "schema_version=2\nprofile='fast'\n[checks.\"python.lint\"]\nenabled=false\n",
    );
    let project = tree.resolve();
    assert_eq!(project.profile, Profile::Fast);
    assert_eq!(project.components[0].files, vec!["main.py"]);
    let lint = &project.checks["python.lint"];
    assert!(!lint.enabled);
    assert!(lint.required_or(true));
    assert_eq!(project.effective["engines"]["test"]["min_line_cov"], 80.0);
    assert_eq!(project.origins["profile"].line, 2);
}

#[test]
fn exclude_replaces_but_extend_exclude_adds() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write("generated/a.py", "x=1");
    tree.write("build/b.py", "x=1");
    tree.write(
        "ici.toml",
        "schema_version=2\nextend_exclude=['generated/**']\n",
    );
    assert_eq!(tree.resolve().components[0].files, vec!["main.py"]);
    tree.write("ici.toml", "schema_version=2\nexclude=['generated/**']\n");
    assert_eq!(
        tree.resolve().components[0].files,
        vec!["build/b.py", "main.py"]
    );
}

#[test]
fn explicit_empty_components_is_an_error_without_fallback() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write("ici.toml", "schema_version=2\ncomponents=[]\n");
    let error = tree.failure();
    assert!(error.message.contains("explicitly empty"));
    assert_eq!(error.origin.line, 2);
}

#[test]
fn monorepo_packages_get_disjoint_owned_sources_and_nested_workspace_stays_separate() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write(
        "python/tool/pyproject.toml",
        "[project]\nname='tool'\nversion='1'\n",
    );
    tree.write("python/tool/src/a.py", "x=1");
    tree.write("rust/Cargo.toml", "[workspace]\nmembers=['one']\n");
    tree.write(
        "rust/one/Cargo.toml",
        "[package]\nname='one'\nversion='1.0.0'\n",
    );
    tree.write("rust/one/src/lib.rs", "pub fn answer(){}\n");
    tree.write("web/package.json", "{\"name\":\"web\"}");
    tree.write("web/main.ts", "const n=1;\n");
    tree.write("independent/ici.toml", "schema_version=2\n");
    tree.write("independent/main.py", "x=1");
    let project = tree.resolve();
    assert_eq!(project.components.len(), 4);
    assert_eq!(
        project
            .components
            .iter()
            .find(|c| c.id == "project")
            .unwrap()
            .files,
        vec!["main.py"]
    );
    let all: Vec<_> = project
        .components
        .iter()
        .flat_map(|c| c.files.clone())
        .collect();
    assert_eq!(all.len(), 4);
    assert!(!all.iter().any(|file| file.starts_with("independent/")));
    let inner = resolve_with_environment(
        &tree.root().join("independent"),
        None,
        None,
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(inner.root, tree.root().join("independent"));
    assert_eq!(inner.components[0].files, vec!["main.py"]);
}

#[test]
fn ici_precedes_pyproject_and_explicit_file_is_isolated() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write(
        "pyproject.toml",
        "[tool.ici]\nschema_version=2\nprofile='deep'\n",
    );
    assert_eq!(tree.resolve().profile, Profile::Deep);
    tree.write("ici.toml", "schema_version=2\nprofile='fast'\n");
    assert_eq!(tree.resolve().profile, Profile::Fast);
    tree.write("override.toml", "schema_version=2\nprofile='standard'\n");
    tree.write("ici.toml", "invalid ==\n");
    let selected = resolve_with_environment(
        tree.root(),
        Some(Path::new("override.toml")),
        None,
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(selected.profile, Profile::Standard);
    assert_eq!(
        selected.config_source,
        Some(tree.root().join("override.toml"))
    );
}

#[test]
fn unknown_future_and_malformed_config_never_modify_original() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    for content in [
        "schema_version=99\n",
        "schema_version=2\nfuture_policy=true\n",
        "schema_version=2\n[broken\n",
    ] {
        tree.write("ici.toml", content);
        let error = tree.failure();
        assert!(error.origin.file.ends_with("ici.toml"));
        assert_eq!(
            fs::read_to_string(tree.root().join("ici.toml")).unwrap(),
            content
        );
    }
    tree.write(
        "ici.toml",
        "# comment\nschema_version=2\n[checks.\"python.lint\"]\nenabled='yes'\n",
    );
    let error = tree.failure();
    assert_eq!(error.origin.line, 4);
    assert!(error.origin.key.ends_with("enabled"));
}

#[test]
fn v1_child_reference_anchors_root_and_interpreter_to_declaring_file() {
    let tree = Project::new();
    tree.write("src/root.py", "x=1");
    tree.write("pkg/main.py", "x=1");
    tree.write("pkg/ici.toml","schema_version=1\n[component]\nroot='.'\nlanguages=['python']\n[python]\nexecutable='./.venv/bin/python'\n");
    tree.write("ici.toml","schema_version=1\n[workspace]\nname='suite'\n[[components]]\nid='pkg'\nconfig='pkg/ici.toml'\n");
    let project = tree.resolve();
    assert_eq!(project.components.len(), 1);
    let component = &project.components[0];
    assert_eq!(component.root, "pkg");
    assert_eq!(component.files, vec!["pkg/main.py"]);
    assert_eq!(
        component.python_executable.as_deref(),
        Some("pkg/.venv/bin/python")
    );
    assert_eq!(project.configuration_files.len(), 2);
}

#[test]
fn standalone_component_requires_explicit_config_and_preserves_scope_axis() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write(
        "ici.toml",
        "schema_version=1\n[component]\nroot='.'\nlanguages=['python']\n",
    );
    assert!(tree.failure().message.contains("unregistered"));
    let project = resolve_with_environment(
        tree.root(),
        Some(Path::new("ici.toml")),
        None,
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(project.scope_kind, ScopeKind::Standalone);
    assert_eq!(project.components[0].files, vec!["main.py"]);
}

#[test]
fn explicit_component_scope_never_expands_to_sibling_sources() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write("pkg/a.py", "x=1");
    tree.write("sibling/b.py", "x=1");
    tree.write(
        "ici.toml",
        "schema_version=2\n[[components]]\nid='pkg'\nroot='pkg'\nlanguages=['python']\n",
    );
    assert_eq!(tree.resolve().components[0].files, vec!["pkg/a.py"]);
}

#[test]
fn lists_replace_and_maps_merge_without_mutating_input() {
    let mut base = json!({"a":{"first":1,"second":2},"argv":["one","two"]});
    let patch = json!({"a":{"first":3},"argv":["replacement"]});
    let original = patch.clone();
    merge(&mut base, &patch);
    assert_eq!(
        base,
        json!({"a":{"first":3,"second":2},"argv":["replacement"]})
    );
    assert_eq!(patch, original);
}

#[test]
fn legacy_thresholds_and_runtime_are_preserved() {
    let tree = Project::new();
    tree.write("src/a.py", "x=1");
    tree.write("main.py", "x=1");
    tree.write("ici.toml","[ici]\nprofile='deep'\n[project]\nsource_dirs=['src']\n[engines.line]\nwarn_limit=700\n[engines.test]\npython='project-python'\n[engines.binary_compat]\nmax_glibc='2.28'\n");
    let project = tree.resolve();
    assert_eq!(project.schema_family, SchemaFamily::Legacy);
    assert_eq!(project.profile, Profile::Deep);
    assert_eq!(project.components[0].files, vec!["src/a.py"]);
    assert_eq!(
        project.components[0].python_executable.as_deref(),
        Some("project-python")
    );
    assert_eq!(project.effective["engines"]["line"]["warn_limit"], 700);
    assert_eq!(project.effective["engines"]["line"]["fail_limit"], 1000);
    assert_eq!(
        project.effective["engines"]["binary_compat"]["max_glibc"],
        "2.28"
    );
}

#[test]
fn bad_legacy_defaults_interaction_and_escaping_paths_fail_before_run() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write("ici.toml", "[engines.line]\nwarn_limit=2000\n");
    assert!(tree.failure().message.contains("exceed"));
    tree.write("ici.toml", "[project]\nsource_dirs=['../outside']\n");
    assert!(tree.failure().message.contains("escapes"));
}

#[test]
fn local_overlay_can_move_runtime_but_cannot_disable_check() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write(
        "ici.toml",
        "schema_version=2\n[[components]]\nid='project'\nroot='.'\nlanguages=['python']\n",
    );
    tree.write(
        "local/settings.toml",
        "[components.project.python]\nexecutable='../runtime/python'\n",
    );
    let project = resolve_with_environment(
        tree.root(),
        None,
        Some(Path::new("local/settings.toml")),
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(
        project.components[0].python_executable.as_deref(),
        Some("runtime/python")
    );
    tree.write(
        "local/settings.toml",
        "[checks.\"python.lint\"]\nenabled=false\n",
    );
    let error = resolve_with_environment(
        tree.root(),
        None,
        Some(Path::new("local/settings.toml")),
        &BTreeMap::new(),
    )
    .unwrap_err();
    assert!(error.message.contains("never inspection policy"));
}

#[test]
fn component_cannot_weaken_required_root_check_without_exemption() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    let config = "schema_version=2\n[checks.\"python.lint\"]\nrequired=true\n[[components]]\nid='project'\nroot='.'\nlanguages=['python']\n[components.checks.\"python.lint\"]\nrequired=false\n";
    tree.write("ici.toml", config);
    assert!(tree.failure().message.contains("cannot disable or weaken"));
    tree.write("ici.toml",&config.replace("required=true\n","required=true\n[checks.\"python.lint\".exemptions.project]\nreason='reviewed legacy exception'\n"));
    let project = tree.resolve();
    assert!(!project.components[0].checks["python.lint"].required_or(true));
}

#[test]
fn native_mypy_settings_win_without_copying_policy_and_conflict_is_explicit() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write("mypy.ini", "[mypy]\nstrict=True\n");
    assert_eq!(
        tree.resolve().components[0].python_type_provider.as_deref(),
        Some("mypy")
    );
    tree.write("ty.toml", "[environment]\npython-version='3.10'\n");
    assert!(tree.failure().message.contains("both native"));
    tree.write(
        "ici.toml",
        "schema_version=2\n[python]\ntype_provider='ty'\n",
    );
    let project = tree.resolve();
    assert_eq!(
        project.components[0].python_type_provider.as_deref(),
        Some("ty")
    );
    assert!(project.effective.get("mypy").is_none());
}

#[test]
fn environment_paths_are_explicit_and_missing_values_fail() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write(
        "ici.toml",
        "schema_version=2\n[python]\nexecutable='${env:PROJECT_RUNTIME}/python'\n",
    );
    assert!(tree.failure().message.contains("PROJECT_RUNTIME"));
    let environment = BTreeMap::from([("PROJECT_RUNTIME".into(), "runtime".into())]);
    let project = resolve_with_environment(tree.root(), None, None, &environment).unwrap();
    assert_eq!(
        project.components[0].python_executable.as_deref(),
        Some("runtime/python")
    );
}

#[test]
fn legacy_migration_is_preview_new_output_and_comment_preserving() {
    let tree = Project::new();
    let source = tree.write(
        "ici.toml",
        "# KEEP USER NOTE\n[ici]\nprofile='fast' # important\n[engines.line]\nwarn_limit=650\n",
    );
    let before = fs::read(&source).unwrap();
    let preview = migrate_preview(&source).unwrap();
    assert_eq!(fs::read(&source).unwrap(), before);
    assert!(preview.proposed_text.contains("# KEEP USER NOTE"));
    assert!(preview.proposed_text.contains("# important"));
    assert!(preview.proposed_text.contains("schema_version = 2"));
    assert!(preview.proposed_text.contains("source_dirs"));
    let output = tree.root().join("ici-next.toml");
    write_migration(&preview, &output).unwrap();
    assert_eq!(fs::read(&source).unwrap(), before);
    assert!(write_migration(&preview, &output).is_err());
    assert!(write_migration(&preview, &source).is_err());
    tree.write("ici.toml", "# edited\n[ici]\nprofile='fast'\n");
    assert!(
        write_migration(&preview, &tree.root().join("other.toml"))
            .unwrap_err()
            .message
            .contains("changed after")
    );
}

#[test]
fn explain_reports_file_line_and_default_or_discovered_origin() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write(
        "ici.toml",
        "# first line\nschema_version=2\nprofile='deep'\n",
    );
    let project = tree.resolve();
    let explanation = explain(&project);
    let profile = explanation.iter().find(|v| v.key == "profile").unwrap();
    assert_eq!(profile.origin.line, 3);
    assert!(profile.origin.file.ends_with("ici.toml"));
    let threshold = explanation
        .iter()
        .find(|v| v.key == "engines.line.fail_limit")
        .unwrap();
    assert_eq!(threshold.origin.layer, "defaults");
    assert!(
        explanation
            .iter()
            .any(|v| v.key.starts_with("resolved_components.") && v.origin.layer == "discovered")
    );
}

#[test]
fn duplicate_ids_and_dependencies_are_rejected() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write(
        "ici.toml",
        "schema_version=2\n[[components]]\nid='a'\nroot='.'\n[[components]]\nid='a'\nroot='.'\n",
    );
    assert!(tree.failure().message.contains("duplicate"));
    tree.write("ici.toml","schema_version=2\n[[components]]\nid='a'\nroot='.'\nneeds=['b']\n[[components]]\nid='b'\nroot='.'\nneeds=['a']\n");
    assert!(tree.failure().message.contains("cycle"));
}

#[test]
fn global_preferences_do_not_weaken_or_override_project_intent() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write(
        "ici.toml",
        "schema_version=2\n[tools.ruff]\npath='project-ruff'\n",
    );
    tree.write(
        "user/ici/preferences.toml",
        "[tools.ruff]\npath='user-ruff'\n",
    );
    let env = BTreeMap::from([(
        "XDG_CONFIG_HOME".into(),
        tree.root().join("user").display().to_string(),
    )]);
    let project = resolve_with_environment(tree.root(), None, None, &env).unwrap();
    assert_eq!(project.tools["ruff"].path.as_deref(), Some("project-ruff"));
    tree.write(
        "user/ici/preferences.toml",
        "[engines.line]\nenabled=false\n",
    );
    assert!(
        resolve_with_environment(tree.root(), None, None, &env)
            .unwrap_err()
            .message
            .contains("never inspection policy")
    );
}

#[cfg(unix)]
#[test]
fn source_symlink_is_not_followed_and_is_reported() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    let outside = TempDir::new().unwrap();
    fs::write(outside.path().join("other.py"), "x=1").unwrap();
    std::os::unix::fs::symlink(outside.path(), tree.root().join("external-link")).unwrap();
    let project = tree.resolve();
    assert_eq!(project.components[0].files, vec!["main.py"]);
    assert!(
        project
            .limitations
            .iter()
            .any(|v| v.contains("symlink not traversed"))
    );
}

#[test]
fn schema_paths_preserve_all_frozen_allowed_key_declarations() {
    let inventory: Value = serde_json::from_str(include_str!(
        "../../../docs/design/rust-replacement/inventory.json"
    ))
    .unwrap();
    for family in ["legacy", "next-v1", "next-local-overlay"] {
        let extracted: Vec<_> = inventory["requirements"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|r| r["kind"] == "config_key" && r["facts"]["schema"] == family)
            .map(|r| r["facts"]["key"].as_str().unwrap())
            .collect();
        let actual = match family {
            "legacy" => LEGACY_KEYS,
            "next-v1" => NEXT_KEYS,
            _ => LOCAL_KEYS,
        };
        let mut expected: std::collections::BTreeSet<_> = extracted.into_iter().collect();
        if family == "next-v1" {
            expected.extend([
                "components[].integrations[].inherit_env",
                "builds.*.test_argv",
                "builds.*.coverage_test_argv",
                "builds.*.sanitize_test_argv",
                "builds.*.thread_sanitize_test_argv",
            ]);
        }
        let actual_keys: std::collections::BTreeSet<_> = actual.iter().copied().collect();
        assert_eq!(actual.len(), actual_keys.len(), "duplicate {family} keys");
        assert_eq!(
            expected, actual_keys,
            "frozen keys plus explicit native additions: {family}"
        );
    }
}

#[test]
fn fixed_fixture_contracts_and_former_init_exclude_bug() {
    let tree = Project::new();
    tree.write("main.py", "x=1");
    tree.write("target/dependency.py", "x=1");
    tree.write(".venv/dependency.py", "x=1");
    let original = tree.resolve();
    tree.write(
        "ici.toml",
        include_str!("../../../tests/rust-parity/config/v1-init-no-excludes.toml"),
    );
    let fixed = tree.resolve();
    assert_eq!(fixed.components[0].files, original.components[0].files);
    for fixture in [
        include_str!("../../../tests/rust-parity/config/sparse.toml"),
        include_str!("../../../tests/rust-parity/config/legacy.toml"),
        include_str!("../../../tests/rust-parity/config/v1-root-full.toml"),
    ] {
        parse_document(Path::new("fixture.toml"), fixture, false).unwrap();
    }
    assert!(
        parse_document(
            Path::new("future.toml"),
            include_str!("../../../tests/rust-parity/config/future.toml"),
            false
        )
        .is_err()
    );
}

#[test]
fn declared_execution_settings_remain_shell_free_and_bounded() {
    for invalid in [
        "[build.make]\nenabled=true\nbuild_argv=['bash','-c','touch forbidden']\n",
        "[engines.integration]\ncases=[{name='bad',argv=['python','test.py']}]\n",
        "[engines.integration]\ncases=[{name='bad',argv=['{python:py}','-c','import os']}]\n",
    ] {
        assert!(parse_document(Path::new("invalid.toml"), invalid, false).is_err());
    }
    let valid = "[engines.integration]\npython_targets={py='project-python'}\ncases=[{name='safe',argv=['{python:py}','test.py'],output_artifacts=[{path='result.txt',min_size=1}]}]\n";
    assert!(parse_document(Path::new("valid.toml"), valid, false).is_ok());
}

#[test]
fn legacy_migration_preserves_nested_package_scope_language_and_runtime() {
    let tree = Project::new();
    tree.write("main.py", "outside=1");
    tree.write("src/main.py", "inside=1");
    tree.write("src/other.rs", "pub fn unrelated() {}");
    tree.write(
        "src/nested/pyproject.toml",
        "[project]\nname='nested'\nversion='1'\n",
    );
    tree.write("src/nested/code.py", "nested=1");
    let source=tree.write("ici.toml", "# preserve\n[project]\nsource_dirs=['src']\ntype='python'\n[engines.test]\npython='project-python'\n");
    let before = tree.resolve();
    let bytes = fs::read(&source).unwrap();
    let preview = migrate_preview(&source).unwrap();
    let output = tree.root().join("migrated.toml");
    write_migration(&preview, &output).unwrap();
    let after =
        resolve_with_environment(tree.root(), Some(&output), None, &BTreeMap::new()).unwrap();
    assert_eq!(scope(&before), scope(&after));
    assert_eq!(
        before.components[0].python_executable,
        after.components[0].python_executable
    );
    assert_eq!(fs::read(source).unwrap(), bytes);
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/config/legacy-migration-scope.json"
    ))
    .unwrap();
    assert_eq!(json!(before.components[0].files), oracle["files"]);
    assert_eq!(json!(after.components[0].files), oracle["files"]);
}

#[test]
fn global_source_dirs_are_workspace_relative_across_discovered_packages() {
    let tree = Project::new();
    tree.write("outside.py", "outside=1");
    tree.write(
        "src/nested/pyproject.toml",
        "[project]\nname='nested'\nversion='1'\n",
    );
    tree.write("src/nested/code.py", "nested=1");
    tree.write("ici.toml", "schema_version=2\nsource_dirs=['src']\n");
    let project = tree.resolve();
    assert_eq!(project.components.len(), 1);
    assert_eq!(project.components[0].files, vec!["src/nested/code.py"]);
}

#[test]
fn type_provider_search_is_shared_case_sensitive_and_workspace_bounded() {
    let tree = Project::new();
    tree.write("nested/component/main.py", "x=1");
    tree.write("ici.toml","schema_version=2\n[[components]]\nid='nested'\nroot='nested/component'\nlanguages=['python']\n");
    tree.write("pyproject.toml", "[tool.mypy]\nstrict=true\n");
    assert_eq!(
        tree.resolve().components[0].python_type_provider.as_deref(),
        Some("mypy")
    );
    tree.write(
        "nested/component/ty.toml",
        "[environment]\npython-version='3.10'\n",
    );
    assert!(tree.failure().message.contains("both native mypy and ty"));
    assert_eq!(
        discover_type_provider(
            &tree.root().join("nested/component"),
            tree.root(),
            Some("ty")
        )
        .unwrap()
        .as_deref(),
        Some("ty")
    );
    fs::remove_file(tree.root().join("pyproject.toml")).unwrap();
    fs::remove_file(tree.root().join("nested/component/ty.toml")).unwrap();
    tree.write(".mypy.ini", "[mypy]\nstrict=True\n");
    assert_eq!(
        tree.resolve().components[0].python_type_provider.as_deref(),
        Some("mypy")
    );
    fs::remove_file(tree.root().join(".mypy.ini")).unwrap();
    tree.write("setup.cfg", "[MYPY]\nstrict=True\n");
    assert_eq!(tree.resolve().components[0].python_type_provider, None);
    tree.write(
        "setup.cfg",
        "[mypy] # native section names are case-sensitive\nstrict=True\n",
    );
    assert_eq!(
        tree.resolve().components[0].python_type_provider.as_deref(),
        Some("mypy")
    );
    let separate = Project::new();
    separate.write("main.py", "x=1");
    assert_eq!(
        discover_type_provider(separate.root(), separate.root(), None).unwrap(),
        None
    );
    assert!(discover_type_provider(separate.root(), tree.root(), None).is_err());
}

#[test]
fn explicit_interpreter_preserves_virtual_environment_and_multicall_invocation_spelling() {
    let root = tempfile::tempdir().unwrap();
    let runtime = root.path().join("pkg/.venv/bin");
    fs::create_dir_all(&runtime).unwrap();
    std::os::unix::fs::symlink("/bin/true", runtime.join("python")).unwrap();
    fs::write(root.path().join("pkg/main.py"), "x=1\n").unwrap();
    fs::write(root.path().join("ici.toml"),"schema_version=2\n[[components]]\nid='pkg'\nroot='pkg'\nlanguages=['python']\n[components.python]\nexecutable='pkg/.venv/bin/python'\n").unwrap();
    let project = resolve_with_environment(root.path(), None, None, &BTreeMap::new()).unwrap();
    assert_eq!(
        project.components[0].python_executable.as_deref(),
        Some("pkg/.venv/bin/python")
    );
}
