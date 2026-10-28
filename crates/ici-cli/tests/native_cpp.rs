//! Explicit native compiler gate. Invoke --ignored with ICI_TEST_CXX.
use ici_cli::{execute, planning, registry};
use ici_domain::{GateVerdict, TaskState};
use ici_execution::{Cancellation, storage};
use std::{collections::BTreeMap, fs};

fn fixture() -> tempfile::TempDir {
    let directory = tempfile::tempdir().unwrap();
    fs::write(directory.path().join("main.cpp"),"#ifndef MODE\n#define MODE 0\n#endif\n#if MODE\nint choose() { return \"bad\"; }\n#else\nint choose() { return 1; }\n#endif\n").unwrap();
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit native ICI_TEST_CXX");
    let rows:Vec<_>=(0..2).map(|mode|serde_json::json!({"directory":directory.path(),"file":directory.path().join("main.cpp"),"arguments":[compiler,"-std=c++17",format!("-DMODE={mode}"),"-c",directory.path().join("main.cpp"),"-o",format!("mode{mode}.o")]})).collect();
    fs::write(
        directory.path().join("compile_commands.json"),
        serde_json::to_vec(&rows).unwrap(),
    )
    .unwrap();
    let mut config = "schema_version=2\n".to_string();
    for check in ici_domain::builtin_catalog()
        .checks
        .iter()
        .filter(|check| check.language == "cpp")
    {
        config.push_str(&format!(
            "[checks.\"{}\"]\nenabled={}\n",
            check.id,
            matches!(
                check.id.as_str(),
                "cpp.line" | "cpp.compile" | "cpp.diagnostics"
            )
        ));
    }
    fs::write(directory.path().join("ici.toml"), config).unwrap();
    directory
}
#[test]
#[ignore = "explicit native C++ compiler diagnostic/configuration acceptance"]
fn actual_recorded_variants_keep_diagnostic_unit_ownership_without_python() {
    let directory = fixture();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    let invocation = registry::parse(["ici", "analyze", "--events", "events.jsonl"]).unwrap();
    let preview = planning::prepare(&invocation, directory.path(), &environment, true).unwrap();
    assert!(!directory.path().join(".ici").exists());
    assert!(
        preview
            .cpp
            .diagnostics
            .contains_key("project.cpp.diagnostics"),
        "{}\n{:?}",
        preview.text(),
        preview.cpp.contexts
    );
    assert_eq!(preview.cpp.diagnostics["project.cpp.diagnostics"].len(), 2);
    assert!(
        preview.cpp.diagnostics["project.cpp.diagnostics"]
            .iter()
            .all(|provider| provider
                .task
                .argv
                .iter()
                .any(|argument| argument == "-fsyntax-only"))
    );
    let bad = preview.cpp.diagnostics["project.cpp.diagnostics"]
        .iter()
        .find(|provider| {
            provider
                .task
                .argv
                .iter()
                .any(|argument| argument == "-DMODE=1")
        })
        .unwrap()
        .context
        .analysis_unit_ids[0]
        .clone();
    let run = execute::execute(
        &invocation,
        directory.path(),
        &environment,
        true,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        run.result.gate.selected,
        GateVerdict::Fail,
        "{:?}",
        run.outputs
    );
    assert_eq!(
        run.outputs["project.cpp.compile"].observation.state,
        TaskState::Succeeded
    );
    assert_eq!(run.result.findings.len(), 1, "{:?}", run.result.findings);
    let finding = &run.result.findings[0];
    assert_eq!(finding.primary_location.path, "main.cpp");
    assert_eq!(finding.primary_location.start_line, 5);
    assert_eq!(finding.task_id.as_deref(), Some("project.cpp.diagnostics"));
    assert_eq!(finding.analysis_unit_id.as_deref(), Some(bad.as_str()));
    assert!(
        run.result
            .checks
            .as_ref()
            .unwrap()
            .iter()
            .find(|check| check.check_id == "cpp.diagnostics")
            .unwrap()
            .analysis_unit_id
            .is_none()
    );
    let events = storage::read_event_stream(&directory.path().join("events.jsonl"))
        .unwrap()
        .0;
    assert_eq!(
        events
            .iter()
            .filter(|event| event.event_type == "task.started"
                && event
                    .task_id
                    .as_deref()
                    .is_some_and(|id| id.starts_with("project.cpp.diagnostics.unit")))
            .count(),
        2
    );
    let moved = fixture();
    let relocated = execute::execute(
        &invocation,
        moved.path(),
        &environment,
        true,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        run.result.findings[0].analysis_unit_id,
        relocated.result.findings[0].analysis_unit_id
    );
    assert_eq!(
        run.result.findings[0].fingerprint,
        relocated.result.findings[0].fingerprint
    );
}

fn runtime_config(selected: &[&str], extra: &str) -> String {
    let mut config = String::from("schema_version=2\n");
    for check in ici_domain::builtin_catalog()
        .checks
        .iter()
        .filter(|check| check.language == "cpp")
    {
        config.push_str(&format!(
            "[checks.\"{}\"]\nenabled={}\n",
            check.id,
            selected.contains(&check.id.as_str())
        ));
    }
    config.push_str(extra);
    config.push_str("\n[[components]]\nid='project'\nroot='.'\nlanguages=['cpp']\nsources=['main.cpp']\nbuild='native'\n");
    config
}

#[test]
fn missing_declared_runtime_outputs_are_blocked_without_build_or_directory_creation() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(
        root.path().join("ici.toml"),
        runtime_config(
            &["cpp.test", "cpp.coverage", "cpp.binary-compat"],
            "[builds.native]\nsystem='cmake'\ndirectory='build'\nartifacts=['suite']\n",
        ),
    )
    .unwrap();
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let plan = planning::prepare(&invocation, root.path(), &BTreeMap::new(), false).unwrap();
    assert!(!root.path().join("build").exists());
    assert!(!root.path().join(".ici").exists());
    for check in &plan.plan.checks {
        assert!(
            matches!(check.work, ici_application::plan::Work::Blocked { .. }),
            "{}",
            plan.text()
        );
    }
}

#[test]
fn explicitly_disabled_clang_tools_preserve_located_not_applicable_scope_without_a_database() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    let config = runtime_config(
        &["cpp.tidy"],
        "[engines.lint]\nclang_tidy='off'\nclazy='off'\n",
    )
    .replace("build='native'\n", "");
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let invocation = registry::parse(["ici", "analyze"]).unwrap();
    let environment = BTreeMap::new();
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        true,
        &Cancellation::default(),
    )
    .unwrap();
    let output = &run.outputs["project.cpp.tidy"];
    assert_eq!(output.status, "SKIP", "{output:?}");
    assert_eq!(output.targets[0].file_path, "main.cpp");
    assert_eq!(
        output.observation.measurements[0].evidence,
        ici_domain::EvidenceLevel::NotApplicable
    );
}

#[test]
#[ignore = "explicit native CTest/gcov/ELF CLI acceptance"]
fn actual_existing_cmake_artifacts_feed_one_ctest_suite_gcov_and_binary_abi() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("main.cpp"),
        "int main(int argc,char**){if(argc>4)return 1; return 0;}\n",
    )
    .unwrap();
    fs::write(root.path().join("CMakeLists.txt"),"cmake_minimum_required(VERSION 3.16)\nproject(native LANGUAGES CXX)\nenable_testing()\nadd_executable(suite main.cpp)\ntarget_compile_options(suite PRIVATE --coverage -O0)\ntarget_link_options(suite PRIVATE --coverage)\nadd_test(NAME green COMMAND suite)\n").unwrap();
    let cmake = std::env::var("ICI_TEST_CMAKE").expect("explicit CMake");
    for args in [
        vec!["-S", ".", "-B", "build"],
        vec!["--build", "build", "--parallel", "2"],
    ] {
        let output = std::process::Command::new(&cmake)
            .args(args)
            .current_dir(root.path())
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
    let tools = [
        ("ctest", "ICI_TEST_CTEST"),
        ("gcov", "ICI_TEST_GCOV"),
        ("readelf", "ICI_TEST_READELF"),
    ]
    .into_iter()
    .map(|(name, var)| {
        format!(
            "[tools.{name}]\npath={}\n",
            serde_json::to_string(&std::env::var(var).expect(var)).unwrap()
        )
    })
    .collect::<String>();
    fs::write(root.path().join("ici.toml"),runtime_config(&["cpp.test","cpp.coverage","cpp.artifact","cpp.binary-compat"],&format!("{tools}[engines.test]\nmin_line_cov=100.0\nmin_file_statements=1\n[builds.native]\nsystem='cmake'\ndirectory='build'\nartifacts=['suite']\n"))).unwrap();
    let invocation =
        registry::parse(["ici", "next", "verify", "--events", "events.jsonl"]).unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    let preview = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert!(!root.path().join(".ici").exists());
    assert_eq!(
        preview.validations["project.cpp.coverage"].len(),
        1,
        "{}",
        preview.text()
    );
    assert_eq!(
        preview.validations["project.cpp.coverage"][0]
            .context
            .options
            .shared_test_task_id
            .as_deref(),
        Some("project.cpp.test.suite")
    );
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    for id in [
        "project.cpp.test",
        "project.cpp.coverage",
        "project.cpp.binary-compat",
        "project.cpp.artifact",
    ] {
        assert_eq!(
            run.outputs[id].observation.state,
            TaskState::Succeeded,
            "{id}: {:?}",
            run.outputs
        );
    }
    assert!(
        run.outputs["project.cpp.coverage"]
            .targets
            .iter()
            .any(|target| target.file_path == "main.cpp")
    );
    assert!(
        run.outputs["project.cpp.binary-compat"]
            .targets
            .iter()
            .any(|target| target.file_path == "build/suite")
    );
    let events = storage::read_event_stream(&root.path().join("events.jsonl"))
        .unwrap()
        .0;
    assert_eq!(
        events
            .iter()
            .filter(|event| event.event_type == "task.started"
                && event.task_id.as_deref() == Some("project.cpp.test.suite"))
            .count(),
        1
    );
    assert!(
        !events
            .iter()
            .any(|event| event.task_id.as_deref() == Some("project.cpp.coverage.tests.suite"))
    );
    let counters = ici_cli::files::tree(root.path(), &root.path().join("build"))
        .unwrap()
        .into_iter()
        .filter(|entry| entry.relative.ends_with(".gcda"))
        .map(|entry| {
            let path = root.path().join(entry.relative);
            let bytes = fs::read(&path).unwrap();
            (path, bytes)
        })
        .collect::<Vec<_>>();
    assert!(!counters.is_empty());
    let next =
        registry::parse(["ici", "next", "verify", "--events", "events-second.jsonl"]).unwrap();
    let repeated = planning::prepare(&next, root.path(), &environment, false).unwrap();
    assert!(
        !repeated.validations["project.cpp.coverage"][0]
            .context
            .options
            .gcov_preexisting_data
            .is_empty()
    );
    for (path, bytes) in &counters {
        assert_eq!(
            &fs::read(path).unwrap(),
            bytes,
            "read-only plan must preserve prior counters"
        );
    }
    let second = execute::execute(
        &next,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        second.outputs["project.cpp.test"].observation.state,
        TaskState::Succeeded,
        "{:?}",
        second.outputs
    );
    assert_eq!(
        second.outputs["project.cpp.coverage"].observation.state,
        TaskState::Blocked,
        "{:?}",
        second.outputs
    );
    assert!(
        second.outputs["project.cpp.coverage"]
            .observation
            .findings
            .is_empty()
    );
    assert_eq!(second.result.gate.selected, GateVerdict::Incomplete);
}

#[test]
#[ignore = "explicit Clang21 tidy with recorded GCC library/configuration acceptance"]
fn actual_tidy_keeps_each_recorded_configuration_and_observes_selected_gcc_standard_library() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"),"#include <vector>\nint choose(){\n#if MODE\nint *p=nullptr;return *p;\n#else\nstd::vector<int> v; return static_cast<int>(v.size());\n#endif\n}\n").unwrap();
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit GCC");
    let rows:Vec<_>=(0..2).map(|mode|serde_json::json!({"directory":root.path(),"file":root.path().join("main.cpp"),"arguments":[compiler,"-std=c++17",format!("-DMODE={mode}"),"-Werror","-c",root.path().join("main.cpp"),"-o",format!("mode{mode}.o")]})).collect();
    fs::write(
        root.path().join("compile_commands.json"),
        serde_json::to_vec(&rows).unwrap(),
    )
    .unwrap();
    let tidy = std::env::var("ICI_TEST_CLANG_TIDY").expect("explicit clang-tidy21");
    let mut config = runtime_config(
        &["cpp.compile", "cpp.tidy"],
        &format!(
            "[tools.clang-tidy]\npath={}\n[engines.lint]\nclazy='off'\nclang_tidy='required'\n",
            serde_json::to_string(&tidy).unwrap()
        ),
    );
    // This static fixture has a compilation database and intentionally no build contract.
    config = config.replace("build='native'\n", "");
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let mut environment = BTreeMap::from([("PATH".into(), String::new())]);
    if let Ok(paths) = std::env::var("ICI_TEST_CLANG_LIBRARY_PATH") {
        environment.insert("LD_LIBRARY_PATH".into(), paths);
    }
    let invocation = registry::parse(["ici", "analyze", "--events", "events.jsonl"]).unwrap();
    let preview = planning::prepare(&invocation, root.path(), &environment, true).unwrap();
    assert!(!root.path().join(".ici").exists());
    assert_eq!(
        preview.cpp.diagnostics["project.cpp.tidy"].len(),
        2,
        "{}",
        preview.text()
    );
    assert_eq!(preview.cpp.tooling_searches.len(), 2);
    assert!(
        preview.cpp.diagnostics["project.cpp.tidy"]
            .iter()
            .all(|provider| provider
                .task
                .argv
                .iter()
                .any(|arg| arg == "{observed-gcc-stdlib}")
                && !provider.task.argv.iter().any(|arg| arg == "-Werror"))
    );
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        true,
        &Cancellation::default(),
    )
    .unwrap();
    let output = &run.outputs["project.cpp.tidy"];
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert!(
        output
            .observation
            .findings
            .iter()
            .any(|finding| finding.primary_location.path == "main.cpp"
                && finding.primary_location.start_line == 4
                && finding.analysis_unit_id.is_some()),
        "{output:?}"
    );
    let projections = output.extra["tooling_projections"].as_object().unwrap();
    assert_eq!(projections.len(), 2);
    assert!(projections.values().all(|projection| {
        projection["stdlib"]["gnu_driver"] == true
            && projection["argv"]
                .as_array()
                .unwrap()
                .iter()
                .any(|arg| arg == "-nostdinc++")
            && !projection["argv"]
                .as_array()
                .unwrap()
                .iter()
                .any(|arg| arg == "{observed-gcc-stdlib}")
    }));
    assert!(!root.path().join("mode0.o").exists());
    assert!(!root.path().join("mode1.o").exists());
    fs::write(
        root.path().join(".clang-tidy"),
        "Checks: '-*,clang-analyzer-*'\nExtraArgs: ['-fplugin=forbidden.so']\n",
    )
    .unwrap();
    let unsafe_plan = planning::prepare(&invocation, root.path(), &environment, true).unwrap();
    let tidy = unsafe_plan
        .plan
        .checks
        .iter()
        .find(|check| check.check.id == "cpp.tidy")
        .unwrap();
    assert!(
        matches!(&tidy.work,ici_application::plan::Work::Blocked{reason} if reason.contains("inject compiler arguments")),
        "{}",
        unsafe_plan.text()
    );
}

#[test]
#[ignore = "explicit Clazy1.17 Qt5/GCC contextual CLI acceptance"]
fn actual_clazy_keeps_qt_check_and_recorded_configuration_locations() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("main.cpp"),
        "#include <QByteArray>\nbool empty(){return qgetenv(\"ICI_FIXTURE\").isEmpty();}\n",
    )
    .unwrap();
    let qmake = std::env::var("ICI_TEST_QMAKE").expect("explicit Qt5 qmake");
    let query = std::process::Command::new(qmake)
        .args(["-query", "QT_INSTALL_HEADERS"])
        .output()
        .unwrap();
    assert!(query.status.success());
    let headers = String::from_utf8(query.stdout).unwrap().trim().to_owned();
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit GCC");
    let row = serde_json::json!([{"directory":root.path(),"file":root.path().join("main.cpp"),"arguments":[compiler,"-std=c++17","-fPIC","-DQT_CORE_LIB",format!("-I{headers}"),format!("-I{headers}/QtCore"),"-c",root.path().join("main.cpp"),"-o","main.o"]}]);
    fs::write(
        root.path().join("compile_commands.json"),
        serde_json::to_vec(&row).unwrap(),
    )
    .unwrap();
    let clazy = std::env::var("ICI_TEST_CLAZY").expect("explicit Clazy standalone");
    let config=runtime_config(&["cpp.compile","cpp.tidy"],&format!("[tools.clazy-standalone]\npath={}\n[engines.lint]\nclang_tidy='off'\nclazy='required'\nclazy_checks=['qgetenv']\n",serde_json::to_string(&clazy).unwrap())).replace("build='native'\n","");
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let mut environment = BTreeMap::from([("PATH".into(), String::new())]);
    if let Ok(paths) = std::env::var("ICI_TEST_CLANG_LIBRARY_PATH") {
        environment.insert("LD_LIBRARY_PATH".into(), paths);
    }
    let invocation = registry::parse(["ici", "analyze"]).unwrap();
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        true,
        &Cancellation::default(),
    )
    .unwrap();
    let output = &run.outputs["project.cpp.tidy"];
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert!(
        output
            .observation
            .findings
            .iter()
            .any(|finding| finding.primary_location.path == "main.cpp"
                && finding.primary_location.start_line == 2
                && finding.native_rule_id.contains("qgetenv")),
        "{output:?}"
    );
    assert!(!root.path().join("main.o").exists());
}

#[test]
#[ignore = "explicit Clang21 metrics plus GCC include and unused configuration consensus"]
fn actual_compiler_facets_keep_estimated_scores_exact_geometry_and_all_variant_unused_consensus() {
    for (tool_id, variable) in [
        ("clang-tidy", "ICI_TEST_CLANG_TIDY"),
        ("clang++", "ICI_TEST_CLANG"),
    ] {
        let root = tempfile::tempdir().unwrap();
        fs::write(root.path().join("main.cpp"),"#include <vector>\n#if MODE\n#include \"b.hpp\"\n#else\n#include \"a.hpp\"\n#endif\nstatic int dead(){return 7;}\nstatic int sometimes(){return 1;}\nint main(){\n#if MODE\nreturn 0;\n#else\nreturn sometimes();\n#endif\n}\n").unwrap();
        if tool_id == "clang++" {
            let source = fs::read_to_string(root.path().join("main.cpp")).unwrap();
            fs::write(
                root.path().join("main.cpp"),
                source.replace("#include <vector>", "// bounded direct-source AST fixture"),
            )
            .unwrap();
        }
        fs::write(
            root.path().join("a.hpp"),
            "#pragma once\n#if !MODE\n#include \"b.hpp\"\n#endif\n",
        )
        .unwrap();
        fs::write(
            root.path().join("b.hpp"),
            "#pragma once\n#if MODE\n#include \"a.hpp\"\n#endif\n",
        )
        .unwrap();
        let compiler = std::env::var("ICI_TEST_CXX").expect("explicit GCC");
        let rows:Vec<_>=(0..2).map(|mode|serde_json::json!({"directory":root.path(),"file":root.path().join("main.cpp"),"arguments":[compiler,"-std=c++17",format!("-DMODE={mode}"),"-Werror","-c",root.path().join("main.cpp"),"-o",format!("mode{mode}.o")]})).collect();
        fs::write(
            root.path().join("compile_commands.json"),
            serde_json::to_vec(&rows).unwrap(),
        )
        .unwrap();
        let tool = std::env::var(variable).expect(variable);
        let config=runtime_config(&["cpp.compile","cpp.complexity","cpp.cognitive","cpp.cycle"],&format!("[checks.\"cpp.dead\"]\nenabled=true\n[tools.\"{tool_id}\"]\npath={}\n[engines.complexity]\ncpp_boundaries='required'\n[engines.cognitive]\ncpp_boundaries='required'\n",serde_json::to_string(&tool).unwrap())).replace("build='native'\n","").replace("sources=['main.cpp']","sources=['main.cpp','*.hpp']");
        fs::write(root.path().join("ici.toml"), config).unwrap();
        let mut environment = BTreeMap::from([("PATH".into(), String::new())]);
        if let Ok(paths) = std::env::var("ICI_TEST_CLANG_LIBRARY_PATH") {
            environment.insert("LD_LIBRARY_PATH".into(), paths);
        }
        let invocation = registry::parse(["ici", "analyze"]).unwrap();
        let preview = planning::prepare(&invocation, root.path(), &environment, true).unwrap();
        assert!(!root.path().join(".ici").exists());
        assert_eq!(
            preview.cpp.boundaries["project.cpp.complexity"].len(),
            2,
            "{}",
            preview.text()
        );
        assert_eq!(preview.cpp.includes["project.cpp.cycle"].len(), 2);
        assert_eq!(preview.cpp.unused["project.cpp.dead"].len(), 2);
        let run = execute::execute(
            &invocation,
            root.path(),
            &environment,
            true,
            &Cancellation::default(),
        )
        .unwrap();
        for id in [
            "project.cpp.complexity",
            "project.cpp.cognitive",
            "project.cpp.cycle",
            "project.cpp.dead",
        ] {
            let output = &run.outputs[id];
            assert_eq!(
                output.observation.state,
                TaskState::Succeeded,
                "{tool_id}: {id}: {output:?}"
            );
        }
        for id in ["project.cpp.complexity", "project.cpp.cognitive"] {
            let output = &run.outputs[id];
            assert_eq!(
                output.extra["boundary_mode"], "exact",
                "{tool_id}: {id}: {output:?}"
            );
            assert!(output.observation.measurements.iter().all(|measurement|measurement.evidence==ici_domain::EvidenceLevel::Estimated),"{output:?}");
            assert!(
                output
                    .targets
                    .iter()
                    .filter(|target| target.file_path == "main.cpp")
                    .all(|target| target.metrics["boundary_confidence"] == "exact"),
                "{output:?}"
            );
        }
        assert!(
            run.outputs["project.cpp.cycle"]
                .observation
                .findings
                .is_empty()
        );
        let dead = &run.outputs["project.cpp.dead"];
        assert_eq!(dead.observation.findings.len(), 1, "{dead:?}");
        assert_eq!(dead.observation.findings[0].primary_location.start_line, 7);
        assert_eq!(
            dead.observation.findings[0].rule_id,
            "dead.cpp-unused-internal-function"
        );
        assert!(!root.path().join("mode0.o").exists());
        assert!(!root.path().join("mode1.o").exists());
    }
}

#[test]
#[ignore = "explicit native CMake preparation and leased user-data preservation"]
fn actual_explicit_cmake_preparation_is_read_only_until_execution_and_overlay_keeps_user_configuration()
 {
    use ici_cli::export_context;
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(root.path().join("CMakeLists.txt"),"cmake_minimum_required(VERSION 3.16)\nproject(prepared LANGUAGES CXX)\nenable_testing()\nadd_executable(suite main.cpp)\nadd_test(NAME green COMMAND suite)\n").unwrap();
    fs::write(root.path().join("data.txt"), "preserved resource\n").unwrap();
    let cmake = std::env::var("ICI_TEST_CMAKE").expect("explicit CMake");
    fs::write(
        root.path().join("ici.toml"),
        format!(
            "schema_version=2\n[tools.cmake]\npath={}\n",
            serde_json::to_string(&cmake).unwrap()
        ),
    )
    .unwrap();
    let config_before = fs::read(root.path().join("ici.toml")).unwrap();
    let environment = BTreeMap::from([("PATH".into(), std::env::var("PATH").unwrap_or_default())]);
    let project =
        ici_config::resolve_with_environment(root.path(), None, None, &environment).unwrap();
    let component = project
        .components
        .iter()
        .find(|component| component.languages.iter().any(|language| language == "cpp"))
        .unwrap();
    let plan = export_context::plan_preparation(
        &project,
        component,
        &environment,
        std::path::Path::new(env!("CARGO_BIN_EXE_ici")),
    )
    .unwrap();
    assert!(!root.path().join(".ici").exists());
    assert!(plan.inputs.contains(&"CMakeLists.txt".into()));
    assert!(plan.inputs.contains(&"data.txt".into()));
    assert_eq!(plan.build.tasks.len(), 2);
    assert!(
        plan.build
            .tasks
            .iter()
            .all(|task| task.mutating() && task.tool_digest.is_some())
    );
    let mut scheduler = ici_execution::Scheduler::new(
        ici_execution::Runner::with_environment(environment.clone()),
        2,
    );
    scheduler.lock_directory = Some(root.path().join(".ici/rust-v1/locks/tasks"));
    let prepared = export_context::execute_preparation(
        &plan,
        &scheduler,
        &Cancellation::default(),
        &ici_cli::events::EventLog::default(),
    )
    .unwrap();
    assert!(prepared.context.complete(), "{:?}", prepared.context);
    assert!(
        prepared
            .artifacts
            .iter()
            .any(|path| path.ends_with("/suite"))
    );
    let alias = export_context::for_consumer(&prepared, &plan).unwrap();
    let mut foreign_plan = plan.clone();
    foreign_plan.build.variant = "different".into();
    assert!(export_context::for_consumer(&prepared, &foreign_plan).is_err());
    let overlay = export_context::overlay(&project, &[prepared]);
    assert!(
        ici_cpp::BuildLease::acquire(
            root.path(),
            &plan.build,
            &Cancellation::default(),
            std::time::Duration::from_millis(20)
        )
        .is_err(),
        "consumer alias must retain the original transaction guard"
    );
    drop(alias);
    assert_eq!(
        fs::read(root.path().join("ici.toml")).unwrap(),
        config_before
    );
    assert_eq!(
        fs::read_to_string(root.path().join("data.txt")).unwrap(),
        "preserved resource\n"
    );
    assert_eq!(
        overlay.components[0].build.as_deref(),
        Some(plan.build_id.as_str())
    );
    assert_eq!(
        overlay.builds[0].directory.as_deref(),
        Some(plan.build.shadow.as_str())
    );
    let foreign = root.path().join("keep.gcda");
    fs::write(&foreign, b"foreign data").unwrap();
    let injected = root.path().join(&plan.build.shadow).join("foreign.gcda");
    fs::hard_link(&foreign, &injected).unwrap();
    let error = export_context::execute_preparation(
        &plan,
        &scheduler,
        &Cancellation::default(),
        &ici_cli::events::EventLog::default(),
    )
    .err()
    .expect("multiply linked data refused");
    assert!(error.contains("multiply linked"), "{error}");
    assert_eq!(fs::read(&foreign).unwrap(), b"foreign data");
    assert_eq!(fs::read(&injected).unwrap(), b"foreign data");
    fs::remove_file(&injected).unwrap();
    let stale = root.path().join(&plan.build.shadow).join("previous.gcda");
    fs::write(&stale, b"owned counters").unwrap();
    let prepared = export_context::execute_preparation(
        &plan,
        &scheduler,
        &Cancellation::default(),
        &ici_cli::events::EventLog::default(),
    )
    .unwrap();
    assert!(!stale.exists());
    assert!(prepared.context.complete());
    assert_eq!(fs::read(&foreign).unwrap(), b"foreign data");
    let mut coverage_project = project.clone();
    coverage_project.components[0].settings["native_requested_variant"] =
        serde_json::json!("coverage");
    let coverage_plan = export_context::plan_preparation(
        &coverage_project,
        &coverage_project.components[0],
        &environment,
        std::path::Path::new(env!("CARGO_BIN_EXE_ici")),
    )
    .unwrap();
    assert_eq!(coverage_plan.build.variant, "coverage");
    let coverage = export_context::execute_preparation(
        &coverage_plan,
        &scheduler,
        &Cancellation::default(),
        &ici_cli::events::EventLog::default(),
    )
    .unwrap();
    let overlay = export_context::overlay(&project, &[prepared, coverage]);
    let component = &overlay.components[0];
    assert_eq!(
        ici_cli::cpp::linked_build(&overlay, component)
            .unwrap()
            .variant,
        "release"
    );
    let request = ici_application::request::RunRequest {
        checks: vec!["cpp.test".into(), "cpp.coverage".into()],
        ..Default::default()
    };
    for id in ["cpp.test", "cpp.coverage"] {
        let check = ici_domain::builtin_catalog()
            .checks
            .into_iter()
            .find(|check| check.id == id)
            .unwrap();
        let bound = ici_cli::cpp_validation::bind(
            &overlay,
            component,
            &check,
            &format!("{}.{}", component.id, id),
            &request,
            &environment,
            "variant-check",
        )
        .unwrap();
        assert!(matches!(
            bound.work,
            ici_application::plan::Work::Steps { .. }
        ));
        assert!(bound.plans.iter().all(|plan| {
            plan.context
                .options
                .build_dir
                .as_ref()
                .unwrap()
                .ends_with("coverage")
        }));
    }
}

#[test]
#[ignore = "explicit same-binary Qt5/Qt6 moc uic rcc preparation and testcase projection"]
fn actual_qmake_preparation_keeps_generated_units_resources_and_testcase_executable() {
    use ici_cli::export_context;
    for variable in ["ICI_TEST_QMAKE", "ICI_TEST_QMAKE6"] {
        let root = tempfile::tempdir().unwrap();
        fs::write(root.path().join("main.cpp"),"#include <QtTest>\n#include \"ui_form.h\"\nclass Suite:public QObject{Q_OBJECT private slots:void green(){QVERIFY(true);}};\nQTEST_GUILESS_MAIN(Suite)\n#include \"main.moc\"\n").unwrap();
        fs::write(root.path().join("suite.pro"),"QT += core testlib widgets\nCONFIG += console c++17 testcase\nCONFIG -= app_bundle\nTEMPLATE=app\nSOURCES += main.cpp\nFORMS += form.ui\nRESOURCES += data.qrc\nTARGET=suite\n").unwrap();
        fs::write(root.path().join("form.ui"),"<ui version=\"4.0\"><class>Widget</class><widget class=\"QWidget\" name=\"Widget\"/></ui>").unwrap();
        fs::write(root.path().join("data.txt"), "original resource\n").unwrap();
        fs::write(
            root.path().join("data.qrc"),
            "<RCC><qresource prefix=\"/\"><file>data.txt</file></qresource></RCC>",
        )
        .unwrap();
        let qmake = std::env::var(variable).expect(variable);
        fs::write(
            root.path().join("ici.toml"),
            format!(
                "schema_version=2\n[tools.qmake]\npath={}\n",
                serde_json::to_string(&qmake).unwrap()
            ),
        )
        .unwrap();
        let environment =
            BTreeMap::from([("PATH".into(), std::env::var("PATH").unwrap_or_default())]);
        let project =
            ici_config::resolve_with_environment(root.path(), None, None, &environment).unwrap();
        let component = project
            .components
            .iter()
            .find(|component| component.languages.iter().any(|language| language == "cpp"))
            .unwrap();
        let plan = export_context::plan_preparation(
            &project,
            component,
            &environment,
            std::path::Path::new(env!("CARGO_BIN_EXE_ici")),
        )
        .unwrap();
        assert!(!root.path().join(".ici").exists());
        assert!(plan.inputs.contains(&"form.ui".into()));
        assert!(plan.inputs.contains(&"data.txt".into()));
        let mut scheduler =
            ici_execution::Scheduler::new(ici_execution::Runner::with_environment(environment), 2);
        scheduler.lock_directory = Some(root.path().join(".ici/rust-v1/locks/tasks"));
        let prepared = export_context::execute_preparation(
            &plan,
            &scheduler,
            &Cancellation::default(),
            &ici_cli::events::EventLog::default(),
        )
        .unwrap();
        assert!(
            prepared.context.complete(),
            "{variable}: {:?}",
            prepared.context
        );
        assert!(
            prepared
                .context
                .units
                .iter()
                .any(|unit| unit.source.ends_with("qrc_data.cpp")),
            "{:?}",
            prepared.context
        );
        assert!(
            prepared
                .test_executable
                .as_deref()
                .is_some_and(|path| path.ends_with("/suite"))
        );
        let overlay = export_context::overlay(&project, &[prepared]);
        assert_eq!(
            overlay.builds[0].settings["test_argv"],
            serde_json::json!(["suite"])
        );
        assert_eq!(
            fs::read_to_string(root.path().join("data.txt")).unwrap(),
            "original resource\n"
        );
        assert!(!root.path().join("Makefile").exists());
        assert!(!root.path().join("main.moc").exists());
    }
}
