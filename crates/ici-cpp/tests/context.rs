use ici_cpp::{
    ContextRequest, ReplayPurpose, load_compilation_context, replay_command, split_posix,
    split_windows,
};
use serde_json::json;
use std::{fs, path::Path};

fn database(root: &Path, rows: serde_json::Value) {
    fs::write(
        root.join("compile_commands.json"),
        serde_json::to_vec(&rows).unwrap(),
    )
    .unwrap();
}
fn row(root: &Path, argv: &[&str]) -> serde_json::Value {
    json!({"directory":root,"file":"main.cpp","arguments":argv})
}
fn request() -> ContextRequest {
    ContextRequest {
        selected_sources: vec!["main.cpp".into()],
        ..Default::default()
    }
}

#[test]
fn configuration_identity_survives_relocation_but_preserves_external_compiler_flags_and_variants() {
    let first = tempfile::tempdir().unwrap();
    let second = tempfile::tempdir().unwrap();
    let external = tempfile::tempdir().unwrap();
    let build = |root: &Path, include: &Path, compiler: &str, variant: &str, define: &str| {
        fs::write(root.join("main.cpp"), "int value;\n").unwrap();
        fs::write(root.join("forced.hpp"), "#pragma once\n").unwrap();
        for path in [
            root.join("include"),
            root.join("build/generated"),
            root.join("sysroot"),
        ] {
            fs::create_dir_all(path).unwrap();
        }
        let argv = vec![
            compiler.to_owned(),
            "-I".into(),
            include.to_string_lossy().into_owned(),
            format!("-isystem{}", root.join("build/generated").display()),
            "-include".into(),
            root.join("forced.hpp").to_string_lossy().into_owned(),
            format!("--sysroot={}", root.join("sysroot").display()),
            format!("-DROOT=\"{}\"", root.join("assets").display()),
            format!("-DLABEL={define}"),
            format!("-ffile-prefix-map={}=/source", root.display()),
            "-c".into(),
            root.join("main.cpp").to_string_lossy().into_owned(),
            "-o".into(),
            root.join("build/main.o").to_string_lossy().into_owned(),
        ];
        database(
            root,
            json!([{"directory":root,"file":root.join("main.cpp"),"arguments":argv}]),
        );
        let context = load_compilation_context(
            root,
            &ContextRequest {
                variant: Some(variant.into()),
                ..request()
            },
        );
        assert!(context.complete(), "{:?}", context);
        context.units[0].configuration.clone()
    };
    let one = build(
        first.path(),
        &first.path().join("include"),
        "/compiler/version15/g++",
        "release",
        "literal",
    );
    let two = build(
        second.path(),
        &second.path().join("include"),
        "/compiler/version15/g++",
        "release",
        "literal",
    );
    assert_eq!(one, two);
    assert_ne!(
        one,
        build(
            second.path(),
            external.path(),
            "/compiler/version15/g++",
            "release",
            "literal"
        )
    );
    assert_ne!(
        one,
        build(
            second.path(),
            &second.path().join("include"),
            "/compiler/version16/g++",
            "release",
            "literal"
        )
    );
    assert_ne!(
        one,
        build(
            second.path(),
            &second.path().join("include"),
            "/compiler/version15/g++",
            "coverage",
            "literal"
        )
    );
    assert_ne!(
        one,
        build(
            second.path(),
            &second.path().join("include"),
            "/compiler/version15/g++",
            "release",
            "changed"
        )
    );
}

#[test]
fn original_and_expanded_arguments_keep_macro_include_variant_and_response_identity() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    fs::create_dir(root.path().join("rsp")).unwrap();
    fs::create_dir(root.path().join("include")).unwrap();
    fs::write(
        root.path().join("rsp/outer.rsp"),
        "@inner.rsp -DNAME='two words' -Iinclude -isystem /usr/include -std=c++20\n",
    )
    .unwrap();
    fs::write(root.path().join("inner.rsp"), "-c main.cpp -o object.o\n").unwrap();
    database(
        root.path(),
        json!([row(root.path(), &["g++", "@rsp/outer.rsp"])]),
    );
    let context = load_compilation_context(
        root.path(),
        &ContextRequest {
            variant: Some("debug".into()),
            ..request()
        },
    );
    assert!(context.complete(), "{:?}", context.diagnostics);
    let unit = &context.units[0];
    assert_eq!(unit.original_argv, ["g++", "@rsp/outer.rsp"]);
    assert!(unit.argv.contains(&"-DNAME=two words".into()));
    assert_eq!(unit.standard, "c++20");
    assert_eq!(unit.variant, "debug");
    assert_eq!(unit.response_files.len(), 2);
    assert_eq!(unit.defines[0].value.as_deref(), Some("two words"));
    assert!(
        unit.include_paths
            .iter()
            .any(|include| include.scope == "external" && include.kind == "system")
    );
    let old = unit.configuration.clone();
    fs::write(
        root.path().join("inner.rsp"),
        "-DNEW=1 -c main.cpp -o object.o\n",
    )
    .unwrap();
    assert_ne!(
        load_compilation_context(root.path(), &request()).units[0].configuration,
        old
    );
}
#[test]
fn explicit_missing_database_and_ambiguity_never_fall_back_to_a_different_variant() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    database(
        root.path(),
        json!([row(root.path(), &["g++", "-c", "main.cpp"])]),
    );
    fs::create_dir(root.path().join("build")).unwrap();
    fs::write(root.path().join("build/compile_commands.json"), "[]").unwrap();
    let ambiguous = load_compilation_context(root.path(), &request());
    assert_eq!(ambiguous.origin, "ambiguous");
    assert!(!ambiguous.complete());
    let missing = load_compilation_context(
        root.path(),
        &ContextRequest {
            database: Some("missing.json".into()),
            ..request()
        },
    );
    assert_eq!(missing.origin, "configured");
    assert!(missing.units.is_empty());
    assert!(
        missing
            .diagnostics
            .iter()
            .any(|d| d.code == "database-missing")
    );
    let chosen = load_compilation_context(
        root.path(),
        &ContextRequest {
            database: Some("compile_commands.json".into()),
            ..request()
        },
    );
    assert!(chosen.complete());
}
#[test]
fn malformed_duplicate_keys_arguments_and_response_cycles_are_explicit_incomplete_evidence() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    fs::write(
        root.path().join("compile_commands.json"),
        "[{\"file\":\"a.cpp\",\"file\":\"b.cpp\"}]",
    )
    .unwrap();
    assert!(
        load_compilation_context(root.path(), &request())
            .diagnostics
            .iter()
            .any(|d| d.code == "database-malformed")
    );
    let mut invalid = row(root.path(), &["g++", "main.cpp"]);
    invalid["arguments"] = json!(false);
    invalid["command"] = json!("g++ -c main.cpp");
    database(root.path(), json!([invalid]));
    assert!(
        load_compilation_context(root.path(), &request())
            .units
            .is_empty()
    );
    fs::write(root.path().join("loop.rsp"), "@loop.rsp").unwrap();
    database(
        root.path(),
        json!([row(root.path(), &["g++", "@loop.rsp"])]),
    );
    let cycle = load_compilation_context(root.path(), &request());
    assert!(!cycle.complete());
    assert!(
        cycle.units[0]
            .diagnostics
            .iter()
            .any(|d| d.code == "response-file-cycle")
    );
}
#[test]
fn source_coverage_missing_forced_inputs_and_configuration_duplicates_remain_visible() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    fs::write(root.path().join("other.cpp"), "int other;\n").unwrap();
    database(
        root.path(),
        json!([
            row(root.path(), &["g++", "-DVARIANT=1", "-c", "main.cpp"]),
            row(root.path(), &["g++", "-DVARIANT=2", "-c", "main.cpp"])
        ]),
    );
    let context = load_compilation_context(
        root.path(),
        &ContextRequest {
            selected_sources: vec!["main.cpp".into(), "other.cpp".into()],
            ..Default::default()
        },
    );
    assert_eq!(context.units.len(), 2);
    assert_ne!(
        context.units[0].configuration,
        context.units[1].configuration
    );
    assert_eq!(context.uncovered_sources, ["other.cpp"]);
    assert!(!context.complete());
}
#[test]
fn replay_drops_only_outputs_demotes_errors_and_rejects_code_loading_or_unknown_operands() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("main.cpp"),
        "#ifndef FLAG\n#error missing\n#endif\nint main(){return 0;}\n",
    )
    .unwrap();
    database(
        root.path(),
        json!([row(
            root.path(),
            &[
                "g++",
                "-DFLAG=1",
                "-std=c++20",
                "-Wall",
                "-Werror",
                "-Werror=unused-variable",
                "-c",
                "main.cpp",
                "-o",
                "must-not-write.o",
                "-MMD",
                "-MF",
                "must-not-write.d"
            ]
        )]),
    );
    let context = load_compilation_context(root.path(), &request());
    let unit = &context.units[0];
    let replay = replay_command(
        root.path(),
        unit,
        Path::new("/usr/bin/g++"),
        ReplayPurpose::Diagnostics,
    )
    .unwrap();
    assert!(
        !replay
            .argv
            .iter()
            .any(|arg| arg == "-Werror" || arg == "-o" || arg == "-MF" || arg == "-c")
    );
    assert!(replay.argv.contains(&"-Wunused-variable".into()));
    let output = std::process::Command::new(&replay.argv[0])
        .args(&replay.argv[1..])
        .current_dir(&replay.cwd)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(!root.path().join("must-not-write.o").exists());
    assert!(!root.path().join("must-not-write.d").exists());
    for unsafe_arg in [
        "-fplugin=evil.so",
        "-specs=evil.specs",
        "-Xclang",
        "-fprofile-generate",
        "--unknown-ici-option",
    ] {
        let mut bad = unit.clone();
        bad.argv.insert(1, unsafe_arg.into());
        assert!(
            replay_command(
                root.path(),
                &bad,
                Path::new("/usr/bin/g++"),
                ReplayPurpose::Diagnostics
            )
            .is_err(),
            "{unsafe_arg}"
        );
    }
}
#[test]
fn string_tokenizers_preserve_literal_quotes_without_executing_shell_or_expanding_variables() {
    assert_eq!(
        split_posix("g++ -DNAME='two words' \"a b.cpp\" '$HOME' `touch` \\\"quote\\\"").unwrap(),
        [
            "g++",
            "-DNAME=two words",
            "a b.cpp",
            "$HOME",
            "`touch`",
            "\"quote\""
        ]
    );
    assert!(split_posix("g++ 'unterminated").is_err());
    assert_eq!(
        split_windows("cl.exe /DNAME=\"two words\" \"C:\\source path\\a.cpp\"").unwrap(),
        ["cl.exe", "/DNAME=two words", "C:\\source path\\a.cpp"]
    );
}

#[test]
fn replay_preserves_source_position_and_driver_language_before_trailing_language_changes() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("main.c"),
        "template<class T>T identity(T value){return value;}\nint main(){return identity(0);}\n",
    )
    .unwrap();
    database(
        root.path(),
        json!([{"directory":root.path(),"file":"main.c","arguments":["g++","-x","c++","main.c","-x","c","-c","-o","must-not-write.o"]}]),
    );
    let context = load_compilation_context(
        root.path(),
        &ContextRequest {
            selected_sources: vec!["main.c".into()],
            ..Default::default()
        },
    );
    assert!(context.complete(), "{:?}", context.diagnostics);
    assert_eq!(context.units[0].language, "c++");
    let replay = replay_command(
        root.path(),
        &context.units[0],
        Path::new("/usr/bin/g++"),
        ReplayPurpose::Diagnostics,
    )
    .unwrap();
    let source = replay.source.display().to_string();
    let source_index = replay
        .argv
        .iter()
        .position(|argument| argument == &source)
        .unwrap();
    let last_language = replay
        .argv
        .iter()
        .rposition(|argument| argument == "-x")
        .unwrap();
    assert!(source_index < last_language);
    let output = std::process::Command::new(&replay.argv[0])
        .args(&replay.argv[1..])
        .current_dir(&replay.cwd)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(!root.path().join("must-not-write.o").exists());
}

#[test]
fn a_unity_filename_does_not_prove_that_an_unrelated_selected_source_was_compiled() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("unity_empty.cpp"), "int value;\n").unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    database(
        root.path(),
        json!([{"directory":root.path(),"file":"unity_empty.cpp","arguments":["g++","-c","unity_empty.cpp"]}]),
    );
    let context = load_compilation_context(root.path(), &request());
    assert!(context.unity_build);
    assert_eq!(context.uncovered_sources, ["main.cpp"]);
    assert!(!context.complete());
}
