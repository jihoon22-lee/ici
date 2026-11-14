use ici_cpp::legacy_main_definition_count;
use ici_domain::SourceInput;

#[test]
fn frozen_direct_build_main_guard_ignores_literals_comments_and_prototypes() {
    for (text, expected) in [
        ("int main(){return 0;}", 1),
        ("int main(int argc,char **argv) noexcept {return argc;}", 1),
        ("int main(); int f(){return 0;}", 0),
        ("int main(){return 0;}\nint main(){return 1;}", 2),
        ("// int main(){}\\\n int main(){}\nint other(){}", 0),
        ("/* int main(){} */ int main(){}", 1),
        ("const char *s=\"int main(){}\";", 0),
        ("auto raw=u8R\"tag(int main(){})tag\"; int main(){}", 1),
        ("int a::main(){}; auto main()->int {return 0;}", 0),
        ("#define ENTRY int main(){}\n", 1),
        ("class X { int main(){} };", 1),
        ("int main() noexcept(true) {return 0;}", 1),
        ("// 😀\rint main(){}", 0),
    ] {
        assert_eq!(
            legacy_main_definition_count(&SourceInput {
                path: "case.cpp".into(),
                bytes: text.as_bytes().to_vec()
            })
            .unwrap(),
            expected,
            "{text:?}"
        );
    }
    // Frozen reads a NUL as text (build-literals-oracle.json inspect-nul compiles).
    assert_eq!(
        legacy_main_definition_count(&SourceInput {
            path: "case.cpp".into(),
            bytes: b"// nul\0\nint main(){return 0;}".to_vec()
        })
        .unwrap(),
        1
    );
    for bytes in [vec![0xff], vec![b' '; 16 * 1024 * 1024 + 1]] {
        assert!(
            legacy_main_definition_count(&SourceInput {
                path: "case.cpp".into(),
                bytes
            })
            .is_err()
        );
    }
}

/// Frozen `cpp_text.defines_main` (cpp_text.py:432-441): a source it cannot decode as UTF-8 is
/// not an entry point (it stays in coverage scope); a NUL is ordinary text.
#[test]
fn frozen_defines_main_keeps_undecodable_sources_in_scope() {
    assert!(ici_cpp::legacy_defines_main(b"int main(){return 0;}"));
    assert!(!ici_cpp::legacy_defines_main(b"int f(){return 0;}"));
    assert!(!ici_cpp::legacy_defines_main(
        b"// caf\xe9\nint main(){return 0;}"
    ));
    assert!(ici_cpp::legacy_defines_main(
        b"// nul\0\nint main(){return 0;}"
    ));
}
