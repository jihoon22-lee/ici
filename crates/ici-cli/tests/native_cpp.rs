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
    fs::write(root.path().join("ici.toml"),runtime_config(&["cpp.test","cpp.coverage","cpp.artifact","cpp.binary-compat"],&format!("{tools}[engines.test]\nmin_line_cov=100.0\nmin_file_statements=1\nmax_coverage_regression=2.0\nmin_tem_score=0.0\nmin_changed_line_cov=100.0\nchanged_lines=['main.cpp:1']\n[builds.native]\nsystem='cmake'\ndirectory='build'\nartifacts=['suite']\n"))).unwrap();
    let invocation =
        registry::parse(["ici", "next", "verify", "--events", "events.jsonl"]).unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    let preview = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert!(!root.path().join(".ici").exists());
    assert_eq!(
        preview.validations["project.cpp.coverage"][0]
            .context
            .options
            .max_coverage_regression,
        Some(2.0)
    );
    let options = &preview.validations["project.cpp.coverage"][0]
        .context
        .options;
    assert_eq!(options.min_tem_score, Some(0.0));
    assert_eq!(options.min_changed_line_cov, Some(100.0));
    assert_eq!(options.changed_lines, ["main.cpp:1"]);
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
    for label in ["TEM score", "Changed executable-line coverage"] {
        assert!(
            run.outputs["project.cpp.coverage"]
                .targets
                .iter()
                .any(|target| target.target_name == label && target.status == "PASS"),
            "{:#?}",
            run.outputs["project.cpp.coverage"]
        );
    }
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
    fs::write(
        root.path().join(".clang-tidy"),
        "Checks: '-*,clang-analyzer-core.NullDereference'\n",
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
        preview
            .cpp
            .evidence_configurations_for("project.cpp.tidy")
            .contains(".clang-tidy")
    );
    assert!(
        ici_cli::inputs::capture(&preview)
            .unwrap()
            .files
            .contains_key(".clang-tidy")
    );
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

#[test]
#[ignore = "explicit owned CMake sanitizer inventory instrumentation and runtime acceptance"]
fn actual_cmake_sanitizer_proves_test_executables_and_keeps_runtime_defect_locations() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"),"#include <climits>\nint main(int argc,char**){if(argc>1){volatile int value=INT_MAX;return value+1;}return 0;}\n").unwrap();
    fs::write(root.path().join("CMakeLists.txt"),"cmake_minimum_required(VERSION 3.19)\nproject(sanitizer_fixture LANGUAGES CXX)\nenable_testing()\nadd_executable(suite main.cpp)\nadd_test(NAME \"clean case\" COMMAND suite)\nadd_test(NAME \"overflow case\" COMMAND suite broken)\n").unwrap();
    let cmake = std::env::var("ICI_TEST_CMAKE").expect("explicit CMake");
    let ctest = std::env::var("ICI_TEST_CTEST").expect("explicit CTest");
    let readelf = std::env::var("ICI_TEST_READELF").expect("explicit readelf");
    let config = runtime_config(
        &["cpp.sanitize"],
        &format!(
            "profile='deep'\n[tools.cmake]\npath={}\n[tools.ctest]\npath={}\n[tools.readelf]\npath={}\n[builds.native]\nsystem='cmake'\nproject='.'\nvariant='sanitize'\nartifacts=['suite']\n",
            serde_json::to_string(&cmake).unwrap(),
            serde_json::to_string(&ctest).unwrap(),
            serde_json::to_string(&readelf).unwrap()
        ),
    );
    // The profile key belongs at the document root, before individual check tables.
    let config = config
        .replace("profile='deep'\n", "")
        .replace("schema_version=2\n", "schema_version=2\nprofile='deep'\n");
    fs::write(root.path().join("ici.toml"), &config).unwrap();
    let environment = BTreeMap::from([("PATH".into(), std::env::var("PATH").unwrap_or_default())]);
    let project =
        ici_config::resolve_with_environment(root.path(), None, None, &environment).unwrap();
    let plan = ici_cli::export_context::plan_preparation(
        &project,
        &project.components[0],
        &environment,
        std::path::Path::new(env!("CARGO_BIN_EXE_ici")),
    )
    .unwrap();
    let mut scheduler = ici_execution::Scheduler::new(
        ici_execution::Runner::with_environment(environment.clone()),
        2,
    );
    scheduler.lock_directory = Some(root.path().join(".ici/rust-v1/locks/tasks"));
    let prepared = ici_cli::export_context::execute_preparation(
        &plan,
        &scheduler,
        &Cancellation::default(),
        &ici_cli::events::EventLog::default(),
    )
    .unwrap();
    fs::write(
        root.path().join("ici.toml"),
        config.replace(
            "variant='sanitize'\n",
            &format!("variant='sanitize'\ndirectory='{}'\n", plan.build.shadow),
        ),
    )
    .unwrap();
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let preview = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    let validation = &preview.validations["project.cpp.sanitize"][0];
    assert_eq!(
        validation.context.options.sanitizer_suite_kind,
        Some(ici_validation::ValidationKind::Ctest)
    );
    assert_eq!(
        validation.context.options.instrumented_artifacts,
        vec![format!("{}/suite", plan.build.shadow)]
    );
    assert_eq!(validation.tasks.len(), 3);
    assert!(validation.tasks[1].argv.last().unwrap().ends_with("/suite"));
    assert!(validation.tasks[1].argv[0].ends_with("readelf"));
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    let output = &run.outputs["project.cpp.sanitize"];
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert!(
        output
            .observation
            .findings
            .iter()
            .any(
                |finding| finding.rule_id == "ici.sanitize.ubsan.signed-integer-overflow"
                    && finding.primary_location.path == "main.cpp"
                    && finding.primary_location.start_line == 2
            ),
        "{output:?}"
    );
    let evidence = &output.extra["project.cpp.sanitize"];
    assert_eq!(
        evidence["sanitizer_test_evidence"]["complete"], true,
        "{output:?}"
    );
    assert_eq!(
        evidence["sanitizer_scope"]["instrumented_artifacts"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(run.result.gate.selected, GateVerdict::Fail);
    // An empty framework scope must not inherit clean instrumentation as a passing test run.
    fs::write(
        root.path()
            .join(&plan.build.shadow)
            .join("CTestTestfile.cmake"),
        "\n",
    )
    .unwrap();
    let empty = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        empty.outputs["project.cpp.sanitize"].observation.state,
        TaskState::Blocked,
        "{:?}",
        empty.outputs
    );
    assert_eq!(empty.result.gate.selected, GateVerdict::Incomplete);
    assert!(prepared.context.complete());
}

#[test]
#[ignore = "explicit CMake executable shared static and static-PIE release producer acceptance"]
fn actual_compiled_producer_preserves_elf_kinds_soname_aliases_and_common_release_publication() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(
        root.path().join("library.cpp"),
        "extern \"C\" int value(){return 3;}\n",
    )
    .unwrap();
    fs::write(root.path().join("CMakeLists.txt"),"cmake_minimum_required(VERSION 3.16)\nproject(compiled_fixture LANGUAGES CXX)\nadd_executable(suite main.cpp)\nadd_executable(static_pie main.cpp)\ntarget_link_options(static_pie PRIVATE -static-pie)\nadd_library(shared SHARED library.cpp)\nset_target_properties(shared PROPERTIES VERSION 2.3 SOVERSION 2)\nadd_library(archive STATIC library.cpp)\n").unwrap();
    let cmake = std::env::var("ICI_TEST_CMAKE").expect("explicit CMake");
    fs::write(
        root.path().join("ici.toml"),
        format!(
            "schema_version=2\nname='compiled-fixture'\nversion='v1.2.3'\n[tools.cmake]\npath={}\n",
            serde_json::to_string(&cmake).unwrap()
        ),
    )
    .unwrap();
    let original = fs::read(root.path().join("main.cpp")).unwrap();
    let environment = BTreeMap::from([("PATH".into(), std::env::var("PATH").unwrap_or_default())]);
    let project =
        ici_config::resolve_with_environment(root.path(), None, None, &environment).unwrap();
    let component = &project.components[0];
    let plan = ici_cli::export_context::plan_preparation(
        &project,
        component,
        &environment,
        std::path::Path::new(env!("CARGO_BIN_EXE_ici")),
    )
    .unwrap();
    let mut scheduler = ici_execution::Scheduler::new(
        ici_execution::Runner::with_environment(environment.clone()),
        2,
    );
    scheduler.lock_directory = Some(root.path().join(".ici/rust-v1/locks/tasks"));
    let prepared = ici_cli::export_context::execute_preparation(
        &plan,
        &scheduler,
        &Cancellation::default(),
        &ici_cli::events::EventLog::default(),
    )
    .unwrap();
    assert!(
        prepared
            .artifacts
            .iter()
            .all(|path| !path.contains("/CMakeFiles/"))
    );
    let sources = ici_cli::export_context::compiled_artifacts(
        &project,
        component,
        std::slice::from_ref(&prepared),
    )
    .unwrap();
    assert!(sources.iter().any(|source| source.path.ends_with("/suite")
        && source.kind == "executable"
        && source.destination_name.is_none()));
    assert!(
        sources
            .iter()
            .any(|source| source.path.ends_with("/static_pie") && source.kind == "executable")
    );
    assert!(sources.iter().any(|source|source.path.ends_with("/libarchive.a")&&source.kind=="static-library"));
    assert!(
        sources
            .iter()
            .any(|source| source.path.ends_with("/libshared.so.2.3")
                && source.kind == "shared-library"
                && source.destination_name.as_deref() == Some("libshared.so.2"))
    );
    assert!(
        sources
            .iter()
            .all(|source| !source.path.contains("/CMakeFiles/"))
    );
    let release = ici_cli::artifact_build::describe_compiled(
        &project,
        component,
        "project.cpp.artifact",
        &environment,
        &sources,
    )
    .unwrap();
    assert!(!root.path().join("v1.2.3").exists());
    let inputs = release
        .task
        .input_refs
        .iter()
        .map(|path| {
            std::path::Path::new(path)
                .strip_prefix(root.path())
                .unwrap()
                .to_string_lossy()
                .into_owned()
        })
        .collect::<Vec<_>>();
    let snapshot = ici_application::snapshot::InputSnapshot::take(
        root.path(),
        &inputs,
        &[],
        ici_application::snapshot::InputLimits {
            file_bytes: 256 * 1024 * 1024,
            total_bytes: 512 * 1024 * 1024,
        },
    )
    .unwrap();
    let output = ici_cli::artifact_build::execute(&release, &snapshot, &Cancellation::default());
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    let published = root.path().join("v1.2.3/x86_64");
    assert!(
        std::process::Command::new(published.join("bin/suite"))
            .status()
            .unwrap()
            .success()
    );
    assert!(
        std::process::Command::new(published.join("bin/static_pie"))
            .status()
            .unwrap()
            .success()
    );
    assert_eq!(
        fs::read(published.join("lib/libshared.so.2")).unwrap(),
        fs::read(published.join("lib/libshared.so.2.3")).unwrap()
    );
    assert!(
        fs::symlink_metadata(published.join("lib/libshared.so.2"))
            .unwrap()
            .is_file()
    );
    assert_eq!(fs::read(root.path().join("main.cpp")).unwrap(), original);
    let mut alias = prepared.clone();
    alias.plan.declared_build = Some(ici_config::Build {
        id: plan.build_id.clone(),
        system: Some("cmake".into()),
        project: Some("CMakeLists.txt".into()),
        directory: Some(plan.build.shadow.clone()),
        variant: "release".into(),
        prepare: Some("explicit".into()),
        artifacts: vec!["libshared.so.2".into()],
        settings: serde_json::json!({}),
        origin: component.origin.clone(),
    });
    let only_alias =
        ici_cli::export_context::compiled_artifacts(&project, component, &[alias]).unwrap();
    assert_eq!(only_alias.len(), 1);
    assert_eq!(
        only_alias[0].destination_name.as_deref(),
        Some("libshared.so.2")
    );
    let thin = root.path().join(&plan.build.shadow).join("thin.a");
    fs::write(&thin, b"!<thin>\n").unwrap();
    assert!(
        ici_cli::export_context::compiled_artifact_kind(&thin)
            .unwrap_err()
            .contains("thin archives")
    );
}

#[test]
fn linker_analysis_requires_explicit_preparation_and_disabled_dead_checks_spawn_nothing() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(root.path().join("CMakeLists.txt"),"cmake_minimum_required(VERSION 3.16)\nproject(dead LANGUAGES CXX)\nadd_executable(dead main.cpp)\n").unwrap();
    let config = runtime_config(
        &[],
        "[checks.'cpp.dead']\nenabled=true\n[engines.dead]\ncpp_unused='off'\ncpp_linker='required'\n[builds.native]\nsystem='cmake'\n",
    );
    fs::write(root.path().join("ici.toml"), &config).unwrap();
    let invocation = registry::parse(["ici", "analyze", "--profile", "deep"]).unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    let plan = planning::prepare(&invocation, root.path(), &environment, true).unwrap();
    assert!(plan.context_preparations.is_empty());
    assert!(matches!(
        plan.plan
            .checks
            .iter()
            .find(|check| check.check.id == "cpp.dead")
            .unwrap()
            .work,
        ici_application::plan::Work::Blocked { .. }
    ));
    assert!(plan.tools.is_empty());
    assert!(!root.path().join(".ici").exists());
    fs::write(
        root.path().join("ici.toml"),
        config.replace("cpp_linker='required'", "cpp_linker='off'"),
    )
    .unwrap();
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        true,
        &Cancellation::default(),
    )
    .unwrap();
    let output = &run.outputs["project.cpp.dead"];
    assert_eq!(output.status, "SKIP");
    assert_eq!(output.targets[0].file_path, "main.cpp");
    assert!(!root.path().join(".ici/rust-v1/build").exists());
}

#[test]
#[ignore = "explicit actual GCC GNU ld readelf addr2line owned relink acceptance"]
fn actual_linker_dead_cli_intersects_targets_and_preserves_final_input_snapshot() {
    let root = tempfile::tempdir().unwrap();
    let sources = [
        (
            "common.cpp",
            "__attribute__((noinline,visibility(\"hidden\"))) int kept_for_one(){return 1;}\n__attribute__((noinline,visibility(\"hidden\"))) int dead_everywhere(){return 2;}\n__attribute__((noinline)) int exported_default(){return 3;}\n",
        ),
        (
            "one.cpp",
            "int kept_for_one(); int main(){return kept_for_one();}\n",
        ),
        ("two.cpp", "int main(){return 0;}\n"),
    ];
    for (path, text) in sources {
        fs::write(root.path().join(path), text).unwrap();
    }
    fs::write(root.path().join("CMakeLists.txt"),"cmake_minimum_required(VERSION 3.16)\nproject(linker_cli LANGUAGES CXX)\nadd_library(common OBJECT common.cpp)\nadd_executable(one one.cpp $<TARGET_OBJECTS:common>)\nadd_executable(two two.cpp $<TARGET_OBJECTS:common>)\n").unwrap();
    let cxx = std::env::var("ICI_TEST_CXX").expect("actual GCC");
    let cmake = std::env::var("ICI_TEST_CMAKE").expect("actual CMake");
    let readelf = std::env::var("ICI_TEST_READELF").expect("actual readelf");
    let addr2line = std::env::var("ICI_TEST_ADDR2LINE").expect("actual addr2line");
    let extra = format!(
        "[checks.'cpp.dead']\nenabled=true\n[engines.dead]\ncpp_unused='off'\ncpp_linker='required'\n[builds.native]\nsystem='cmake'\n[tools.'c++']\npath={}\n[tools.cmake]\npath={}\n[tools.readelf]\npath={}\n[tools.addr2line]\npath={}\n",
        serde_json::to_string(&cxx).unwrap(),
        serde_json::to_string(&cmake).unwrap(),
        serde_json::to_string(&readelf).unwrap(),
        serde_json::to_string(&addr2line).unwrap()
    );
    let config = runtime_config(&[], &extra).replace(
        "sources=['main.cpp']",
        "sources=['common.cpp','one.cpp','two.cpp']",
    );
    fs::write(root.path().join("ici.toml"), &config).unwrap();
    let invocation = registry::parse(["ici", "dead"]).unwrap();
    let environment = BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]);
    let plan = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert_eq!(plan.context_preparations.len(), 1, "{}", plan.text());
    assert_eq!(
        plan.context_preparations[0].build.variant,
        "link-reachability"
    );
    assert!(!root.path().join(".ici").exists());
    assert!(plan.text().contains("-ffunction-sections"));
    // Serialized generated metadata alone does not grant permission to relink it.
    let scheduler = ici_execution::Scheduler::new(
        ici_execution::Runner::with_environment(environment.clone()),
        2,
    );
    let prepared = ici_cli::export_context::execute_preparation(
        &plan.context_preparations[0],
        &scheduler,
        &Cancellation::default(),
        &ici_cli::events::EventLog::default(),
    )
    .unwrap();
    let overlay = ici_cli::export_context::overlay(&plan.project, std::slice::from_ref(&prepared));
    let component = &overlay.components[0];
    let mut forged = ici_cli::cpp::CppPlans::default();
    let definition = ici_cli::cpp::supplemental_checks(&overlay).remove(0);
    let work = forged
        .bind(
            &overlay,
            component,
            &definition,
            "project.cpp.dead",
            &environment,
        )
        .unwrap();
    assert!(matches!(work, ici_application::plan::Work::Steps { .. }));
    let before = fs::metadata(root.path().join(&prepared.plan.build.shadow).join("one"))
        .unwrap()
        .modified()
        .unwrap();
    let request = ici_domain::AnalysisRequest {
        task_id: "project.cpp.dead".into(),
        check_id: "cpp.dead".into(),
        component_id: component.id.clone(),
        language: "cpp".into(),
        inputs: sources
            .iter()
            .map(|(path, text)| ici_domain::SourceInput {
                path: (*path).into(),
                bytes: text.as_bytes().to_vec(),
            })
            .collect(),
        options: serde_json::json!({}),
        target_version: "c++17".into(),
    };
    let denied = forged.run(
        &overlay,
        component,
        &request,
        &scheduler,
        &Cancellation::default(),
        &ici_cli::events::EventLog::default(),
    );
    assert_eq!(denied.observation.state, TaskState::Failed);
    assert!(denied.summary.contains("held native build lease"));
    assert_eq!(
        fs::metadata(root.path().join(&prepared.plan.build.shadow).join("one"))
            .unwrap()
            .modified()
            .unwrap(),
        before
    );
    drop(prepared);
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    let output = &run.outputs["project.cpp.dead"];
    assert_eq!(
        output.observation.state,
        TaskState::Succeeded,
        "{output:?}\n{:?}",
        run.result.execution
    );
    assert_eq!(output.observation.findings.len(), 1, "{output:?}");
    assert!(
        output
            .targets
            .iter()
            .any(
                |target| target.metrics.get("symbol").is_some_and(|symbol| symbol
                    .as_str()
                    .is_some_and(|symbol| symbol.contains("dead_everywhere")))
            )
    );
    assert!(
        !output
            .targets
            .iter()
            .any(
                |target| target.metrics.get("symbol").is_some_and(|symbol| symbol
                    .as_str()
                    .is_some_and(|symbol| symbol.contains("kept_for_one")
                        || symbol.contains("exported_default")))
            )
    );
    assert!(
        run.result.execution.required_complete,
        "{:?}",
        run.result.execution
    );
    assert_ne!(
        run.result.gate.selected,
        ici_domain::GateVerdict::Incomplete,
        "{:?}",
        run.result.gate
    );
    assert!(
        !run.result
            .gate
            .reasons
            .iter()
            .any(|reason| reason.contains("inputs changed")),
        "{:?}",
        run.result.gate
    );
    let source = fs::read_to_string(root.path().join("common.cpp")).unwrap();
    assert_eq!(source, sources[0].1);
    let steps = &output.extra["linker_probes"]["steps"];
    assert!(
        steps
            .as_array()
            .unwrap()
            .iter()
            .any(|step| step["provider"] == "addr2line")
    );
    assert!(!steps.to_string().contains("{observed-discarded-section}"));
    let relocated_root = tempfile::tempdir().unwrap();
    for file in [
        "common.cpp",
        "one.cpp",
        "two.cpp",
        "CMakeLists.txt",
        "ici.toml",
    ] {
        fs::copy(root.path().join(file), relocated_root.path().join(file)).unwrap();
    }
    let relocated = execute::execute(
        &invocation,
        relocated_root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert!(
        relocated.result.execution.required_complete,
        "{:?}",
        relocated.result.gate
    );
    assert_eq!(
        run.result.checks.as_ref().unwrap()[0].scope_digest,
        relocated.result.checks.as_ref().unwrap()[0].scope_digest,
        "normalized parsed DB/link metadata must preserve selected scope across checkouts"
    );
    fs::write(
        root.path().join("ici.toml"),
        config.replace("cpp_unused='off'", "cpp_unused='auto'"),
    )
    .unwrap();
    let combined_plan = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert_eq!(
        combined_plan.context_preparations.len(),
        2,
        "{}",
        combined_plan.text()
    );
    let combined = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        combined.outputs["project.cpp.dead"].observation.state,
        TaskState::Succeeded,
        "{:?}",
        combined.outputs["project.cpp.dead"]
    );
    assert!(
        combined.result.execution.required_complete,
        "{:?}",
        combined.result.execution
    );
    assert_ne!(
        combined.result.gate.selected,
        ici_domain::GateVerdict::Incomplete,
        "{:?}",
        combined.result.gate
    );
    assert_eq!(
        combined.outputs["project.cpp.dead"]
            .observation
            .findings
            .len(),
        1
    );
}

#[test]
fn static_only_abi_is_located_not_applicable_without_readelf_or_implicit_build() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    fs::create_dir(root.path().join("build")).unwrap();
    fs::write(root.path().join("build/libonly.a"), b"!<arch>\n").unwrap();
    fs::write(root.path().join("build/report.json"), "{}\n").unwrap();
    let mut reloc = vec![0u8; 20];
    reloc[..4].copy_from_slice(b"\x7fELF");
    reloc[4] = 2;
    reloc[5] = 1;
    reloc[16] = 1;
    fs::write(root.path().join("build/unit.o"), reloc).unwrap();
    let config = runtime_config(
        &["cpp.binary-compat"],
        "[builds.native]\nsystem='cmake'\ndirectory='build'\nartifacts=['*']\n",
    );
    let config = config.replace(
        "[builds.native]",
        "[project]\ncompile_database='missing-compilation.json'\n[builds.native]",
    );
    fs::write(root.path().join("ici.toml"), &config).unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let plan = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert!(plan.tools.is_empty());
    assert!(plan.context_preparations.is_empty());
    assert!(!root.path().join(".ici").exists());
    assert_eq!(plan.cpp.evidence_input_limits().len(), 3);
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    let output = &run.outputs["project.cpp.binary-compat"];
    assert_eq!(output.status, "SKIP");
    assert_eq!(
        output.observation.measurements[0].evidence,
        ici_domain::EvidenceLevel::NotApplicable
    );
    assert_eq!(output.extra["elf"]["state"], "NOT_APPLICABLE");
    assert_eq!(output.targets.len(), 3);
    assert!(
        output
            .targets
            .iter()
            .all(|target| target.file_path.starts_with("build/") && target.start_line == 1)
    );
    assert!(run.result.execution.required_complete);
    assert_ne!(run.result.gate.selected, GateVerdict::Incomplete);
    fs::write(
        root.path().join("ici.toml"),
        config.replace(
            "[builds.native]",
            "[engines.binary_compat]\nrequired=true\n[builds.native]",
        ),
    )
    .unwrap();
    let plan = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert!(matches!(
        plan.plan.checks[0].work,
        ici_application::plan::Work::Blocked { .. }
    ));
    assert!(plan.tools.is_empty());
    fs::write(
        root.path().join("ici.toml"),
        config.replace(
            "[builds.native]",
            "[engines.binary_compat]\nartifacts=['libonly.a']\n[builds.native]",
        ),
    )
    .unwrap();
    let plan = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert!(matches!(
        plan.plan.checks[0].work,
        ici_application::plan::Work::Blocked { .. }
    ));
    assert!(plan.text().contains("readelf"));
}

#[test]
#[ignore = "explicit actual GCC ar readelf default ELF ABI selection"]
fn actual_abi_default_scope_ignores_archives_objects_reports_and_deduplicates_soname_aliases() {
    use std::os::unix::fs::symlink;
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(root.path().join("lib.cpp"), "int exported(){return 1;}\n").unwrap();
    fs::create_dir(root.path().join("build")).unwrap();
    let cxx = std::env::var("ICI_TEST_CXX").expect("actual GCC");
    let ar = std::env::var("ICI_TEST_AR").expect("actual ar");
    let readelf = std::env::var("ICI_TEST_READELF").expect("actual readelf");
    for args in [
        vec!["main.cpp", "-o", "build/application"],
        vec!["-shared", "-fPIC", "lib.cpp", "-o", "build/libgood.so.2.3"],
        vec!["-c", "lib.cpp", "-o", "build/unit.o"],
    ] {
        let status = std::process::Command::new(&cxx)
            .args(args)
            .current_dir(root.path())
            .status()
            .unwrap();
        assert!(status.success());
    }
    let status = std::process::Command::new(ar)
        .args(["rcs", "build/libonly.a", "build/unit.o"])
        .current_dir(root.path())
        .status()
        .unwrap();
    assert!(status.success());
    symlink("libgood.so.2.3", root.path().join("build/libgood.so")).unwrap();
    fs::write(root.path().join("build/report.json"), "{}\n").unwrap();
    let config = runtime_config(
        &["cpp.artifact", "cpp.binary-compat"],
        &format!(
            "[builds.native]\nsystem='cmake'\ndirectory='build'\nartifacts=['*']\n[tools.readelf]\npath={}\n",
            serde_json::to_string(&readelf).unwrap()
        ),
    );
    fs::write(root.path().join("ici.toml"), &config).unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let plan = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert_eq!(
        plan.validations["project.cpp.binary-compat"][0]
            .context
            .targets,
        vec!["build/application", "build/libgood.so.2.3"]
    );
    assert_eq!(plan.cpp.evidence_input_limits().len(), 5);
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    let output = &run.outputs["project.cpp.binary-compat"];
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert!(run.result.execution.required_complete);
    assert_ne!(
        run.result.gate.selected,
        GateVerdict::Incomplete,
        "{:?}",
        run.result.gate
    );
    fs::write(
        root.path().join("ici.toml"),
        config.replace(
            "[builds.native]",
            "[engines.binary_compat]\nartifacts=['libgood.so']\n[builds.native]",
        ),
    )
    .unwrap();
    let alias = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert_eq!(
        alias.validations["project.cpp.binary-compat"][0]
            .context
            .targets,
        vec!["build/libgood.so.2.3"]
    );
    fs::write(
        root.path().join("ici.toml"),
        config.replace(
            "[builds.native]",
            "[engines.binary_compat]\nartifacts=['libonly.a']\n[builds.native]",
        ),
    )
    .unwrap();
    let archive = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    let output = &archive.outputs["project.cpp.binary-compat"];
    assert!(
        output
            .observation
            .findings
            .iter()
            .any(|finding| finding.rule_id.contains("non-elf")),
        "{output:?}"
    );
    assert!(
        archive.result.execution.required_complete,
        "{:?}",
        archive.result.execution
    );
}

#[test]
fn legacy_cpp_type_keeps_located_not_applicable_without_reading_compilation_context() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    fs::write(root.path().join("ici.toml"),"schema_version=2\n[project]\ncompile_database='missing.json'\n[[components]]\nid='native'\nroot='.'\nlanguages=['cpp']\n").unwrap();
    let invocation = registry::parse(["ici", "type"]).unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    let plan = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert_eq!(plan.plan.checks.len(), 1, "{}", plan.text());
    assert_eq!(plan.plan.checks[0].check.id, "cpp.type");
    assert!(plan.tools.is_empty());
    assert!(plan.cpp.contexts.is_empty());
    assert!(plan.context_preparations.is_empty());
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    let output = &run.outputs["native.cpp.type"];
    assert_eq!(output.status, "SKIP");
    assert_eq!(output.targets[0].file_path, "main.cpp");
    assert_eq!(output.targets[0].target_name, "CppType:NotApplicable");
    assert_eq!(
        output.observation.measurements[0].evidence,
        ici_domain::EvidenceLevel::NotApplicable
    );
    assert!(run.result.execution.required_complete);
}

#[test]
#[ignore = "explicit actual legacy ABI preparation preserving pre-existing user artifacts"]
fn actual_legacy_abi_preparation_preserves_original_artifact_inputs_after_owned_scope_switch() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(root.path().join("CMakeLists.txt"),"cmake_minimum_required(VERSION 3.16)\nproject(abi_scope LANGUAGES CXX)\nadd_executable(application main.cpp)\n").unwrap();
    fs::create_dir(root.path().join("build")).unwrap();
    let cxx = std::env::var("ICI_TEST_CXX").expect("actual GCC");
    let cmake = std::env::var("ICI_TEST_CMAKE").expect("actual CMake");
    let readelf = std::env::var("ICI_TEST_READELF").expect("actual readelf");
    assert!(
        std::process::Command::new(&cxx)
            .args(["main.cpp", "-o", "build/application"])
            .current_dir(root.path())
            .status()
            .unwrap()
            .success()
    );
    let original = fs::read(root.path().join("build/application")).unwrap();
    let config = runtime_config(
        &["cpp.artifact", "cpp.binary-compat"],
        &format!(
            "[builds.native]\nsystem='cmake'\ndirectory='build'\nartifacts=['application']\n[tools.cmake]\npath={}\n[tools.'c++']\npath={}\n[tools.readelf]\npath={}\n",
            serde_json::to_string(&cmake).unwrap(),
            serde_json::to_string(&cxx).unwrap(),
            serde_json::to_string(&readelf).unwrap()
        ),
    );
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let invocation = registry::parse(["ici", "verify", "--profile", "deep"]).unwrap();
    let environment = BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]);
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        fs::read(root.path().join("build/application")).unwrap(),
        original
    );
    assert_eq!(
        run.outputs["project.cpp.binary-compat"].observation.state,
        TaskState::Succeeded,
        "{:?}\n{:?}",
        run.outputs,
        run.result.limitations
    );
    assert!(
        run.result.execution.required_complete,
        "{:?}",
        run.result.gate
    );
    assert_ne!(
        run.result.gate.selected,
        GateVerdict::Incomplete,
        "{:?}",
        run.result.gate
    );
}

#[test]
fn recorded_compilation_scope_hashes_response_configuration_but_not_source_code_bytes() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    fs::create_dir(root.path().join("include")).unwrap();
    fs::write(
        root.path().join("flags.rsp"),
        format!(
            "-std=c++17 -DMODE=1 -I{}",
            root.path().join("include").display()
        ),
    )
    .unwrap();
    fs::write(root.path().join("compile_commands.json"),serde_json::to_vec(&serde_json::json!([{"directory":root.path(),"file":"main.cpp","arguments":["g++","@flags.rsp","-c","main.cpp","-o","main.o"]}])).unwrap()).unwrap();
    let config = runtime_config(&["cpp.compile"], "").replace("build='native'\n", "");
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    let plan = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert_eq!(
        plan.cpp.evidence_configurations_for("project.cpp.compile"),
        ["compile_commands.json".into(), "flags.rsp".into()]
            .into_iter()
            .collect()
    );
    assert!(plan.tools.is_empty());
    let first = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert!(first.result.execution.required_complete);
    let scope = first.result.checks.as_ref().unwrap()[0]
        .scope_digest
        .clone();
    let other = tempfile::tempdir().unwrap();
    fs::create_dir(other.path().join("include")).unwrap();
    fs::copy(root.path().join("main.cpp"), other.path().join("main.cpp")).unwrap();
    fs::copy(root.path().join("ici.toml"), other.path().join("ici.toml")).unwrap();
    fs::write(
        other.path().join("flags.rsp"),
        fs::read_to_string(root.path().join("flags.rsp"))
            .unwrap()
            .replace(
                root.path().to_str().unwrap(),
                other.path().to_str().unwrap(),
            ),
    )
    .unwrap();
    fs::write(
        other.path().join("compile_commands.json"),
        fs::read_to_string(root.path().join("compile_commands.json"))
            .unwrap()
            .replace(
                root.path().to_str().unwrap(),
                other.path().to_str().unwrap(),
            ),
    )
    .unwrap();
    let relocated = execute::execute(
        &invocation,
        other.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert!(relocated.result.execution.required_complete);
    assert_eq!(
        relocated.result.checks.as_ref().unwrap()[0].scope_digest,
        scope,
        "absolute DB and RSP project paths are semantic metadata"
    );
    fs::write(root.path().join("main.cpp"), "int corrected_value;\n").unwrap();
    let fixed = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert!(fixed.result.execution.required_complete);
    assert_eq!(fixed.result.checks.as_ref().unwrap()[0].scope_digest, scope);
    fs::write(
        root.path().join("flags.rsp"),
        format!(
            "-std=c++17 -DMODE=2 -I{}",
            root.path().join("include").display()
        ),
    )
    .unwrap();
    let reconfigured = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert!(reconfigured.result.execution.required_complete);
    assert_ne!(
        reconfigured.result.checks.as_ref().unwrap()[0].scope_digest,
        scope
    );
}

#[test]
fn compilation_policy_flags_and_missing_database_policy_reach_the_common_cli_gate() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    let config=runtime_config(&["cpp.compile"],"[engines.compile_db]\ndatabase_required=false\nrequired_flags=['-fPIC']\nforbidden_flags=['-fpermissive']\n").replace("build='native'\n","");
    fs::write(root.path().join("ici.toml"), &config).unwrap();
    let invocation = registry::parse(["ici", "verify"]).unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    let modern = execute::execute(
        &registry::parse(["ici", "next", "verify"]).unwrap(),
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(modern.result.gate.selected, GateVerdict::Incomplete);
    assert!(!modern.result.execution.required_complete);
    let optional = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(optional.outputs["project.cpp.compile"].status, "WARN");
    assert!(optional.result.execution.required_complete);
    assert_ne!(optional.result.gate.selected, GateVerdict::Incomplete);
    fs::write(
        root.path().join("ici.toml"),
        config.replace("database_required=false", "database_required=true"),
    )
    .unwrap();
    let required = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(required.outputs["project.cpp.compile"].status, "FAIL");
    assert_eq!(required.result.gate.selected, GateVerdict::Fail);
    assert!(required.result.execution.required_complete);
    fs::write(root.path().join("ici.toml"), &config).unwrap();
    let rows = serde_json::json!([{"directory":root.path(),"file":"main.cpp","arguments":["g++","-fpermissive","-c","main.cpp","-o","main.o"]}]);
    fs::write(
        root.path().join("compile_commands.json"),
        serde_json::to_vec(&rows).unwrap(),
    )
    .unwrap();
    let flags = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(flags.result.gate.selected, GateVerdict::Fail);
    let output = &flags.outputs["project.cpp.compile"];
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert_eq!(output.observation.findings.len(), 2);
    assert!(
        output
            .targets
            .iter()
            .any(|target| target.target_name == "ici.compile-db.required-flag")
    );
    assert!(
        output
            .targets
            .iter()
            .any(|target| target.target_name == "ici.compile-db.forbidden-flag")
    );
}

#[test]
fn legacy_python_only_descriptors_return_cpp_applicability_through_actual_cli_dispatch() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    fs::write(root.path().join("ici.toml"),"schema_version=2\n[project]\ncompile_database='missing.json'\n[[components]]\nid='native'\nroot='.'\nlanguages=['cpp']\n").unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    for (command, check) in [
        ("resource", "cpp.resource"),
        ("security", "cpp.security"),
        ("python-compat", "cpp.python-compat"),
    ] {
        let invocation = registry::parse(["ici", command]).unwrap();
        let plan = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
        assert_eq!(plan.plan.checks.len(), 1, "{}", plan.text());
        assert_eq!(plan.plan.checks[0].check.id, check);
        assert!(plan.tools.is_empty());
        assert!(plan.cpp.contexts.is_empty());
        assert!(plan.context_preparations.is_empty());
        let output = ici_cli::commands::dispatch(&invocation, root.path(), &environment).unwrap();
        assert_eq!(output.exit_code, 0, "{}", output.stderr);
        let stored = ici_cli::report::load(&root.path().join(ici_cli::DEFAULT_RESULT)).unwrap();
        let bytes = stored.to_json().unwrap();
        assert!(bytes.contains("NOT_APPLICABLE"));
        assert!(!root.path().join(".ici/rust-v1/build").exists());
    }
}

#[test]
fn generated_and_vendor_duplicate_options_change_actual_selected_cpp_findings() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("Vendor/Generated")).unwrap();
    let code = "int f(int x) {\n int a=x;\n a+=1;\n a*=2;\n a-=3;\n a/=4;\n return a;\n}\n";
    for path in ["main.cpp", "other.cpp", "Vendor/Generated/moc_item.cpp"] {
        fs::write(root.path().join(path), code).unwrap();
    }
    let invocation = registry::parse(["ici", "dup"]).unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    for (generated, vendor, expected) in [
        (false, false, 2),
        (true, false, 2),
        (false, true, 2),
        (true, true, 3),
    ] {
        let config = runtime_config(
            &["cpp.dup"],
            &format!("[engines.dup]\ninclude_generated={generated}\ninclude_vendor={vendor}\n"),
        )
        .replace("sources=['main.cpp']", "sources=['**/*.cpp']")
        .replace("build='native'\n", "");
        fs::write(root.path().join("ici.toml"), &config).unwrap();
        let run = execute::execute(
            &invocation,
            root.path(),
            &environment,
            false,
            &Cancellation::default(),
        )
        .unwrap();
        let output = &run.outputs["project.cpp.dup"];
        assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
        assert_eq!(output.observation.findings.len(), expected, "{output:?}");
        assert!(run.result.execution.required_complete);
        for option in ["include_generated", "include_vendor"] {
            let invalid = config.replace(
                &format!(
                    "{option}={}",
                    if option == "include_generated" {
                        generated
                    } else {
                        vendor
                    }
                ),
                &format!("{option}='true'"),
            );
            fs::write(root.path().join("ici.toml"), invalid).unwrap();
            assert!(planning::prepare(&invocation, root.path(), &environment, false).is_err());
        }
    }
}

#[test]
fn cpp_cycle_report_limit_reaches_actual_cli_counts_and_rejects_zero() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    for path in ["a.h", "b.h", "c.h"] {
        fs::write(root.path().join(path), format!("#include \"{path}\"\n")).unwrap();
    }
    let invocation = registry::parse(["ici", "cycle"]).unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    for limit in [1, 2] {
        let config = runtime_config(
            &["cpp.cycle"],
            &format!("[engines.cycle]\nmax_reported={limit}\n"),
        )
        .replace("sources=['main.cpp']", "sources=['*.cpp','*.h']")
        .replace("build='native'\n", "");
        fs::write(root.path().join("ici.toml"), &config).unwrap();
        let run = execute::execute(
            &invocation,
            root.path(),
            &environment,
            false,
            &Cancellation::default(),
        )
        .unwrap();
        let output = &run.outputs["project.cpp.cycle"];
        assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
        assert_eq!(output.observation.findings.len(), limit);
        assert_eq!(output.extra["cycles_reported"], limit);
        assert_eq!(output.extra["cycles_truncated"], 3 - limit);
        assert_eq!(
            output
                .observation
                .measurements
                .iter()
                .find(|metric| metric.name == "cycles_found")
                .unwrap()
                .value,
            3.0
        );
        fs::write(
            root.path().join("ici.toml"),
            config.replace(&format!("max_reported={limit}"), "max_reported=0"),
        )
        .unwrap();
        assert!(planning::prepare(&invocation, root.path(), &environment, false).is_err());
    }
}

#[test]
#[ignore = "explicit native GCC source ownership CLI acceptance"]
fn actual_gcc_dead_options_apply_each_generated_and_vendor_dimension() {
    let root = tempfile::tempdir().unwrap();
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit native GCC");
    fs::create_dir_all(root.path().join("Vendor/Generated")).unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(
        root.path().join("Vendor/Generated/item.cpp"),
        "static int unused(){return 1;}\nint value(){return 0;}\n",
    )
    .unwrap();
    let rows=serde_json::json!(["main.cpp","Vendor/Generated/item.cpp"].map(|path|serde_json::json!({"directory":root.path(),"file":path,"arguments":[compiler,"-c",path,"-o","must-not-create.o"]})));
    fs::write(
        root.path().join("compile_commands.json"),
        serde_json::to_vec(&rows).unwrap(),
    )
    .unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    for (generated, vendor, expected) in [
        (false, false, 0),
        (true, false, 0),
        (false, true, 0),
        (true, true, 1),
    ] {
        let config=runtime_config(&[],&format!("[checks.\"cpp.dead\"]\nenabled=true\n[engines.dead]\ncpp_unused='required'\ncpp_linker='off'\ninclude_generated={generated}\ninclude_vendor={vendor}\n"))
            .replace("sources=['main.cpp']","sources=['**/*.cpp']")
            .replace("build='native'\n","");
        fs::write(root.path().join("ici.toml"), config).unwrap();
        let run = execute::execute(
            &invocation,
            root.path(),
            &environment,
            false,
            &Cancellation::default(),
        )
        .unwrap();
        let output = &run.outputs["project.cpp.dead"];
        assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
        assert_eq!(output.observation.findings.len(), expected, "{output:?}");
        assert!(run.result.execution.required_complete);
        if expected == 1 {
            assert_eq!(
                output.observation.findings[0].primary_location.path,
                "Vendor/Generated/item.cpp"
            );
        }
        assert!(!root.path().join("must-not-create.o").exists());
    }
}
