use ici_cpp::{
    ContextRequest, GccStdlibProjection, clang_tidy_boundary_task, gcc_include_search_tasks,
    gcc_standard_library_projection, load_compilation_context, parse_clang_tidy_boundaries,
    parse_compiler_include_search,
};
use ici_domain::{SourceInput, TaskState};
use ici_execution::{Cancellation, Runner};
use serde_json::json;
use std::{
    fs,
    path::{Path, PathBuf},
};
fn fixture(root: &Path) -> ici_cpp::CompilationContext {
    fs::write(
        root.join("main.cpp"),
        include_bytes!("../../../parity-fixtures/cpp/clang-boundaries.cpp"),
    )
    .unwrap();
    fs::write(root.join("compile_commands.json"),json!([{"directory":root,"file":"main.cpp","arguments":["g++","-std=c++20","-c","main.cpp","-o","must-not-write.o"]}]).to_string()).unwrap();
    load_compilation_context(
        root,
        &ContextRequest {
            selected_sources: vec!["main.cpp".into()],
            ..Default::default()
        },
    )
}
#[test]
fn actual_gcc_c_and_cpp_include_difference_pins_observed_stdlib_without_guessing_paths() {
    let root = tempfile::tempdir().unwrap();
    let context = fixture(root.path());
    let tasks = gcc_include_search_tasks(root.path(), &context.units[0], Path::new("/usr/bin/g++"))
        .unwrap();
    assert_eq!(tasks.len(), 2);
    let results: Vec<_> = tasks
        .iter()
        .map(|task| Runner::default().run(task, &Cancellation::default()))
        .collect();
    let projection =
        gcc_standard_library_projection(&results[0], &results[1], root.path()).unwrap();
    assert!(projection.gnu_driver);
    assert_eq!(projection.arguments[0], "-nostdinc++");
    assert!(projection.arguments.len() > 1);
    let roots: Vec<_> = projection.arguments[1..]
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| PathBuf::from(&pair[1]))
        .collect();
    assert!(roots.iter().all(|path|projection.cpp_search.contains(path)&&!projection.c_search.contains(path)));
    assert!(!root.path().join("must-not-write.o").exists());
    let mut truncated = results[0].clone();
    truncated.truncated = true;
    assert!(gcc_standard_library_projection(&truncated, &results[1], root.path()).is_err());
    let mut explicit = context.units[0].clone();
    explicit.argv.insert(1, "-nostdinc++".into());
    assert!(
        gcc_include_search_tasks(root.path(), &explicit, Path::new("/usr/bin/g++"))
            .unwrap()
            .is_empty()
    );
}
#[test]
fn include_search_rejects_duplicate_blocks_relative_root_and_unavailable_directories() {
    let root = tempfile::tempdir().unwrap();
    let valid = format!(
        "#include <...> search starts here:\n {}\nEnd of search list.\n",
        root.path().display()
    );
    assert_eq!(
        parse_compiler_include_search(&valid, root.path()).unwrap(),
        [root.path()]
    );
    for text in [
        format!("{valid}{valid}"),
        "#include <...> search starts here:\n relative/path\nEnd of search list.\n".into(),
        "#include <...> search starts here:\n /\nEnd of search list.\n".into(),
        "#include <...> search starts here:\n /ici-missing-search-root\nEnd of search list.\n"
            .into(),
    ] {
        assert!(parse_compiler_include_search(&text, root.path()).is_err());
    }
}
#[test]
fn actual_clang_tidy21_golden_preserves_size_notes_and_rejects_suppressed_or_unrecognized_evidence()
{
    let source = SourceInput {
        path: "main.cpp".into(),
        bytes: include_bytes!("../../../parity-fixtures/cpp/clang-boundaries.cpp").to_vec(),
    };
    let transcript = include_str!("../../../tests/rust-parity/cpp/clang-tidy-boundaries.txt");
    let facts = parse_clang_tidy_boundaries(
        transcript,
        "",
        &source,
        Path::new("/fixture/main.cpp"),
        "debug",
    )
    .unwrap();
    assert_eq!(facts.compiler.boundaries.len(), 4);
    assert_eq!(facts.compiler.macro_definitions_excluded, 1);
    let ordinary = facts
        .metrics
        .iter()
        .find(|metric| metric.name == "ordinary")
        .unwrap();
    assert_eq!(ordinary.statements, 9);
    assert_eq!(ordinary.parameters, 1);
    assert_eq!(ordinary.lines, 0);
    assert!(
        parse_clang_tidy_boundaries(
            transcript,
            "Suppressed 1 warning (1 NOLINT).",
            &source,
            Path::new("/fixture/main.cpp"),
            "debug"
        )
        .is_err()
    );
    assert!(
        parse_clang_tidy_boundaries(
            transcript,
            "unrecognized record",
            &source,
            Path::new("/fixture/main.cpp"),
            "debug"
        )
        .is_err()
    );
}
#[test]
fn optional_actual_tidy_probe_uses_inline_config_and_observed_gcc_stdlib() {
    let tidy = std::env::var_os("ICI_TEST_CLANG_TIDY")
        .map(PathBuf::from)
        .or_else(|| {
            ["/usr/bin/clang-tidy-21", "/usr/bin/clang-tidy"]
                .iter()
                .map(PathBuf::from)
                .find(|path| path.is_file())
        });
    let Some(tidy) = tidy else {
        assert!(
            std::env::var_os("ICI_REQUIRE_BUILD_ADAPTERS").is_none(),
            "ICI_REQUIRE_BUILD_ADAPTERS requires ICI_TEST_CLANG_TIDY or installed clang-tidy"
        );
        eprintln!(
            "SKIP actual clang-tidy boundary replay: tool unavailable; frozen actual Clang21 size notes still tested"
        );
        return;
    };
    let root = tempfile::tempdir().unwrap();
    let context = fixture(root.path());
    let tasks = gcc_include_search_tasks(root.path(), &context.units[0], Path::new("/usr/bin/g++"))
        .unwrap();
    let results: Vec<_> = tasks
        .iter()
        .map(|task| Runner::default().run(task, &Cancellation::default()))
        .collect();
    let projection =
        gcc_standard_library_projection(&results[0], &results[1], root.path()).unwrap();
    let mut task = clang_tidy_boundary_task(
        root.path(),
        &context.units[0],
        Path::new("/usr/bin/g++"),
        &tidy,
        &projection,
    )
    .unwrap();
    if let Ok(paths) = std::env::var("ICI_TEST_CLANG_LIBRARY_PATH") {
        task.env_overlay.insert("LD_LIBRARY_PATH".into(), paths);
    }
    assert!(
        task.argv
            .iter()
            .any(|argument| argument.starts_with("--config="))
    );
    assert!(task.argv.contains(&"-nostdinc++".into()));
    assert!(!root.path().join(".ici").exists());
    let result = Runner::default().run(&task, &Cancellation::default());
    assert_eq!(result.state, TaskState::Succeeded, "{:?}", result.error);
    assert_eq!(
        result.exit_code,
        Some(0),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let facts = parse_clang_tidy_boundaries(
        &String::from_utf8(result.stdout).unwrap(),
        &String::from_utf8(result.stderr).unwrap(),
        &SourceInput {
            path: "main.cpp".into(),
            bytes: fs::read(root.path().join("main.cpp")).unwrap(),
        },
        &root.path().join("main.cpp"),
        &context.units[0].configuration,
    )
    .unwrap();
    assert_eq!(facts.compiler.boundaries.len(), 4);
    assert!(!root.path().join("must-not-write.o").exists());
    let invalid = GccStdlibProjection {
        arguments: vec!["-fplugin=evil.so".into()],
        ..Default::default()
    };
    assert!(
        clang_tidy_boundary_task(
            root.path(),
            &context.units[0],
            Path::new("/usr/bin/g++"),
            &tidy,
            &invalid
        )
        .is_err()
    );
}
