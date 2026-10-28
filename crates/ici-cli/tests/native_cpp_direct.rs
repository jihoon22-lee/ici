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

fn bind_direct_checks(
    project: &ici_config::ResolvedProject,
    environment: &BTreeMap<String, String>,
    names: &[&str],
    label: &str,
) -> Vec<ici_validation::ValidationPlan> {
    let request = ici_application::request::RunRequest {
        checks: names.iter().map(|name| name.to_string()).collect(),
        ..Default::default()
    };
    let mut plans = vec![];
    for name in names {
        let check = ici_domain::builtin_catalog()
            .checks
            .into_iter()
            .find(|check| check.id == *name)
            .unwrap();
        let bound = ici_cli::cpp_validation::bind(
            project,
            &project.components[0],
            &check,
            &format!("app.{name}"),
            &request,
            environment,
            label,
        )
        .unwrap();
        assert!(
            matches!(bound.work, ici_application::plan::Work::Steps { .. }),
            "{}: {:?}",
            name,
            bound.work
        );
        plans.extend(bound.plans);
    }
    plans
}
fn execute_direct_checks(
    mut plans: Vec<ici_validation::ValidationPlan>,
    environment: &BTreeMap<String, String>,
) -> Vec<ici_domain::AnalysisOutput> {
    use ici_validation::{CapturedArtifacts, OwnedRun};
    let sources = plans
        .iter()
        .flat_map(|plan| plan.context.source_files.iter())
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .map(|path| ici_domain::SourceInput {
            path: path.clone(),
            bytes: fs::read(plans[0].context.root.join(path)).unwrap(),
        })
        .collect::<Vec<_>>();
    let coordinates = ici_providers::source_coordinates(&sources);
    for plan in &mut plans {
        ici_validation::attach_sources(plan, coordinates.clone());
    }
    let directory = plans[0].context.owned_run_dir.clone();
    fs::create_dir_all(directory.parent().unwrap()).unwrap();
    let owner = OwnedRun::create(&directory).unwrap();
    let mut outcomes = BTreeMap::new();
    loop {
        let mut progressed = false;
        for plan in &plans {
            for id in ici_validation::ready_steps(plan, &outcomes).unwrap() {
                let task = plan.tasks.iter().find(|task| task.id == id).unwrap();
                owner.mark_started(plan, &id);
                outcomes.insert(
                    id,
                    Runner::with_environment(environment.clone())
                        .run(task, &Cancellation::default()),
                );
                progressed = true;
            }
        }
        if !progressed {
            break;
        }
    }
    plans
        .iter()
        .map(|plan| {
            let captured = if plan.artifact_patterns.is_empty() {
                CapturedArtifacts::default()
            } else {
                ici_validation::capture_artifacts(plan, &owner, &outcomes).unwrap()
            };
            ici_validation::observe(plan, &outcomes, &captured)
        })
        .collect()
}
#[test]
#[ignore = "explicit native multi-executable direct variants and collective GCOV gate"]
fn actual_direct_tests_use_separate_executables_shared_production_and_collective_coverage() {
    let (root, mut environment) = fixture();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(root.path().join("fixture.txt"), "fixture").unwrap();
    fs::write(root.path().join("tests/green.cpp"),"#include <fstream>\nint answer(); int main(){std::ifstream fixture(\"fixture.txt\");return fixture.good() && answer()==11?0:1;}\n").unwrap();
    fs::write(
        root.path().join("tests/red.cpp"),
        "int answer(); int main(){return answer()==11?7:1;}\n",
    )
    .unwrap();
    let config = root.path().join("ici.toml");
    let mut text = fs::read_to_string(&config).unwrap();
    text.push_str(&format!(
        "\n[tools.gcov]\npath={}\n[tools.readelf]\npath={}\n",
        serde_json::to_string(&std::env::var("ICI_TEST_GCOV").expect("explicit gcov")).unwrap(),
        serde_json::to_string(&std::env::var("ICI_TEST_READELF").expect("explicit readelf"))
            .unwrap()
    ));
    fs::write(config, text).unwrap();
    environment.insert("PATH".into(), "/usr/bin:/bin".into());
    let project = resolve(root.path(), &environment);
    let original = fs::read(root.path().join("src/main.cpp")).unwrap();
    for variant in ["test", "coverage"] {
        let mut component = project.components[0].clone();
        component.settings["native_requested_variant"] = serde_json::json!(variant);
        let plan =
            export_context::plan_legacy_direct_preparation(&project, &component, &environment)
                .unwrap();
        assert!(!plan.selected_sources.contains(&"src/main.cpp".into()));
        assert!(
            !plan
                .selected_sources
                .contains(&"src/external/widget.cpp".into())
        );
        assert_eq!(
            plan.build
                .tasks
                .iter()
                .filter(|task| task.id.contains(":compile"))
                .count(),
            3
        );
        assert_eq!(
            plan.build
                .tasks
                .iter()
                .filter(|task| task.id.contains(":link"))
                .count(),
            2
        );
        let prepared = export_context::execute_preparation_typed(
            &plan,
            &scheduler(root.path(), &environment),
            &Cancellation::default(),
            &EventLog::default(),
        )
        .unwrap();
        assert_eq!(prepared.test_executables.len(), 2);
        let contexts = vec![prepared];
        let overlay = export_context::overlay(&project, &contexts);
        let checks = if variant == "coverage" {
            vec!["cpp.test", "cpp.coverage"]
        } else {
            vec!["cpp.test"]
        };
        let plans = bind_direct_checks(&overlay, &environment, &checks, variant);
        if variant == "coverage" {
            let coverage = plans
                .iter()
                .find(|plan| plan.kind == ici_validation::ValidationKind::Gcov)
                .unwrap();
            assert_eq!(coverage.context.options.shared_cpp_test_sources.len(), 2);
            assert_eq!(coverage.context.source_files, ["src/value.cpp"]);
        }
        let outputs = execute_direct_checks(plans, &environment);
        for output in &outputs {
            assert_eq!(
                output.observation.state,
                TaskState::Succeeded,
                "{variant}: {output:?}"
            );
        }
        let tests = outputs
            .iter()
            .filter(|output| output.observation.provider == "cpp-executable")
            .collect::<Vec<_>>();
        assert_eq!(tests.len(), 2);
        assert_eq!(
            tests
                .iter()
                .map(|output| output.extra["passed_tests"].as_u64().unwrap())
                .sum::<u64>(),
            1
        );
        assert!(
            tests
                .iter()
                .flat_map(|output| &output.observation.findings)
                .any(|finding| finding.primary_location.path == "tests/red.cpp")
        );
        if variant == "coverage" {
            let coverage = outputs
                .iter()
                .find(|output| output.observation.provider == "gcov")
                .unwrap();
            assert_eq!(coverage.extra["passed_tests"], 1);
            assert_eq!(coverage.extra["total_tests"], 2);
            assert_eq!(
                coverage.extra["physical_test_suites"]
                    .as_array()
                    .unwrap()
                    .len(),
                2
            );
            assert_eq!(
                coverage.extra["coverage_files"].as_array().unwrap().len(),
                1
            );
            assert_eq!(coverage.extra["coverage_files"][0]["file"], "src/value.cpp");
            assert_eq!(coverage.extra["coverage_totals"]["cover"], 100.0);
        }
    }
    assert_eq!(
        fs::read(root.path().join("src/main.cpp")).unwrap(),
        original
    );
    assert!(!root.path().join("build").exists());
    assert!(!root.path().join("compile_commands.json").exists());
}

#[test]
#[ignore = "explicit direct ASan/UBSan and TSan instrumented variants gate"]
fn actual_direct_sanitizer_variants_keep_instrumentation_and_runtime_limits_distinct() {
    let (root, environment) = fixture();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(root.path().join("tests/runtime.cpp"),"#include <limits>\nint answer();int main(){volatile int n=std::numeric_limits<int>::max();int overflow=n+1;return answer()==11 && overflow<0 ? 0 : 1;}\n").unwrap();
    let config = root.path().join("ici.toml");
    let mut text = fs::read_to_string(&config).unwrap();
    text.push_str(&format!(
        "\n[tools.readelf]\npath={}\n",
        serde_json::to_string(&std::env::var("ICI_TEST_READELF").expect("explicit readelf"))
            .unwrap()
    ));
    fs::write(config, text).unwrap();
    let project = resolve(root.path(), &environment);
    for (variant, check) in [
        ("sanitize", "cpp.sanitize"),
        ("thread-sanitize", "cpp.tsan"),
    ] {
        let mut component = project.components[0].clone();
        component.settings["native_requested_variant"] = serde_json::json!(variant);
        let plan =
            export_context::plan_legacy_direct_preparation(&project, &component, &environment)
                .unwrap();
        assert!(
            plan.build
                .tasks
                .iter()
                .filter(|task| task.id.contains(":compile"))
                .all(|task| task.argv.iter().any(|argument| argument
                    == if variant == "sanitize" {
                        "-fsanitize=address,undefined"
                    } else {
                        "-fsanitize=thread"
                    }))
        );
        let prepared = export_context::execute_preparation_typed(
            &plan,
            &scheduler(root.path(), &environment),
            &Cancellation::default(),
            &EventLog::default(),
        )
        .unwrap();
        assert_eq!(prepared.test_executables.len(), 1);
        let contexts = vec![prepared];
        let overlay = export_context::overlay(&project, &contexts);
        let plans = bind_direct_checks(&overlay, &environment, &[check], variant);
        assert!(
            plans[0]
                .tasks
                .iter()
                .any(|task| task.id.ends_with(".instrumentation"))
        );
        let outputs = execute_direct_checks(plans, &environment);
        assert_eq!(outputs.len(), 1);
        let output = &outputs[0];
        eprintln!(
            "{variant} observed state={:?}; findings={}; limitations={:?}",
            output.observation.state,
            output.observation.findings.len(),
            output.observation.limitations
        );
        if variant == "sanitize" {
            assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
            assert!(
                output
                    .observation
                    .findings
                    .iter()
                    .any(|finding| finding.rule_id == "ici.sanitize.ubsan.signed-integer-overflow"),
                "{output:?}"
            );
            assert_eq!(
                output.observation.findings[0].primary_location.path,
                "tests/runtime.cpp"
            );
        } else if output.observation.state != TaskState::Succeeded {
            assert!(
                output
                    .observation
                    .limitations
                    .iter()
                    .any(|reason| reason.contains("runtime failed")
                        || reason.contains("did not complete")),
                "{output:?}"
            );
            assert!(
                output.observation.findings.is_empty(),
                "a runtime failure must not fabricate a code violation: {output:?}"
            );
        } else {
            assert!(output.observation.findings.is_empty(), "{output:?}");
        }
    }
}

#[test]
#[ignore = "explicit legacy ici test common-application acceptance"]
fn actual_legacy_test_command_runs_owned_plain_suites_without_a_build_descriptor() {
    let (root, environment) = fixture();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(
        root.path().join("tests/green.cpp"),
        "int answer();int main(){return answer()==11?0:1;}\n",
    )
    .unwrap();
    fs::write(
        root.path().join("tests/red.cpp"),
        "int answer();int main(){return answer()==11?7:1;}\n",
    )
    .unwrap();
    let config = root.path().join("ici.toml");
    let mut text = fs::read_to_string(&config).unwrap();
    text.push_str(&format!(
        "\n[tools.gcov]\npath={}\n",
        serde_json::to_string(&std::env::var("ICI_TEST_GCOV").expect("explicit gcov")).unwrap()
    ));
    fs::write(config, text).unwrap();
    let invocation = ici_cli::registry::parse(["ici", "test"]).unwrap();
    let planned =
        ici_cli::planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert!(
        !planned.context_preparations.is_empty(),
        "{:?}",
        planned.plan.limitations
    );
    assert!(
        planned
            .context_preparations
            .iter()
            .all(|plan| plan.direct.is_some())
    );
    assert!(!root.path().join(".ici").exists());
    let result = ici_cli::execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    let outputs = result
        .outputs
        .iter()
        .filter(|(id, _)| id.contains("cpp.test") || id.contains("cpp.coverage"))
        .collect::<Vec<_>>();
    assert!(!outputs.is_empty(), "{:?}", result.outputs);
    for (_, output) in &outputs {
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{output:?}; limitations={:?}",
            result.result.limitations
        );
    }
    assert!(
        outputs
            .iter()
            .flat_map(|(_, output)| &output.observation.findings)
            .any(|finding| finding.primary_location.path == "tests/red.cpp")
    );
    assert!(
        !result
            .result
            .limitations
            .iter()
            .any(|limitation| limitation.contains("changed during")
                || limitation.contains("changed after")),
        "{:?}",
        result.result.limitations
    );
    assert!(!root.path().join("build").exists());
    assert!(!root.path().join("compile_commands.json").exists());
}
