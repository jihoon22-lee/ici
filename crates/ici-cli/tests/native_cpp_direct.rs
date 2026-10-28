use ici_application::snapshot::{InputLimits, InputSnapshot};
use ici_cli::{
    artifact_build,
    events::EventLog,
    export_context::{self, PreparationFailure},
};
use ici_domain::TaskState;
use ici_execution::{Cancellation, Runner, Scheduler};
use std::{collections::BTreeMap, fs, os::unix::fs::symlink, path::Path, process::Command};

fn fixture() -> (tempfile::TempDir, BTreeMap<String, String>) {
    let root = tempfile::tempdir().unwrap();
    for directory in ["src/external", "include", "pc/headers"] {
        fs::create_dir_all(root.path().join(directory)).unwrap();
    }
    fs::write(root.path().join("include/local.hpp"), "#define LOCAL 7\n").unwrap();
    fs::write(
        root.path().join("pc/headers/package.hpp"),
        "#define PKG_HEADER 4\n",
    )
    .unwrap();
    fs::write(
        root.path().join("src/main.cpp"),
        "int answer();\nint main(){return answer()==11 ? 0 : 1;}\n",
    )
    .unwrap();
    fs::write(root.path().join("src/value.cpp"),"#include <local.hpp>\n#include <package.hpp>\nstatic_assert(PKG_MACRO==3);\nint answer(){return LOCAL+PKG_HEADER;}\n").unwrap();
    fs::write(
        root.path().join("src/external/widget.cpp"),
        "this requires another native build\n",
    )
    .unwrap();
    fs::write(root.path().join("pc/ici-direct.pc"),"Name: ici-direct\nDescription: Direct compiler fixture\nVersion: 1\nCflags: -DPKG_MACRO=3 -I${pcfiledir}/headers\n").unwrap();
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit native compiler");
    let pkg = std::env::var("ICI_TEST_PKG_CONFIG").expect("explicit pkg-config");
    fs::write(root.path().join("ici.toml"),format!("schema_version=2\nname='direct-app'\nversion='1.2.3'\n[project]\ncpp_external_build_dirs=['src/external']\ncpp_pkg_config=['ici-direct']\n[tools.'g++']\npath={}\n[tools.pkg-config]\npath={}\n[[components]]\nid='app'\nroot='.'\nlanguages=['cpp']\nsources=['src/**/*.cpp']\nsource_dirs=['src']\n",serde_json::to_string(&compiler).unwrap(),serde_json::to_string(&pkg).unwrap())).unwrap();
    let environment = BTreeMap::from([
        ("PATH".into(), "/usr/bin:/bin".into()),
        (
            "PKG_CONFIG_PATH".into(),
            root.path().join("pc").display().to_string(),
        ),
    ]);
    (root, environment)
}
fn resolve(root: &Path, environment: &BTreeMap<String, String>) -> ici_config::ResolvedProject {
    ici_config::resolve_with_environment(root, None, None, environment).unwrap()
}
fn scheduler(root: &Path, environment: &BTreeMap<String, String>) -> Scheduler {
    let mut scheduler = Scheduler::new(Runner::with_environment(environment.clone()), 2);
    scheduler.lock_directory = Some(root.join(".ici/rust-v1/locks/tasks"));
    scheduler
}

#[test]
#[ignore = "explicit native direct GCC and common release packaging acceptance"]
fn actual_direct_build_records_only_observed_commands_and_publishes_through_common_writer() {
    let (root, environment) = fixture();
    let project = resolve(root.path(), &environment);
    let component = &project.components[0];
    let before = fs::read(root.path().join("src/main.cpp")).unwrap();
    let plan =
        export_context::plan_legacy_direct_preparation(&project, component, &environment).unwrap();
    assert!(!root.path().join(".ici").exists());
    assert_eq!(plan.build.system, "direct-gcc");
    assert_eq!(plan.selected_sources, ["src/main.cpp", "src/value.cpp"]);
    assert_eq!(
        plan.build
            .tasks
            .iter()
            .filter(|task| task.id.contains(":compile"))
            .count(),
        2
    );
    assert!(
        plan.build
            .tasks
            .iter()
            .any(|task| task.provider == "pkg-config")
    );
    assert!(
        export_context::plan_preparation(
            &project,
            component,
            &environment,
            &std::env::current_exe().unwrap()
        )
        .is_err(),
        "standalone export preparation still requires a native descriptor"
    );
    let cancellation = Cancellation::default();
    let prepared = export_context::execute_preparation_typed(
        &plan,
        &scheduler(root.path(), &environment),
        &cancellation,
        &EventLog::default(),
    )
    .unwrap();
    assert!(
        prepared.context.complete(),
        "{:?}",
        prepared.context.diagnostics
    );
    assert_eq!(prepared.context.units.len(), 2);
    assert!(
        prepared
            .context
            .units
            .iter()
            .all(|unit| unit.argv.iter().any(|argument| argument == "-DPKG_MACRO=3"))
    );
    assert_eq!(prepared.artifacts.len(), 1);
    let binary = root.path().join(&prepared.artifacts[0]);
    assert!(binary.ends_with("bin/direct-app"));
    assert!(Command::new(&binary).status().unwrap().success());
    let evidence = prepared.evidence.as_ref().unwrap();
    let database: serde_json::Value = serde_json::from_slice(
        &fs::read(root.path().join(&plan.build.compilation_database)).unwrap(),
    )
    .unwrap();
    for index in 0..2 {
        assert_eq!(
            database[index]["arguments"],
            evidence["compiler_tasks"][index]["argv"]
        );
        assert!(
            database[index]["arguments"]
                .as_array()
                .unwrap()
                .iter()
                .all(|argument| argument != "{observed-pkg-config-cflags}"
                    && argument != "-fsyntax-only")
        );
    }
    let contexts = vec![prepared];
    let overlay = export_context::overlay(&project, &contexts);
    let sources =
        export_context::compiled_artifacts(&overlay, &overlay.components[0], &contexts).unwrap();
    let release = artifact_build::describe_compiled(
        &overlay,
        &overlay.components[0],
        "app.cpp.artifact",
        &environment,
        &sources,
    )
    .unwrap();
    let paths = release
        .task
        .input_refs
        .iter()
        .map(|path| {
            Path::new(path)
                .strip_prefix(root.path())
                .unwrap()
                .display()
                .to_string()
        })
        .collect::<Vec<_>>();
    let snapshot = InputSnapshot::take(
        root.path(),
        &paths,
        &[],
        InputLimits {
            file_bytes: 256 * 1024 * 1024,
            total_bytes: 512 * 1024 * 1024,
        },
    )
    .unwrap();
    let output = artifact_build::execute(&release, &snapshot, &cancellation);
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    let published = root
        .path()
        .join(&release.output_root)
        .join("bin/direct-app");
    assert!(Command::new(&published).status().unwrap().success());
    assert_eq!(fs::read(root.path().join("src/main.cpp")).unwrap(), before);
    assert!(!root.path().join("main.o").exists());
    assert!(!root.path().join("compile_commands.json").exists());
}

#[test]
#[ignore = "explicit native direct GCC compile/link failure acceptance"]
fn actual_direct_compiler_failures_keep_located_code_evidence_and_refuse_output_redirects() {
    let (root, environment) = fixture();
    let project = resolve(root.path(), &environment);
    fs::write(
        root.path().join("src/value.cpp"),
        "int answer(){return unknown_value;}\n",
    )
    .unwrap();
    let plan = export_context::plan_legacy_direct_preparation(
        &project,
        &project.components[0],
        &environment,
    )
    .unwrap();
    let failure = export_context::execute_preparation_typed(
        &plan,
        &scheduler(root.path(), &environment),
        &Cancellation::default(),
        &EventLog::default(),
    )
    .err()
    .expect("compiler failure");
    assert!(matches!(&failure,PreparationFailure::Observed{phase,..} if phase=="compile"));
    let output = failure.observed_for("app.cpp.artifact").unwrap();
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert_eq!(output.status, "FAIL");
    assert!(
        output
            .observation
            .findings
            .iter()
            .any(|finding| finding.primary_location.path == "src/value.cpp"
                && finding.primary_location.start_line == 1)
    );
    assert!(
        !output.extra["finding_details"]
            .as_object()
            .unwrap()
            .is_empty()
    );
    assert!(
        output
            .observation
            .findings
            .iter()
            .all(|finding| finding.task_id.as_deref() == Some("app.cpp.artifact"))
    );
    assert!(!root.path().join(&plan.build.compilation_database).exists());
    fs::write(
        root.path().join("src/main.cpp"),
        "int missing();\nint main(){return missing();}\n",
    )
    .unwrap();
    fs::write(
        root.path().join("src/value.cpp"),
        "int answer(){return 1;}\n",
    )
    .unwrap();
    let plan = export_context::plan_legacy_direct_preparation(
        &project,
        &project.components[0],
        &environment,
    )
    .unwrap();
    let failure = export_context::execute_preparation_typed(
        &plan,
        &scheduler(root.path(), &environment),
        &Cancellation::default(),
        &EventLog::default(),
    )
    .err()
    .expect("link failure");
    assert!(matches!(&failure,PreparationFailure::Observed{phase,..} if phase=="link"));
    let output = failure.observed_for("app.cpp.artifact").unwrap();
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert!(!output.observation.findings.is_empty());
    assert!(!root.path().join(&plan.build.compilation_database).exists());
    let output_path = plan
        .build
        .tasks
        .iter()
        .find(|task| task.id.contains(":compile"))
        .unwrap()
        .output_specs[0]
        .clone();
    fs::remove_file(&output_path).unwrap();
    fs::write(root.path().join("user-data"), b"preserve").unwrap();
    symlink(root.path().join("user-data"), &output_path).unwrap();
    let failure = export_context::execute_preparation_typed(
        &plan,
        &scheduler(root.path(), &environment),
        &Cancellation::default(),
        &EventLog::default(),
    )
    .err()
    .expect("redirect refusal");
    assert!(matches!(failure, PreparationFailure::Incomplete(_)));
    assert_eq!(
        fs::read(root.path().join("user-data")).unwrap(),
        b"preserve"
    );
}

#[test]
#[ignore = "explicit native direct GCC main guard and descriptor refusal"]
fn direct_application_guard_refuses_descriptors_multiple_mains_and_changed_source_before_mutation()
{
    let (root, environment) = fixture();
    let project = resolve(root.path(), &environment);
    let plan = export_context::plan_legacy_direct_preparation(
        &project,
        &project.components[0],
        &environment,
    )
    .unwrap();
    fs::write(root.path().join("src/main.cpp"), "int main(){return 0;}\n").unwrap();
    let error = export_context::execute_preparation_typed(
        &plan,
        &scheduler(root.path(), &environment),
        &Cancellation::default(),
        &EventLog::default(),
    )
    .err()
    .expect("source drift");
    assert!(matches!(error, PreparationFailure::Incomplete(_)));
    assert!(!root.path().join(".ici").exists());
    fs::write(root.path().join("src/value.cpp"), "int main(){return 1;}\n").unwrap();
    assert!(
        export_context::plan_legacy_direct_preparation(
            &project,
            &project.components[0],
            &environment
        )
        .unwrap_err()
        .contains("found 2")
    );
    fs::write(root.path().join("Makefile"), "all:\n\tfalse\n").unwrap();
    assert!(
        export_context::plan_legacy_direct_preparation(
            &project,
            &project.components[0],
            &environment
        )
        .unwrap_err()
        .contains("descriptor")
    );
    assert!(!root.path().join(".ici").exists());
}
