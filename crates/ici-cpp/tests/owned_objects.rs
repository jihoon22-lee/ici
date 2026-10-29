use ici_cpp::{
    BuildLease, BuildRequest, ContextRequest, ReplayPurpose, load_compilation_context,
    owned_object_command, plan_build, replay_command,
};
use ici_domain::TaskSpec;
use ici_execution::{Cancellation, Runner};
use std::{fs, path::Path, time::Duration};

#[test]
fn owned_object_compile_preserves_build_warning_debug_policy_and_analysis_stays_read_only() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("main.cpp"),
        "int main(){int unused;return 0;}\n",
    )
    .unwrap();
    fs::write(root.path().join("compile_commands.json"),serde_json::to_vec(&serde_json::json!([{"directory":root.path(),"file":"main.cpp","arguments":["g++","-std=c++17","-Wall","-Wextra","-Werror","-g3","-fdiagnostics-column-unit=byte","-fdiagnostics-column-origin=1","-c","main.cpp","-o","must-not-create.o"]}])).unwrap()).unwrap();
    let context = load_compilation_context(
        root.path(),
        &ContextRequest {
            selected_sources: vec!["main.cpp".into()],
            ..Default::default()
        },
    );
    assert!(context.complete());
    let plan = plan_build(
        root.path(),
        &BuildRequest {
            // Any pure plan provides the fixed Rust-owned shadow namespace; nothing runs here.
            id: "direct".into(),
            project: ".".into(),
            ..Default::default()
        },
    )
    .unwrap();
    let output = root.path().join(&plan.shadow).join("unit.o");
    let mut unit = context.units[0].clone();
    let command =
        owned_object_command(root.path(), &unit, Path::new("/usr/bin/g++"), &output).unwrap();
    assert!(!root.path().join(".ici").exists());
    assert!(command.argv.iter().any(|argument| argument == "-Werror"));
    assert!(command.argv.iter().any(|argument| argument == "-g3"));
    assert!(
        !command
            .argv
            .iter()
            .any(|argument| argument == "-fsyntax-only")
    );
    let cancellation = Cancellation::default();
    let lease =
        BuildLease::acquire(root.path(), &plan, &cancellation, Duration::from_secs(3)).unwrap();
    lease.prepare(&plan).unwrap();
    let result = Runner::default().run(
        &TaskSpec::command(
            "compile",
            "g++",
            command.argv,
            command.cwd.display().to_string(),
        ),
        &cancellation,
    );
    assert!(result.completed());
    assert_eq!(result.exit_code, Some(1));
    assert!(!output.exists());
    unit.argv.retain(|argument| argument != "-Werror");
    let command =
        owned_object_command(root.path(), &unit, Path::new("/usr/bin/g++"), &output).unwrap();
    let result = Runner::default().run(
        &TaskSpec::command(
            "compile",
            "g++",
            command.argv,
            command.cwd.display().to_string(),
        ),
        &cancellation,
    );
    assert!(
        result.prerequisite_succeeded(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let before = fs::read(&output).unwrap();
    let syntax = replay_command(
        root.path(),
        &context.units[0],
        Path::new("/usr/bin/g++"),
        ReplayPurpose::Diagnostics,
    )
    .unwrap();
    assert!(!syntax.argv.iter().any(|argument| argument == "-Werror"));
    let result = Runner::default().run(
        &TaskSpec::command(
            "syntax",
            "g++",
            syntax.argv,
            syntax.cwd.display().to_string(),
        ),
        &cancellation,
    );
    assert!(result.prerequisite_succeeded());
    assert_eq!(fs::read(&output).unwrap(), before);
    assert!(!root.path().join("must-not-create.o").exists());
    assert!(
        owned_object_command(
            root.path(),
            &unit,
            Path::new("/usr/bin/g++"),
            &root.path().join("user.o")
        )
        .is_err()
    );
    unit.argv
        .insert(1, "-fplugin=/tmp/ici-must-not-load.so".into());
    assert!(owned_object_command(root.path(), &unit, Path::new("/usr/bin/g++"), &output).is_err());
}
