use ici_cpp::{ElfPolicy, abi_violations, parse_readelf};
use ici_domain::TaskSpec;
use ici_execution::{Cancellation, Runner};
use std::{fs, path::Path};
fn invoke(argv: Vec<String>, cwd: &Path) -> String {
    let task = TaskSpec::command("elf-fixture", "fixture", argv, cwd.display().to_string());
    let result = Runner::default().run(&task, &Cancellation::default());
    assert!(
        result.prerequisite_succeeded(),
        "{:?}\n{}",
        result.error,
        String::from_utf8_lossy(&result.stderr)
    );
    String::from_utf8(result.stdout).unwrap()
}
#[test]
fn actual_dynamic_and_static_elf_keep_dependencies_versions_loader_paths_and_static_evidence() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("main.cpp"),
        "#include <cstdio>\nint main(){std::puts(\"fixture\");return 0;}\n",
    )
    .unwrap();
    for (name, static_link) in [("dynamic", false), ("static", true)] {
        let mut argv = vec!["g++".into(), "main.cpp".into(), "-o".into(), name.into()];
        if static_link {
            argv.push("-static".into());
        } else {
            argv.push(format!(
                "-Wl,-rpath,{}/lib:$ORIGIN/lib",
                root.path().display()
            ));
        }
        invoke(argv, root.path());
        let transcript = invoke(
            vec![
                "readelf".into(),
                "-h".into(),
                "-S".into(),
                "-d".into(),
                "-V".into(),
                "--wide".into(),
                name.into(),
            ],
            root.path(),
        );
        let facts = parse_readelf(&transcript).unwrap();
        assert_eq!(facts.elf_class, "ELF64");
        assert!(!facts.stripped);
        assert_eq!(facts.dynamic, !static_link);
        if static_link {
            assert!(facts.needed.is_empty());
            assert!(
                abi_violations(
                    &facts,
                    &ElfPolicy {
                        require_static: true,
                        ..Default::default()
                    },
                    &[]
                )
                .unwrap()
                .is_empty()
            );
        } else {
            assert!(facts.needed.contains(&"libc.so.6".into()));
            assert!(!facts.glibc.is_empty());
            assert!(facts.runpath.contains(&"$ORIGIN/lib".into()));
            let violations = abi_violations(
                &facts,
                &ElfPolicy {
                    require_static: true,
                    max_glibc: "2.1".into(),
                    forbidden_needed: vec!["libc.so.6".into()],
                    ..Default::default()
                },
                &[root.path().to_path_buf()],
            )
            .unwrap();
            for rule in [
                "dynamic-linkage",
                "glibc-floor",
                "forbidden-rpath",
                "build-path-leak",
                "forbidden-dependency",
            ] {
                assert!(
                    violations.iter().any(|item| item.rule_id.ends_with(rule)),
                    "{rule}: {violations:?}"
                );
            }
        }
    }
}
#[test]
fn empty_needed_is_not_static_and_abi_versions_compare_numerically() {
    let facts=parse_readelf("Class: ELF64\nMachine: Advanced Micro Devices X86-64\nType: EXEC (Executable file)\nGLIBC_2.9 GLIBC_2.10 GLIBCXX_3.4.9 GLIBCXX_3.4.30\n").unwrap();
    assert!(facts.dynamic);
    assert_eq!(facts.glibc, ["2.9", "2.10"]);
    assert_eq!(facts.glibcxx, ["3.4.9", "3.4.30"]);
    let violations = abi_violations(
        &facts,
        &ElfPolicy {
            max_glibc: "2.9".into(),
            max_glibcxx: "3.4.9".into(),
            expected_class: "ELF32".into(),
            expected_machine: "AArch64".into(),
            ..Default::default()
        },
        &[],
    )
    .unwrap();
    assert_eq!(violations.len(), 4);
    assert!(parse_readelf("Class: ELF64\nClass: ELF32\nMachine: X\nType: EXEC\n").is_err());
    assert!(parse_readelf("not an ELF transcript").is_err());
    assert!(parse_readelf("Class: ELF64\0\nMachine: X\nType: EXEC\n").is_err());
}
