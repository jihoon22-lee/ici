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
    for bytes in [vec![0xff], vec![0], vec![b' '; 16 * 1024 * 1024 + 1]] {
        assert!(
            legacy_main_definition_count(&SourceInput {
                path: "case.cpp".into(),
                bytes
            })
            .is_err()
        );
    }
}
