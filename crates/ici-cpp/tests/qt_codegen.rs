//! Frozen `analysis/_qt_codegen.py` helper rules against the development-only oracle
//! `tests/rust-parity/cpp/qt-lexical-oracle.json` (`capture_qt_lexical.py`).
use ici_cpp::{CompilationUnit, Define, IncludePath};
use serde_json::Value;

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/qt-lexical-oracle.json"
    ))
    .unwrap()
}

fn unit(case: &Value) -> CompilationUnit {
    let strings = |value: &Value| -> Vec<String> {
        value
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item.as_str().unwrap().to_owned())
            .collect()
    };
    CompilationUnit {
        source: "a.cpp".into(),
        directory: ".".into(),
        original_argv: strings(&case["argv"]),
        argv: strings(&case["argv"]),
        compiler: "g++".into(),
        language: "c++".into(),
        standard: String::new(),
        output: String::new(),
        target: String::new(),
        variant: String::new(),
        defines: case["defines"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|define| Define {
                name: define[0].as_str().unwrap().into(),
                value: define[1].as_str().map(str::to_owned),
                undefine: false,
            })
            .collect(),
        include_paths: case["include_paths"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|path| IncludePath {
                path: path[0].as_str().unwrap().into(),
                kind: path[1].as_str().unwrap().into(),
                scope: "project".into(),
                present: true,
            })
            .collect(),
        sysroot: String::new(),
        sysroot_scope: String::new(),
        configuration: String::new(),
        response_files: vec![],
        diagnostics: vec![],
    }
}

#[test]
fn qt_masker_q_object_lines_and_include_names_match_frozen() {
    let oracle = oracle();
    let texts = oracle["texts"].as_array().unwrap();
    assert_eq!(texts.len(), 31);
    let mut corrected = 0;
    for case in texts {
        let text = case["text"].as_str().unwrap();
        assert_eq!(
            ici_cpp::qt_cpp_code(text),
            case["code"].as_str().unwrap(),
            "{text:?}"
        );
        assert_eq!(
            ici_cpp::q_object_line(text)
                .map(Value::from)
                .unwrap_or(Value::Null),
            case["q_object_line"],
            "{text:?}"
        );
        let names = ici_cpp::qt_include_names(text);
        if let Some(error) = case["include_names"]["error"].as_str() {
            // Known frozen defect, corrected: a lone line boundary inside a comment or literal
            // crashes frozen `zip(strict=True)`; native reads the include from its own line.
            assert!(error.starts_with("ValueError: zip()"), "{text:?}");
            let expected: &[&str] = if text.contains("c.h") {
                &["c.h"]
            } else {
                &["f.h"]
            };
            assert_eq!(names, expected, "{text:?}");
            corrected += 1;
        } else {
            assert_eq!(
                Value::from(names),
                case["include_names"]["value"],
                "{text:?}"
            );
        }
    }
    assert_eq!(corrected, 2);
}

#[test]
fn qt_include_names_and_major_metadata_match_frozen() {
    let oracle = oracle();
    for (name, expected) in oracle["names"].as_object().unwrap() {
        assert_eq!(
            ici_cpp::is_qt_include(name),
            expected.as_bool().unwrap(),
            "{name}"
        );
    }
    let units = oracle["units"].as_array().unwrap();
    assert_eq!(units.len(), 15);
    for case in units {
        let unit = unit(case);
        let majors: Vec<u8> = ici_cpp::qt_major(&unit).into_iter().collect();
        assert_eq!(Value::from(majors), case["majors"], "{case}");
        assert_eq!(
            ici_cpp::qt_metadata_relevant(&unit),
            case["metadata_relevant"].as_bool().unwrap(),
            "{case}"
        );
    }
}
