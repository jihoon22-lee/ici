use ici_cpp::{
    BuildLease, BuildRequest, ContextRequest, LinkerObjectEvidence, LinkerTargetEvidence,
    analyze_linker_dead, discover_link_commands, eligible_discarded_symbol, linker_identity_task,
    load_compilation_context, parse_object_facts, plan_linker_build, relink_task,
};
use ici_domain::{AnalysisRequest, SourceInput, TaskSpec, TaskState};
use ici_execution::{Cancellation, Runner, Scheduler};
use serde_json::json;
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    time::Duration,
};
fn run_task(task: TaskSpec) -> ici_execution::TaskOutcome {
    let result = Runner::default().run(&task, &Cancellation::default());
    assert!(
        result.prerequisite_succeeded(),
        "{:?}\n{}",
        result.error,
        String::from_utf8_lossy(&result.stderr)
    );
    result
}
fn readelf(root: &Path, path: &Path, flags: &[&str]) -> String {
    let mut argv = vec!["readelf".into()];
    argv.extend(flags.iter().map(|flag| (*flag).into()));
    argv.push(path.display().to_string());
    String::from_utf8(
        run_task(TaskSpec::command(
            "readelf",
            "readelf",
            argv,
            root.display().to_string(),
        ))
        .stdout,
    )
    .unwrap()
}
#[test]
fn actual_gnu_section_gc_intersects_targets_and_reports_only_hidden_owned_functions() {
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
    for (path, text) in &sources {
        fs::write(root.path().join(path), text).unwrap();
    }
    fs::write(root.path().join("CMakeLists.txt"),"cmake_minimum_required(VERSION 3.16)\nproject(gc_fixture LANGUAGES CXX)\nadd_library(common OBJECT common.cpp)\nadd_executable(one one.cpp $<TARGET_OBJECTS:common>)\nadd_executable(two two.cpp $<TARGET_OBJECTS:common>)\n").unwrap();
    let plan = plan_linker_build(root.path(), &BuildRequest::default()).unwrap();
    assert!(!root.path().join(".ici").exists());
    let cancel = Cancellation::default();
    let lease = BuildLease::acquire(root.path(), &plan, &cancel, Duration::from_secs(2)).unwrap();
    lease.prepare(&plan).unwrap();
    let result = Scheduler::new(Runner::default(), 2)
        .run(&plan.tasks, &cancel)
        .unwrap();
    for outcome in result.outcomes {
        assert!(
            outcome.prerequisite_succeeded(),
            "{}",
            String::from_utf8_lossy(&outcome.stderr)
        );
    }
    let context = load_compilation_context(
        root.path(),
        &ContextRequest {
            database: Some(plan.compilation_database.clone()),
            selected_sources: sources.iter().map(|(path, _)| (*path).into()).collect(),
            ..Default::default()
        },
    );
    assert!(context.complete(), "{:?}", context.diagnostics);
    let commands = discover_link_commands(
        root.path(),
        &plan,
        &[PathBuf::from("/usr/bin/c++")],
        &context,
    )
    .unwrap();
    assert_eq!(commands.len(), 2);
    let mut targets = vec![];
    let mut removals = vec![];
    for command in &commands {
        let identity = run_task(linker_identity_task(root.path(), command));
        let banner = format!(
            "{}\n{}",
            String::from_utf8_lossy(&identity.stdout),
            String::from_utf8_lossy(&identity.stderr)
        );
        assert!(banner.contains("GNU ld"));
        let outcome = run_task(relink_task(root.path(), command));
        let discarded = format!(
            "{}\n{}",
            String::from_utf8_lossy(&outcome.stdout),
            String::from_utf8_lossy(&outcome.stderr)
        );
        removals
            .extend(ici_cpp::parse_discarded_sections(root.path(), command, &discarded).unwrap());
        targets.push(LinkerTargetEvidence {
            command_digest: command.digest.clone(),
            complete: true,
            linker_banner: banner,
            elf_header: readelf(root.path(), &root.path().join(&command.output), &["-Wh"]),
            discarded_output: discarded,
        });
    }
    assert!(
        removals
            .iter()
            .any(|removal| removal.section.contains("dead_everywhere")),
        "{removals:?}"
    );
    let mut objects = vec![];
    for unit in &context.units {
        let object = root.path().join(&unit.output);
        let headers = readelf(root.path(), &object, &["-WS"]);
        let symbols = readelf(root.path(), &object, &["-Ws"]);
        let groups = readelf(root.path(), &object, &["--section-groups"]);
        let mut locations = BTreeMap::new();
        for section in removals
            .iter()
            .filter(|removal| removal.object_path == unit.output)
            .map(|removal| &removal.section)
        {
            let result = run_task(TaskSpec::command(
                "locate",
                "addr2line",
                vec![
                    "addr2line".into(),
                    "-e".into(),
                    object.display().to_string(),
                    "-j".into(),
                    section.clone(),
                    "0".into(),
                ],
                root.path().join(&plan.shadow).display().to_string(),
            ));
            locations.insert(section.clone(), String::from_utf8(result.stdout).unwrap());
        }
        let bytes = fs::read(root.path().join(&unit.source)).unwrap();
        objects.push(LinkerObjectEvidence {
            object_path: unit.output.clone(),
            source: unit.source.clone(),
            source_digest: ici_domain::sha256(bytes),
            headers,
            symbols,
            groups,
            addr2line: locations,
        });
    }
    let request = AnalysisRequest {
        task_id: "linker-dead".into(),
        check_id: "cpp.dead".into(),
        component_id: "fixture".into(),
        language: "cpp".into(),
        inputs: sources
            .iter()
            .map(|(path, text)| SourceInput {
                path: (*path).into(),
                bytes: text.as_bytes().to_vec(),
            })
            .collect(),
        options: json!({}),
        target_version: "c++20".into(),
    };
    let output = analyze_linker_dead(
        &request,
        root.path(),
        &context,
        &commands,
        &targets,
        &objects,
    );
    assert_eq!(
        output.observation.state,
        TaskState::Succeeded,
        "{:?}",
        output.observation.limitations
    );
    assert_eq!(output.observation.findings.len(), 1, "{output:?}");
    assert!(output.targets.iter().any(|target| {
        target
            .metrics
            .get("symbol")
            .is_some_and(|symbol| symbol.as_str().unwrap().contains("dead_everywhere"))
    }));
    assert!(
        output.extra["sections_kept_by_another_target"]
            .as_u64()
            .unwrap()
            > 0
    );
    assert!(!output.targets.iter().any(|target| {
        target
            .metrics
            .get("symbol")
            .is_some_and(|symbol| symbol.as_str().unwrap().contains("exported_default"))
    }));
    targets.pop();
    assert_eq!(
        analyze_linker_dead(
            &request,
            root.path(),
            &context,
            &commands,
            &targets,
            &objects
        )
        .observation
        .state,
        TaskState::Failed
    );
}
#[test]
fn lto_comdat_weak_global_default_cloned_and_aliased_sections_are_excluded() {
    let headers = "  [ 1] .text._Z4deadv PROGBITS 00000000 000040 000010 00 AX 0 0 1\n";
    let local = "   1: 0000000000000000 16 FUNC LOCAL DEFAULT 1 _Z4deadv\n";
    let facts = parse_object_facts(headers, local, "").unwrap();
    assert_eq!(
        eligible_discarded_symbol(&facts, ".text._Z4deadv").as_deref(),
        Some("_Z4deadv")
    );
    for (symbols, groups) in [
        (local.replace("LOCAL", "GLOBAL"), String::new()),
        (
            local.replace("LOCAL", "WEAK").replace("DEFAULT", "HIDDEN"),
            String::new(),
        ),
        (local.replace("_Z4deadv", "_Z4deadv.cold"), String::new()),
        (format!("{local}{local}"), String::new()),
        (local.to_owned(), "[1] .text._Z4deadv".into()),
    ] {
        assert!(
            eligible_discarded_symbol(
                &parse_object_facts(headers, &symbols, &groups).unwrap(),
                ".text._Z4deadv"
            )
            .is_none()
        );
    }
    let lto = format!("{headers}  [ 2] .gnu.lto.fn PROGBITS 0 40 10 0 0 0 1\n");
    assert!(
        eligible_discarded_symbol(
            &parse_object_facts(&lto, local, "").unwrap(),
            ".text._Z4deadv"
        )
        .is_none()
    );
}
