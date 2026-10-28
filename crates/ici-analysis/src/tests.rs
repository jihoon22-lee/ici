use super::*;
use ici_domain::SourceInput;
use serde_json::{Value, json};

fn request(check: &str, text: &str) -> AnalysisRequest {
    AnalysisRequest {
        task_id: format!("task:{check}"),
        check_id: format!("python.{check}"),
        component_id: "project".into(),
        language: "python".into(),
        inputs: vec![SourceInput {
            path: "main.py".into(),
            bytes: text.as_bytes().to_vec(),
        }],
        options: json!({}),
        target_version: "3.14".into(),
    }
}

#[test]
fn dead_code_matches_frozen_targets_and_reviewed_corrections() {
    let fixture: Value =
        serde_json::from_str(include_str!("../../../tests/rust-parity/python/dead.json")).unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let mut input = request("dead", "");
        input.inputs = case["sources"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(path, text)| SourceInput {
                path: path.clone(),
                bytes: text.as_str().unwrap().as_bytes().to_vec(),
            })
            .collect();
        input.options = json!({"source_roots":case["source_roots"]});
        let output = analyze(&input);
        if !case["original_errors"].as_array().unwrap().is_empty() {
            assert_eq!(
                output.observation.state,
                TaskState::Failed,
                "{}",
                case["id"]
            );
            assert_eq!(output.status, "ERROR");
            assert!(output.targets.iter().any(|target| target.status == "ERROR"));
            assert!(output.observation.findings.is_empty());
            continue;
        }
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{}: {:?}",
            case["id"],
            output.observation.limitations
        );
        let mut actual: Vec<Value> = output
            .targets
            .iter()
            .map(|target| {
                json!({
                    "file_path":target.file_path,"start_line":target.start_line,
                    "end_line":target.end_line,"start_column":target.start_column,
                    "end_column":target.end_column,"target_name":target.target_name,
                    "status":target.status,"message":target.message,"snippet":target.snippet,
                })
            })
            .collect();
        let mut expected = case
            .get("expected_targets")
            .unwrap_or(&case["original_targets"])
            .as_array()
            .unwrap()
            .clone();
        let key = |value: &Value| {
            (
                value["file_path"].as_str().unwrap().to_owned(),
                value["start_line"].as_u64().unwrap(),
                value["target_name"].as_str().unwrap().to_owned(),
            )
        };
        actual.sort_by_key(key);
        expected.sort_by_key(key);
        assert_eq!(actual, expected, "{}", case["id"]);
        let warnings = output
            .targets
            .iter()
            .filter(|target| target.status == "WARN")
            .count();
        assert_eq!(
            output.observation.findings.len(),
            warnings,
            "{}",
            case["id"]
        );
        for finding in &output.observation.findings {
            assert_eq!(finding.evidence, ici_domain::EvidenceLevel::Estimated);
            assert_eq!(finding.severity, "medium");
            assert_eq!(finding.category, "maintainability");
        }
    }
}

#[test]
fn explicit_annotation_projection_preserves_optin_and_nested_definitions() {
    use ici_domain::Validate;
    let fixtures: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/annotations.json"
    ))
    .unwrap();
    for case in fixtures["cases"].as_array().unwrap() {
        let mut input = request("type", case["source"].as_str().unwrap());
        input.options = json!({"legacy_projection":true,"warn_on_missing_annotation":case["warn_on_missing_annotation"]});
        let output = analyze(&input);
        output
            .observation
            .validate()
            .expect("annotation findings must be usable by the shared application/cache");
        if case["incomplete"] == true {
            assert_eq!(output.observation.state, TaskState::Failed);
            assert_eq!(output.status, "ERROR");
            continue;
        }
        let mut actual: Vec<_> = output.targets.iter().filter(|target| target.status=="WARN").map(|target| json!({"line":target.start_line,"name":target.target_name,"status":target.status,"message":target.message})).collect();
        let mut expected = case["original_targets"].as_array().unwrap().clone();
        actual.sort_by_key(|row| row["line"].as_u64());
        expected.sort_by_key(|row| row["line"].as_u64());
        assert_eq!(actual, expected, "{}", case["id"]);
        assert!(
            output
                .targets
                .iter()
                .any(|target| target.target_name == "AnnotationCoverage")
        );
        assert_eq!(output.observation.findings.len(), actual.len());
    }
    let output = analyze(&request("type", "def f():\n    pass\n"));
    assert_eq!(output.observation.state, TaskState::Failed);
    assert!(
        output.targets[0]
            .message
            .contains("explicit legacy_projection")
    );
}

#[test]
fn static_compatibility_matches_fixed_floor_inventory() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/compat-static.json"
    ))
    .unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let mut input = request("compat", case["source"].as_str().unwrap());
        input.options = json!({"target_version":case["target"]});
        let output = analyze(&input);
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{}: {:?}",
            case["id"],
            output.observation.limitations
        );
        let actual:Vec<_>=output.targets.iter().filter(|target|target.status=="WARN").map(|target|json!({"start_line":target.start_line,"end_line":target.end_line,"start_column":target.start_column,"end_column":target.end_column,"target_name":target.target_name,"status":target.status,"message":target.message})).collect();
        let mut actual = actual;
        actual.sort_by_key(|row| (row["start_line"].as_u64(), row["start_column"].as_u64()));
        let expected = case
            .get("expected_targets")
            .unwrap_or(&case["original_targets"]);
        assert_eq!(json!(actual), *expected, "{}", case["id"]);
        assert_eq!(
            output.extra["python_ast_nodes"], case["ast_nodes"],
            "{}",
            case["id"]
        );
    }
}

#[test]
fn shared_type2_matcher_preserves_frozen_groups_and_raw_denominators() {
    let fixture: Value =
        serde_json::from_str(include_str!("../../../tests/rust-parity/python/type2.json")).unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let files: Vec<_> = case["files"]
            .as_array()
            .unwrap()
            .iter()
            .map(|file| duplicates::DuplicateFileData {
                file_path: file["file_path"].as_str().unwrap().into(),
                language: file["language"].as_str().unwrap().into(),
                raw_text: file["raw_text"].as_str().unwrap().into(),
                indexed: file["indexed"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|row| {
                        (
                            row[0].as_u64().unwrap() as u32,
                            row[1].as_str().unwrap().into(),
                        )
                    })
                    .collect(),
                regions: file["regions"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|row| {
                        (
                            row[0].as_u64().unwrap() as u32,
                            row[1].as_u64().unwrap() as u32,
                        )
                    })
                    .collect(),
            })
            .collect();
        let mut input = request("dup", "");
        input.options = json!({"window_size":case["window_size"]});
        let output = duplicates::analyze_type2(&input, &files);
        let mut pipeline = input.clone();
        pipeline.inputs = files
            .iter()
            .map(|file| SourceInput {
                path: file.file_path.clone(),
                bytes: file.raw_text.as_bytes().to_vec(),
            })
            .collect();
        pipeline.options["python_semantic"] = json!("off");
        assert_eq!(
            analyze(&pipeline).extra["clone_groups"],
            case["expected_groups"],
            "{} native Python dispatch",
            case["id"]
        );
        for file in &files {
            let source = SourceInput {
                path: file.file_path.clone(),
                bytes: file.raw_text.as_bytes().to_vec(),
            };
            let parsed =
                ParsedFile::parse(SourceDocument::decode(&source).unwrap(), "3.14").unwrap();
            let normalized = python_duplicates::normalize(&parsed, None).unwrap();
            assert_eq!(
                normalized.indexed, file.indexed,
                "{}:{} lexical records",
                case["id"], file.file_path
            );
            for first in 0..file.regions.len() {
                for second in first..file.regions.len() {
                    assert_eq!(
                        normalized.regions[first] == normalized.regions[second],
                        file.regions[first] == file.regions[second],
                        "{}:{} region partition {first}/{second}",
                        case["id"],
                        file.file_path
                    );
                }
            }
        }
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{}: {:?}",
            case["id"],
            output.observation.limitations
        );
        assert_eq!(
            output.extra["clone_groups"], case["expected_groups"],
            "{}",
            case["id"]
        );
        assert_eq!(
            output.extra["duplicated_lines"], case["duplicated_lines"],
            "{}",
            case["id"]
        );
        assert_eq!(
            output.extra["total_code_lines"], case["total_code_lines"],
            "{}",
            case["id"]
        );
        assert!(files.iter().all(|file| {
            output
                .targets
                .iter()
                .any(|target| target.file_path == file.file_path)
        }));
    }
}

#[test]
fn shared_type2_bounds_cancellation_and_policy_preserve_evidence_axes() {
    let file = duplicates::DuplicateFileData {
        file_path: "main.py".into(),
        language: "python".into(),
        raw_text: "a()\nb()\nc()\nd()\ne()\nf()\n".into(),
        indexed: (1..=6)
            .map(|line| (line, "return\u{1f}ID".into()))
            .collect(),
        regions: vec![(0, 0); 6],
    };
    let files = [
        file.clone(),
        duplicates::DuplicateFileData {
            file_path: "other.py".into(),
            ..file
        },
    ];
    for key in [
        "max_window_occurrences",
        "max_cross_file_pairs",
        "max_cross_file_seed_pairs",
        "max_extension_comparisons",
        "max_raw_matches",
        "max_indexed_records",
        "max_normalized_chars",
    ] {
        let mut input = request("dup", "");
        input.options = json!({key:0,"window_size":3});
        let result = duplicates::analyze_type2(&input, &files);
        assert_eq!(result.observation.state, TaskState::Failed, "{key}");
        assert!(result.observation.findings.is_empty());
    }
    let mut input = request("dup", "");
    input.options = json!({"window_size":3,"warn_pct":100,"fail_pct":100});
    let result = duplicates::analyze_type2(&input, &files);
    assert_eq!(result.status, "PASS");
    assert!(result.targets.iter().all(|target| target.status == "PASS"));
    assert!(!result.observation.findings.is_empty());
    let result = duplicates::analyze_type2_cancellable(
        &input,
        &files,
        &std::sync::atomic::AtomicBool::new(true),
    );
    assert_eq!(result.observation.state, TaskState::Cancelled);
    assert!(result.observation.findings.is_empty());
}

#[test]
fn native_metadata_floor_and_cache_targets_are_explicit() {
    let mut input = request("compat", "import tomllib\n");
    input.inputs.push(SourceInput {
        path: "pyproject.toml".into(),
        bytes:
            b"[project]\nname = 'demo'\nversion = '1.0'\nrequires-python = '>=3.10.5,!=3.10.0'\n"
                .to_vec(),
    });
    let result = analyze(&input);
    assert_eq!(result.extra["target_version"], "3.10");
    assert_eq!(result.observation.findings.len(), 1);
    input.inputs[1].bytes = b"[project]\nrequires-python = 'invalid'".to_vec();
    assert_eq!(analyze(&input).observation.state, TaskState::Failed);
    let mut input = request("compat", "type Alias = int\n");
    input.options = json!({"target_version":"3.10"});
    input.target_version = "3.10".into();
    let mut session = AnalysisSession::new();
    assert_eq!(
        session.analyze(&input).observation.state,
        TaskState::Succeeded
    );
    input.check_id = "python.complexity".into();
    assert_eq!(session.analyze(&input).observation.state, TaskState::Failed);
}

#[test]
fn native_function_facts_preserve_coverage_body_ranges_and_pytest_scopes() {
    let input = request(
        "coverage",
        "@decorator\nclass Suite:\n    @decorate\n    async def test_이름(self):\n        @decorate\n        def nested():\n            return 1\n\n        return nested()\n",
    );
    let mut session = AnalysisSession::new();
    let facts = session.source_facts(&input).unwrap();
    assert_eq!(facts.classes.len(), 1);
    assert_eq!(facts.classes[0].start_line, 2);
    assert_eq!(facts.functions.len(), 2);
    let method = &facts.functions[0];
    assert_eq!(method.name, "test_이름");
    assert_eq!(method.qualified_name, "Suite.test_이름");
    assert_eq!(method.entry_line, 3);
    assert_eq!(method.class_names, ["Suite"]);
    assert_eq!(
        (
            method.start_line,
            method.end_line,
            method.body_start_line,
            method.body_end_line
        ),
        (4, 9, 6, 9)
    );
    let nested = &facts.functions[1];
    assert_eq!(nested.qualified_name, "Suite.test_이름.nested");
    assert_eq!(
        (
            nested.start_line,
            nested.end_line,
            nested.body_start_line,
            nested.body_end_line
        ),
        (6, 7, 7, 7)
    );
    assert_eq!(session.function_facts(&input).unwrap(), facts.functions);
    assert_eq!(session.parser_invocations(), 1);
    assert!(function_facts(&request("coverage", "def broken(:\n")).is_err());
    let mut invalid = input;
    invalid.inputs[0].bytes = vec![0xff];
    assert!(function_facts(&invalid).is_err());
}

#[test]
fn semantic_shapes_match_fixed_cpython_fields_literals_and_exclusions() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/semantic.json"
    ))
    .unwrap();
    let mut differences = vec![];
    for case in fixture["cases"].as_array().unwrap() {
        let mut files = vec![];
        let mut failed = false;
        for (path, text) in case["sources"].as_object().unwrap() {
            let input = SourceInput {
                path: path.clone(),
                bytes: text.as_str().unwrap().as_bytes().to_vec(),
            };
            match SourceDocument::decode(&input)
                .and_then(|source| ParsedFile::parse(source, "3.14"))
            {
                Ok(file) => files.push(std::sync::Arc::new(file)),
                Err(error) => {
                    failed = true;
                    if case["native_source_error"] != true {
                        differences.push(format!("{}: source failed {error:?}", case["id"]));
                    }
                }
            }
        }
        if failed {
            continue;
        }
        assert_ne!(case["native_source_error"], true, "{}", case["id"]);
        let mut limits = semantic::Limits::default();
        for (name, value) in case["limits"].as_object().unwrap() {
            let value = value.as_u64().unwrap() as usize;
            match name.as_str() {
                "max_files" => limits.files = value,
                "max_regions" => limits.regions = value,
                "max_nodes" => limits.nodes = value,
                "max_serialized_chars" => limits.chars = value,
                _ => unreachable!(),
            }
        }
        let actual = serde_json::to_value(semantic::analyze(&files, limits, None)).unwrap();
        let expected = case.get("expected").unwrap_or(&case["original"]);
        if actual != *expected {
            differences.push(format!(
                "{}\nACTUAL={actual}\nEXPECTED={expected}",
                case["id"]
            ));
        }
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
}

#[test]
fn semantic_clone_pipeline_preserves_old_group_merging_and_policy() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/semantic-integration.json"
    ))
    .unwrap();
    let mut differences = vec![];
    for case in fixture["cases"].as_array().unwrap() {
        let mut input = request("dup", "");
        input.options = case["options"].clone();
        input.inputs = case["sources"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(path, text)| SourceInput {
                path: path.clone(),
                bytes: text.as_str().unwrap().as_bytes().to_vec(),
            })
            .collect();
        let output = analyze(&input);
        if json!(output.status) != case["status"] {
            differences.push(format!(
                "{}: status{} vs{}",
                case["id"], output.status, case["status"]
            ));
        }
        for (key, expected) in case["extra"].as_object().unwrap() {
            if output.extra[key] != *expected {
                differences.push(format!(
                    "{}:{key}\nACTUAL={}\nEXPECTED={expected}",
                    case["id"], output.extra[key]
                ));
            }
        }
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
}

#[test]
fn unicode16_full_folding_and_identifier_inventory_match_every_cpython_scalar() {
    use sha2::Digest;
    use std::fmt::Write;
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/unicode-casefold.json"
    ))
    .unwrap();
    assert_eq!(
        format!(
            "{:x}",
            sha2::Sha256::digest(include_bytes!("../data/unicode-16/CaseFolding.txt"))
        ),
        fixture["source_sha256"].as_str().unwrap()
    );
    assert_eq!(
        format!(
            "{:x}",
            sha2::Sha256::digest(include_bytes!(
                "../data/unicode-16/DerivedCoreProperties.txt"
            ))
        ),
        fixture["xid_source_sha256"].as_str().unwrap()
    );
    assert_eq!(
        format!(
            "{:x}",
            sha2::Sha256::digest(include_bytes!("../data/unicode-16/LICENSE.txt"))
        ),
        fixture["license_sha256"].as_str().unwrap()
    );
    let mut folding = sha2::Sha256::new();
    let mut identifiers = sha2::Sha256::new();
    let mut lower = sha2::Sha256::new();
    let mut decimal = sha2::Sha256::new();
    let mut repr = sha2::Sha256::new();
    let mut word = sha2::Sha256::new();
    let mut text = String::new();
    for code in 0..=0x10ffff {
        let Some(character) = char::from_u32(code) else {
            continue;
        };
        identifiers.update([
            u8::from(unicode::identifier_start(character)),
            u8::from(unicode::identifier_continue(character)),
        ]);
        decimal.update([unicode::decimal_digit(character).unwrap_or(255)]);
        let character_text = character.to_string();
        word.update([u8::from(unicode::word(&character_text))]);
        let representation = unicode::python_repr(&character_text);
        repr.update(format!("{code:06X};{representation}\n").as_bytes());
        let lowercase = unicode::lower(&character_text);
        let mapping = lowercase
            .chars()
            .map(|character| format!("{:06X}", character as u32))
            .collect::<Vec<_>>()
            .join(" ");
        lower.update(format!("{code:06X};{mapping}\n").as_bytes());
        text.clear();
        write!(text, "{code:06X};").unwrap();
        let folded = unicode::fold(&character.to_string());
        for (index, character) in folded.chars().enumerate() {
            if index > 0 {
                text.push(' ');
            }
            write!(text, "{:06X}", character as u32).unwrap();
        }
        text.push('\n');
        folding.update(text.as_bytes());
    }
    assert_eq!(
        format!("{:x}", folding.finalize()),
        fixture["all_scalar_fold_sha256"].as_str().unwrap()
    );
    assert_eq!(
        format!("{:x}", identifiers.finalize()),
        fixture["all_scalar_identifier_sha256"].as_str().unwrap()
    );
    assert_eq!(
        format!("{:x}", decimal.finalize()),
        fixture["all_scalar_decimal_sha256"].as_str().unwrap()
    );
    assert_eq!(
        format!("{:x}", repr.finalize()),
        fixture["all_scalar_repr_sha256"].as_str().unwrap()
    );
    assert_eq!(
        format!("{:x}", lower.finalize()),
        fixture["all_scalar_lower_sha256"].as_str().unwrap()
    );
    assert_eq!(
        format!("{:x}", word.finalize()),
        fixture["all_scalar_word_sha256"].as_str().unwrap()
    );
    for sample in fixture["lower_samples"].as_array().unwrap() {
        assert_eq!(
            unicode::lower(sample[0].as_str().unwrap()),
            sample[1].as_str().unwrap()
        );
    }
    let bytes: Vec<_> = (0..=255).collect();
    let decoded = wheel_zip::decode_name(&bytes, 0).unwrap();
    assert_eq!(
        format!("{:x}", sha2::Sha256::digest(decoded.as_bytes())),
        fixture["all_cp437_sha256"].as_str().unwrap()
    );
    assert_eq!(
        unicode::portable("Straße/Cafe\u{301}.PY/"),
        "strasse/café.py"
    );
    assert_eq!(unicode::fold("IİıΣςσẞ"), "ii\u{307}ıσσσss");
    // These scalars entered Unicode after 16 and are accepted by newer tables.
    assert!(!unicode::identifier("\u{1c8b}"));
    assert_eq!(
        analyze(&request("complexity", "\u{1c8b} = 1\n"))
            .observation
            .state,
        TaskState::Failed
    );
}

#[test]
fn security_unicode_casefold_allowlists_and_comment_suppression_match_python() {
    let mut input = request("security", "ss = 'ghp_0123456789abcdefghijkABCDEF'\n");
    input.options = json!({"secret_name_allowlist":["ß"]});
    assert!(analyze(&input).observation.findings.is_empty());
    let input = request(
        "security",
        "password = 'ghp_0123456789abcdefghijkABCDEF' # noſec\n",
    );
    assert!(analyze(&input).observation.findings.is_empty());
}

#[test]
fn package_and_wheel_contracts_match_frozen_findings_targets_and_metadata() {
    use base64::Engine;
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/package.json"
    ))
    .unwrap();
    let mut differences = vec![];
    for case in fixture["cases"].as_array().unwrap() {
        let mut input = request("compat", "");
        input.options = case["options"].clone();
        input.options["package_inspection"] = json!(true);
        input.inputs = case["sources"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(path, text)| SourceInput {
                path: path.clone(),
                bytes: text.as_str().unwrap().as_bytes().to_vec(),
            })
            .collect();
        if let Some(text) = case["pyproject"].as_str() {
            input.inputs.push(SourceInput {
                path: "pyproject.toml".into(),
                bytes: text.as_bytes().to_vec(),
            });
        }
        for (path, bytes) in case["artifacts"].as_object().unwrap() {
            input.inputs.push(SourceInput {
                path: path.clone(),
                bytes: base64::engine::general_purpose::STANDARD
                    .decode(bytes.as_str().unwrap())
                    .unwrap(),
            });
        }
        let output = analyze(&input);
        for finding in output
            .observation
            .findings
            .iter()
            .filter(|finding| finding.rule_id.starts_with("ici.package."))
        {
            let detail = &output.extra["finding_details"][&finding.fingerprint];
            assert_eq!(detail["legacy_rule_id"], finding.rule_id);
            assert_eq!(detail["legacy_confidence"], finding.confidence);
            assert_eq!(detail["legacy_label"], finding.primary_location.label);
        }
        if let Some(message) = case["correction"]["native_error"].as_str() {
            assert_eq!(
                output.observation.state,
                TaskState::Succeeded,
                "{}",
                case["id"]
            );
            assert!(
                output.observation.findings.iter().any(|finding| {
                    finding.rule_id == "ici.package.wheel-invalid"
                        && finding.message.contains(message)
                        && finding.primary_location.path.ends_with(".whl")
                }),
                "{}: {:?}",
                case["id"],
                output.observation.findings
            );
            assert_eq!(output.extra["package_evidence"]["checked"], json!(0));
            assert_eq!(output.extra["package_evidence"]["invalid"], json!(1));
            continue;
        }
        if case.get("original_error").is_some() {
            if output.observation.state != TaskState::Failed || output.status != "ERROR" {
                differences.push(format!(
                    "{}: expected incomplete metadata/policy, got{} {:?}",
                    case["id"], output.status, output.observation.limitations
                ));
            }
            continue;
        }
        let expected = case.get("expected").unwrap_or(&case["original"]);
        if output.observation.state != TaskState::Succeeded {
            differences.push(format!(
                "{}: unexpectedly incomplete {:?}",
                case["id"], output.observation.limitations
            ));
            continue;
        }
        let findings:Vec<_>=output.observation.findings.iter().filter(|finding|finding.rule_id.starts_with("ici.package.")).map(|finding|json!({"rule_id":finding.rule_id,"fingerprint":finding.fingerprint,"message":finding.message,"severity":finding.severity,"confidence":finding.confidence,"category":finding.category,"native_rule_id":finding.native_rule_id,"path":finding.primary_location.path,"line":finding.primary_location.start_line})).collect();
        let targets:Vec<_>=output.targets.iter().filter(|target|target.target_name.starts_with("Package:")).map(|target|json!({"file_path":target.file_path,"start_line":target.start_line,"end_line":target.end_line,"start_column":target.start_column,"end_column":target.end_column,"target_name":target.target_name,"status":target.status,"message":target.message,"snippet":target.snippet})).collect();
        for (key, actual) in [
            ("findings", json!(findings)),
            ("targets", json!(targets)),
            ("metadata", output.extra["package_evidence"].clone()),
        ] {
            if actual != expected[key] {
                differences.push(format!(
                    "{}:{key}\nACTUAL={actual}\nEXPECTED={}",
                    case["id"], expected[key]
                ));
            }
        }
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
}

#[test]
fn package_inspection_scope_is_explicit_and_missing_requested_metadata_is_incomplete() {
    let mut input = request("compat", "value = 1\n");
    input.inputs.push(SourceInput {
        path: "pyproject.toml".into(),
        bytes: b"[project]\nname = ''\nrequires-python = '>=3.10'\n".to_vec(),
    });
    let output = analyze(&input);
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert!(output.extra.get("package_evidence").is_none());
    assert!(output.observation.findings.is_empty());
    for flag in ["legacy_projection", "package_inspection"] {
        input.options = json!({flag:true});
        assert_eq!(analyze(&input).observation.state, TaskState::Failed);
    }
    input.inputs.pop();
    input.options = json!({"legacy_projection":true});
    assert_eq!(analyze(&input).observation.state, TaskState::Succeeded);
    for options in [
        json!({"package_inspection":true}),
        json!({"wheel_globs":["dist/*.whl"]}),
    ] {
        input.options = options;
        let output = analyze(&input);
        assert_eq!(output.observation.state, TaskState::Failed);
        assert!(
            output
                .observation
                .limitations
                .iter()
                .any(|message| message.contains("package-metadata-missing"))
        );
    }
    input.options = json!({});
    input.inputs.push(SourceInput {
        path: "dist/example.whl".into(),
        bytes: vec![],
    });
    assert_eq!(analyze(&input).observation.state, TaskState::Failed);
}

#[test]
fn wheel_chunk_cancellation_retains_completed_source_metadata_without_partial_success() {
    use base64::Engine;
    use std::io::{Cursor, Read, Write};
    use std::sync::{Barrier, atomic::Ordering};
    use zip::{ZipArchive, ZipWriter, write::SimpleFileOptions};
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/package.json"
    ))
    .unwrap();
    let case = fixture["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| case["id"] == "pure-wheel")
        .unwrap();
    let encoded = case["artifacts"]
        .as_object()
        .unwrap()
        .values()
        .next()
        .unwrap()
        .as_str()
        .unwrap();
    let mut old = ZipArchive::new(Cursor::new(
        base64::engine::general_purpose::STANDARD
            .decode(encoded)
            .unwrap(),
    ))
    .unwrap();
    let mut writer = ZipWriter::new(Cursor::new(Vec::new()));
    let options = SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
    for index in 0..old.len() {
        let mut member = old.by_index(index).unwrap();
        let mut payload = vec![];
        member.read_to_end(&mut payload).unwrap();
        if member.name().ends_with("/RECORD") {
            payload.extend_from_slice(b"demo/large.dat,sha256=placeholder,33554432\n");
        }
        writer.start_file(member.name(), options).unwrap();
        writer.write_all(&payload).unwrap();
    }
    writer.start_file("demo/large.dat", options).unwrap();
    for _ in 0..512 {
        writer.write_all(&[0; 65_536]).unwrap();
    }
    let mut input = request("compat", "");
    input.options = case["options"].clone();
    input.inputs = case["sources"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(path, text)| SourceInput {
            path: path.clone(),
            bytes: text.as_str().unwrap().as_bytes().to_vec(),
        })
        .collect();
    let files: Vec<_> = input
        .inputs
        .iter()
        .map(|source| {
            Arc::new(ParsedFile::parse(SourceDocument::decode(source).unwrap(), "3.14").unwrap())
        })
        .collect();
    input.inputs.push(SourceInput {
        path: "pyproject.toml".into(),
        bytes: case["pyproject"].as_str().unwrap().as_bytes().to_vec(),
    });
    input.inputs.push(SourceInput {
        path: "dist/demo-1.2.3-py3-none-any.whl".into(),
        bytes: writer.finish().unwrap().into_inner(),
    });
    let metadata = metadata::load(&input).unwrap().unwrap();
    let cancellation = Arc::new(AtomicBool::new(false));
    let ready = Arc::new(Barrier::new(2));
    let flag = Arc::clone(&cancellation);
    let signal = Arc::clone(&ready);
    let worker = std::thread::spawn(move || {
        signal.wait();
        std::thread::sleep(std::time::Duration::from_millis(10));
        flag.store(true, Ordering::Relaxed);
    });
    let mut output = AnalysisOutput::new(&input.task_id, "ici.compat");
    ready.wait();
    package::analyze(&input, &metadata, &files, &mut output, Some(&cancellation));
    worker.join().unwrap();
    assert_eq!(output.observation.state, TaskState::Cancelled);
    assert_eq!(output.status, "ERROR");
    assert!(output.targets.iter().any(|target|target.target_name=="Package:SourceMetadata" && target.status=="PASS"));
    assert!(
        !output
            .targets
            .iter()
            .any(|target| target.target_name == "Package:Wheel")
    );
    assert!(output.observation.findings.is_empty());
    assert!(output.extra.get("package_evidence").is_none());
}

#[test]
fn python_metadata_specifiers_preserve_pep440_arbitrary_and_bounded_floor_semantics() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/metadata.json"
    ))
    .unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let specifier = case["specifier"].as_str().unwrap();
        let floor = metadata::infer_floor(specifier);
        if case.get("error").is_some() {
            assert!(floor.is_err(), "{specifier:?}");
            continue;
        }
        assert_eq!(
            floor
                .unwrap()
                .map(|version| json!([version.major, version.minor]))
                .unwrap_or(Value::Null),
            case["floor"],
            "{specifier:?}"
        );
        for (version, expected) in case["versions"].as_object().unwrap() {
            assert_eq!(
                python_version_allowed(specifier, version).unwrap(),
                expected.as_bool().unwrap(),
                "{specifier:?}: {version}"
            );
        }
    }
}

#[test]
fn metadata_import_discovery_matches_fixed_project_and_workspace_precedence() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/metadata.json"
    ))
    .unwrap();
    for case in fixture["metadata_cases"].as_array().unwrap() {
        let mut input = request("compat", "");
        input.inputs = case["sources"]
            .as_array()
            .unwrap()
            .iter()
            .map(|path| SourceInput {
                path: path.as_str().unwrap().into(),
                bytes: b"value = 1\n".to_vec(),
            })
            .collect();
        input.options = json!({"component_root":case["component_root"]});
        if let Some(document) = case["document"].as_str() {
            let root = case["metadata_root"].as_str().unwrap();
            let path = if root.is_empty() {
                "pyproject.toml".into()
            } else {
                format!("{root}/pyproject.toml")
            };
            input.inputs.push(SourceInput {
                path,
                bytes: document.as_bytes().to_vec(),
            });
        }
        let loaded = metadata::load(&input);
        if case.get("error").is_some() {
            assert!(loaded.is_err(), "{}", case["id"]);
            continue;
        }
        let loaded = loaded.unwrap();
        let actual = json!({"requires_python":loaded.as_ref().map_or("",|metadata|metadata.requires_python.as_str()),"pyproject_present":loaded.is_some(),"import_names":metadata::import_names(&input,loaded.as_ref())});
        assert_eq!(actual, case["original"], "{}", case["id"]);
    }
    let mut input = request("compat", "value = 1\n");
    input.options = json!({"component_root":"app"});
    input.inputs.extend([
        SourceInput {
            path: "pyproject.toml".into(),
            bytes: b"[project]\nrequires-python = '>=3.9'".to_vec(),
        },
        SourceInput {
            path: "app/pyproject.toml".into(),
            bytes: b"[project]\nrequires-python = '>=3.11'".to_vec(),
        },
    ]);
    assert_eq!(requires_python(&input).unwrap().as_deref(), Some(">=3.11"));
}

#[test]
fn source_facts_propagate_active_cancellation_during_large_enrichment() {
    use std::sync::{
        Arc, Barrier,
        atomic::{AtomicBool, Ordering},
    };
    let mut text = String::new();
    for index in 0..20_000 {
        text.push_str(&format!(
            "def function_{index}(value):\n    return value + 1\n"
        ));
    }
    let input = request("coverage", &text);
    let cancellation = Arc::new(AtomicBool::new(false));
    let ready = Arc::new(Barrier::new(2));
    let flag = Arc::clone(&cancellation);
    let signal = Arc::clone(&ready);
    let worker = std::thread::spawn(move || {
        signal.wait();
        std::thread::sleep(std::time::Duration::from_millis(1));
        flag.store(true, Ordering::Relaxed);
    });
    ready.wait();
    let result = AnalysisSession::new().source_facts_cancellable(&input, &cancellation);
    worker.join().unwrap();
    let output = result.unwrap_err();
    assert_eq!(output.observation.state, TaskState::Cancelled);
    assert_eq!(output.status, "ERROR");
}
#[test]
fn metrics_match_fixed_python_formulas() {
    let fixtures: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/metrics.json"
    ))
    .unwrap();
    for case in fixtures["cases"].as_array().unwrap() {
        let mut session = AnalysisSession::new();
        for kind in ["complexity", "cognitive"] {
            let output = session.analyze(&request(kind, case["source"].as_str().unwrap()));
            assert_eq!(
                output.observation.state,
                TaskState::Succeeded,
                "{}: {:?}",
                case["id"],
                output.observation.limitations
            );
            assert_eq!(
                output
                    .targets
                    .iter()
                    .filter(|target| target.metrics.contains_key(kind))
                    .count(),
                case["functions"].as_array().unwrap().len(),
                "{} function inventory",
                case["id"]
            );
            for expected in case["functions"].as_array().unwrap() {
                let name = expected["name"].as_str().unwrap();
                let label = if kind == "complexity" {
                    format!("{name}()")
                } else {
                    name.to_owned()
                };
                let target = output
                    .targets
                    .iter()
                    .find(|target| target.target_name == label)
                    .unwrap();
                assert_eq!(
                    target.metrics[kind], expected[kind],
                    "{}:{label}:{kind}",
                    case["id"]
                );
                assert_eq!(
                    target.metrics["nesting"],
                    expected[if kind == "complexity" {
                        "nesting"
                    } else {
                        "cognitive_nesting"
                    }],
                    "{}:{label}:nesting",
                    case["id"]
                );
                assert_eq!(
                    target.start_line,
                    expected["line"].as_u64().unwrap() as u32,
                    "{}:{label}:start",
                    case["id"]
                );
                assert_eq!(
                    target.end_line,
                    Some(expected["end_line"].as_u64().unwrap() as u32)
                );
            }
        }
        assert_eq!(
            session.parser_invocations(),
            1,
            "parse shared across two metrics"
        );
    }
}
#[test]
fn unicode_and_legacy_threshold_boundaries_are_preserved() {
    let mut input = request(
        "cognitive",
        "def 이름(값):\n    if 값:\n        return 값\n",
    );
    input.options = json!({"warn":1,"fail":2,"warn_nesting":9});
    let output = analyze(&input);
    assert_eq!(output.targets[0].status, "WARN");
    assert!(
        output.observation.findings.is_empty(),
        "modern metric uses strict > threshold"
    );
    assert_eq!(output.targets[0].start_column, Some(1));
    input.options["warn"] = json!(0);
    let output = analyze(&input);
    assert_eq!(output.observation.findings.len(), 1);
    assert_eq!(
        output.observation.findings[0].primary_location.end_column,
        Some(16)
    );
}
#[test]
fn syntax_semantics_and_bounds_cannot_be_clean() {
    for source in [
        "return 1\n",
        "nonlocal missing\n",
        "break\n",
        "await work()\n",
        "def bad(:\n pass\n",
        "def f(a,a):\n pass\n",
    ] {
        let output = analyze(&request("complexity", source));
        assert_eq!(output.observation.state, TaskState::Failed, "{source}");
        assert_eq!(output.status, "ERROR");
        assert!(!output.targets.is_empty());
    }
    let mut input = request("complexity", "type Alias[T] = list[T]\n");
    input.target_version = "3.10".into();
    assert_eq!(analyze(&input).observation.state, TaskState::Failed);
    input = request(
        "complexity",
        &format!("x = {}1{}\n", "(".repeat(201), ")".repeat(201)),
    );
    assert_eq!(analyze(&input).observation.state, TaskState::Failed);
}
#[test]
fn scope_aliases_respect_shadowing_and_comprehension_boundaries() {
    let input = request(
        "complexity",
        "import os\ndef outer(os):\n    os.system('x')\n    return [os.system('x') for os in values]\ndef plain():\n    return os.system('x')\n",
    );
    let file =
        ParsedFile::parse(SourceDocument::decode(&input.inputs[0]).unwrap(), "3.14").unwrap();
    struct Calls<'a> {
        graph: &'a scope::ScopeGraph,
        names: Vec<Option<String>>,
    }
    impl<'a> ruff_python_ast::visitor::Visitor<'a> for Calls<'_> {
        fn visit_expr(&mut self, e: &'a ruff_python_ast::Expr) {
            if let ruff_python_ast::Expr::Call(call) = e
                && matches!(call.func.as_ref(), ruff_python_ast::Expr::Attribute(_))
            {
                self.names.push(self.graph.resolve(&call.func));
            }
            ruff_python_ast::visitor::walk_expr(self, e);
        }
    }
    use ruff_python_ast::visitor::Visitor;
    let mut calls = Calls {
        graph: &file.scopes,
        names: vec![],
    };
    calls.visit_body(file.body());
    assert_eq!(calls.names, vec![None, None, Some("os.system".into())]);
}
#[test]
fn decoder_maps_original_bytes_and_universal_newlines() {
    let input = SourceInput {
        path: "main.py".into(),
        bytes: b"\xef\xbb\xbf# coding: utf-8\r\nx=1\rprint(x)\r".to_vec(),
    };
    let source = SourceDocument::decode(&input).unwrap();
    assert_eq!(source.location(source.text.find("print").unwrap()), (3, 1));
    assert_eq!(source.raw_offset(0), Some(3));
    let input = SourceInput {
        path: "main.py".into(),
        bytes: b"#!/usr/bin/python\r\n# coding: latin-1\r\nname='caf\xe9'\r\n".to_vec(),
    };
    let source = SourceDocument::decode(&input).unwrap();
    let start = source.text.find('é').unwrap();
    assert_eq!(
        source.raw_offset(start + 2),
        source.raw_offset(start).map(|value| value + 1)
    );
    assert_eq!(source.lines().count(), 3);
}
#[test]
fn line_counts_and_explicit_legacy_scopes_keep_all_inspected_targets() {
    let mut input = request("line", "# comment\r\nx=1\r\n\r\n");
    input.inputs.push(SourceInput {
        path: "other.py".into(),
        bytes: b"x=2\n".to_vec(),
    });
    input.options = json!({"legacy_projection":true,"source_paths":["main.py"],"gated_paths":[],"warn_limit":0,"fail_limit":null});
    let output = analyze(&input);
    assert_eq!(output.targets.len(), 2);
    assert_eq!(output.extra["code"], json!(1));
    assert_eq!(output.extra["all"]["code"], json!(2));
    assert!(output.observation.findings.is_empty());
    assert_eq!(output.extra["comment"], json!(1));
    assert_eq!(output.extra["blank"], json!(1));
}

#[test]
fn legacy_line_rankings_keep_source_scope_and_actual_extra_languages() {
    let mut input = request("line", "# comment\nx = 1\n");
    input.inputs.extend([
        SourceInput {
            path: "README.md".into(),
            bytes: b"# title\nparagraph\nmore\n".to_vec(),
        },
        SourceInput {
            path: "tools/run.sh".into(),
            bytes: b"#!/bin/sh\n# comment\necho ready\n".to_vec(),
        },
        SourceInput {
            path: "config.toml".into(),
            bytes: b"# comment\nkey = 1\n".to_vec(),
        },
        SourceInput {
            path: "ci.yaml".into(),
            bytes: b"# comment\nkey: 1\n".to_vec(),
        },
        SourceInput {
            path: "data.json".into(),
            bytes: b"{\n}\n".to_vec(),
        },
        SourceInput {
            path: "example.cpp".into(),
            bytes: b"/* start\n\nend */\n// comment\nint x;\n".to_vec(),
        },
    ]);
    input.options = json!({"legacy_projection":true,"source_paths":["main.py"],"gated_paths":["main.py"],"warn_limit":1,"fail_limit":2});
    let output = analyze(&input);
    assert_eq!(output.targets.len(), 7);
    assert!(output.targets.iter().all(|target| target.status == "PASS"));
    assert_eq!(output.extra["code"], 1);
    assert_eq!(output.extra["comment"], 1);
    assert_eq!(output.extra["all"]["code"], 10);
    assert_eq!(output.extra["all"]["comment"], 8);
    assert_eq!(output.extra["all"]["blank"], 1);
    assert_eq!(output.extra["top_files"].as_array().unwrap().len(), 1);
    assert_eq!(output.extra["top_files"][0]["path"], "main.py");
    assert_eq!(output.extra["top_files_all"][0]["path"], "README.md");
    for (path, language, code, comment, blank) in [
        ("README.md", "Markdown", 3, 0, 0),
        ("tools/run.sh", "Shell", 1, 2, 0),
        ("config.toml", "TOML", 1, 1, 0),
        ("ci.yaml", "YAML", 1, 1, 0),
        ("data.json", "JSON", 2, 0, 0),
        ("example.cpp", "C++", 1, 3, 1),
    ] {
        let row = output.extra["files_data"]
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["path"] == path)
            .unwrap();
        assert_eq!(row["lang"], language, "{path}");
        assert_eq!(row["scope"], "extra", "{path}");
        assert_eq!(
            (
                row["code"].as_u64(),
                row["comment"].as_u64(),
                row["blank"].as_u64()
            ),
            (Some(code), Some(comment), Some(blank)),
            "{path}"
        );
        assert_eq!(
            output
                .targets
                .iter()
                .find(|target| target.file_path == path)
                .unwrap()
                .target_name,
            language
        );
    }
}

#[test]
fn inclusive_endpoints_cover_unicode_multiline_and_zero_width() {
    use ruff_text_size::{TextRange, TextSize};
    let source = SourceDocument::decode(&SourceInput {
        path: "main.py".into(),
        bytes: "가나\néx".as_bytes().to_vec(),
    })
    .unwrap();
    let span = source.span(TextRange::new(TextSize::new(0), TextSize::new(10)));
    assert_eq!(
        (
            span.start_line,
            span.start_column,
            span.end_line,
            span.end_column
        ),
        (1, Some(1), Some(2), Some(2))
    );
    let letter = source.span(TextRange::new(TextSize::new(7), TextSize::new(9)));
    assert_eq!(
        (
            letter.start_line,
            letter.start_column,
            letter.end_line,
            letter.end_column
        ),
        (2, Some(1), Some(2), Some(1))
    );
    let point = source.span(TextRange::new(TextSize::new(7), TextSize::new(7)));
    assert_eq!(point, letter);
}

#[test]
fn hygiene_matches_fixed_oracle_and_explicit_corrected_expectations() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/hygiene.json"
    ))
    .unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let mut input = request(
            case["kind"].as_str().unwrap(),
            case["source"].as_str().unwrap(),
        );
        if let Some(options) = case.get("options") {
            input.options = options.clone();
        }
        let output = analyze(&input);
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{}:{} {:?}",
            case["kind"],
            case["id"],
            output.observation.limitations
        );
        let targets:Vec<Value>=output.targets.iter().filter(|target|matches!(target.status.as_str(),"WARN"|"FAIL")).map(|target|json!({"name":target.target_name,"line":target.start_line,"end_line":target.end_line,"message":target.message,"snippet":target.snippet,"metrics":target.metrics,"status":target.status})).collect();
        let expected = case
            .get("expected_targets")
            .unwrap_or(&case["oracle_targets"]);
        assert_eq!(
            json!(targets),
            *expected,
            "{}:{} targets",
            case["kind"],
            case["id"]
        );
        let counters = case
            .get("expected_counters")
            .unwrap_or(&case["oracle_counters"]);
        for (key, value) in counters.as_object().unwrap() {
            assert_eq!(
                output.extra[key], *value,
                "{}:{}:{key}",
                case["kind"], case["id"]
            );
        }
        assert!(
            output.targets.iter().any(|target| target.status == "PASS"),
            "all checked files retain a target"
        );
    }
}
#[test]
fn secrets_are_redacted_and_allowlists_and_nosec_are_structural() {
    let source = "password=\"sk-fixture-sensitive-value-98765\"; eval(payload)\nsettings={\"auth_token\":\"ghp_fixture_sensitive_123456\"}\n";
    let mut input = request("security", source);
    let output = analyze(&input);
    assert_eq!(output.observation.findings.len(), 3);
    let serialized = serde_json::to_string(&output).unwrap();
    assert!(!serialized.contains("sk-fixture-sensitive-value-98765"));
    assert!(!serialized.contains("ghp_fixture_sensitive_123456"));
    input.options = json!({"secret_name_allowlist":["password","AUTH_TOKEN"]});
    assert_eq!(analyze(&input).observation.findings.len(), 1);
    let input = request("security", "암호 = \"x\"; password=\"abcdefghijk\"\n");
    let output = analyze(&input);
    let target = output
        .targets
        .iter()
        .find(|target| target.target_name == "Security:HardcodedSecret")
        .unwrap();
    assert_eq!(target.start_column, Some(11));
    assert_eq!(target.end_column, Some(32));
}

#[test]
fn excluded_vendor_and_generated_inputs_remain_visible_and_not_measured() {
    let mut input = request("security", "x=1\n");
    input.inputs.extend([
        SourceInput {
            path: "vendor/a.py".into(),
            bytes: b"eval(data)\n".to_vec(),
        },
        SourceInput {
            path: "generated/b.py".into(),
            bytes: b"eval(data)\n".to_vec(),
        },
        SourceInput {
            path: "vendor/generated/c.py".into(),
            bytes: b"eval(data)\n".to_vec(),
        },
    ]);
    let output = analyze(&input);
    assert!(output.observation.findings.is_empty());
    assert_eq!(output.extra["files_checked"], json!(1));
    assert_eq!(
        output.extra["excluded_source_counts"],
        json!({"generated":2,"vendor":2})
    );
    assert_eq!(
        output
            .targets
            .iter()
            .filter(|target| target.status == "SKIP")
            .count(),
        3
    );
    input.options = json!({"include_vendor":true,"include_generated":true});
    assert_eq!(analyze(&input).observation.findings.len(), 3);
}

#[test]
fn exception_aliases_and_handler_targets_match_fixed_source() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/exception.json"
    ))
    .unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let output = analyze(&request("exception", case["source"].as_str().unwrap()));
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{} {:?}",
            case["id"],
            output.observation.limitations
        );
        let targets:Vec<Value>=output.targets.iter().map(|target|json!({"name":target.target_name,"line":target.start_line,"end_line":target.end_line,"message":target.message,"status":target.status})).collect();
        assert_eq!(json!(targets), case["targets"], "{}", case["id"]);
    }
}

#[test]
fn cancellation_is_explicit_before_intake_and_does_not_populate_cache() {
    use std::sync::atomic::AtomicBool;
    let cancellation = AtomicBool::new(true);
    let mut session = AnalysisSession::new();
    let output = session.analyze_cancellable(
        &request("complexity", "def f():\n return 1\n"),
        &cancellation,
    );
    assert_eq!(output.observation.state, TaskState::Cancelled);
    assert_eq!(session.parser_invocations(), 0);
    assert!(output.targets.is_empty());
    assert!(
        output
            .observation
            .limitations
            .iter()
            .any(|message| message == "analysis cancelled")
    );
}
#[test]
fn frozen_source_limit_accepts_sixteen_mib_and_rejects_larger_input() {
    let mut input = SourceInput {
        path: "large.py".into(),
        bytes: vec![b' '; 16 * 1024 * 1024],
    };
    input.bytes[0] = b'#';
    let source = SourceDocument::decode(&input).unwrap();
    assert_eq!(source.text.len(), 16 * 1024 * 1024);
    assert_eq!(source.lines().count(), 1);
    input.bytes.push(b' ');
    assert_eq!(
        SourceDocument::decode(&input).unwrap_err().code,
        "source-limit"
    );
}

fn project_request(check: &str, files: &[(&str, &str)], options: Value) -> AnalysisRequest {
    let mut request = request(check, "");
    request.inputs = files
        .iter()
        .map(|(path, text)| SourceInput {
            path: (*path).into(),
            bytes: text.as_bytes().to_vec(),
        })
        .collect();
    request.options = options;
    request
}
#[test]
fn cycle_preserves_exact_graph_and_native_fingerprint() {
    let input = project_request(
        "cycle",
        &[
            ("src/a.py", "from b import y\n"),
            ("src/b.py", "from a import x\ny=2\n"),
        ],
        json!({"source_roots":["src"]}),
    );
    let output = analyze(&input);
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert_eq!(output.extra["py_cycles"], json!(1));
    assert_eq!(
        output.observation.findings[0].fingerprint,
        finding::sha1_short("cycle-", "python:b:a")
    );
    assert_eq!(
        output.observation.findings[0].message,
        "import cycle: a -> b -> a"
    );
    assert_eq!(
        output.observation.findings[0].evidence,
        ici_domain::EvidenceLevel::Measured
    );
    let target = output
        .targets
        .iter()
        .find(|target| target.target_name == "Cycle:2")
        .unwrap();
    assert_eq!(target.file_path, "src/a.py");
    assert_eq!(target.metrics["modules"], json!(["a", "b"]));
}
#[test]
fn package_relative_imports_and_source_root_boundaries_are_preserved() {
    let input = project_request(
        "cycle",
        &[
            ("src/pkg/__init__.py", "from . import a\n"),
            ("src/pkg/a.py", "from .b import value\n"),
            ("src/pkg/b.py", "from .a import value\n"),
        ],
        json!({"source_roots":["src"]}),
    );
    let output = analyze(&input);
    assert_eq!(output.extra["py_cycles"], json!(1));
    let edges = output.extra["import_edges"].as_array().unwrap();
    assert!(
        edges
            .iter()
            .any(|edge| edge["from"] == "pkg" && edge["to"] == "pkg.a")
    );
    assert!(
        edges
            .iter()
            .any(|edge| edge["from"] == "pkg.a" && edge["to"] == "pkg.b")
    );
    assert!(
        edges
            .iter()
            .any(|edge| edge["from"] == "pkg.b" && edge["to"] == "pkg.a")
    );
    let input = project_request(
        "cycle",
        &[
            ("pkg/__init__.py", "from . import a\n"),
            ("pkg/a.py", "from . import b\n"),
            ("pkg/b.py", "from . import a\n"),
        ],
        json!({"component_root":"pkg"}),
    );
    let output = analyze(&input);
    assert_eq!(output.extra["py_cycles"], json!(1));
    assert_eq!(
        output.extra["module_index"]["__init__"],
        json!("pkg/__init__.py")
    );
}
#[test]
fn stdlib_collision_and_package_attributes_do_not_invent_edges() {
    let input = project_request(
        "cycle",
        &[
            ("src/pkg/html.py", "import pkg.other\n"),
            ("src/pkg/other.py", "import html\n"),
            ("src/pkg/__init__.py", "child=1\n"),
            ("src/pkg/child.py", "import pkg.user\n"),
            ("src/pkg/user.py", "from pkg import child\n"),
        ],
        json!({"source_roots":["src"]}),
    );
    let output = analyze(&input);
    assert_eq!(output.extra["py_cycles"], json!(0));
    assert!(
        !output.extra["import_edges"]
            .as_array()
            .unwrap()
            .iter()
            .any(|edge| edge["from"] == "pkg.user" && edge["to"] == "pkg.child")
    );
}
#[test]
fn ambiguous_suffix_and_invalid_sources_are_incomplete_graphs() {
    let input = project_request(
        "cycle",
        &[
            ("one/b.py", "import a\n"),
            ("two/b.py", "import a\n"),
            ("a.py", "import b\n"),
        ],
        json!({}),
    );
    let output = analyze(&input);
    assert_eq!(output.observation.state, TaskState::Failed);
    assert!(
        output
            .observation
            .limitations
            .iter()
            .any(|text| text.contains("ambiguous-import"))
    );
    let output = analyze(&request("cycle", "def broken(:\n"));
    assert_eq!(output.observation.state, TaskState::Failed);
}
#[test]
fn iterative_scc_handles_deep_graph_and_real_cycle_edges() {
    use std::collections::BTreeSet;
    let mut graph: Vec<_> = (0..5000).map(|index| BTreeSet::from([index + 1])).collect();
    graph.push(BTreeSet::from([0]));
    let components = cycle::components(&graph);
    assert_eq!(components.len(), 1);
    assert_eq!(components[0].len(), 5001);
}
#[test]
fn fixed_python_import_graphs_and_explicit_corrected_expectations_match() {
    use std::collections::BTreeSet;
    let fixture: serde_json::Value =
        serde_json::from_str(include_str!("../../../tests/rust-parity/python/cycle.json")).unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let sources: Vec<_> = case["sources"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(path, source)| (path.as_str(), source.as_str().unwrap()))
            .collect();
        let input = project_request(
            "cycle",
            &sources,
            json!({"source_roots":case["source_roots"]}),
        );
        let output = analyze(&input);
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{}: {:?}",
            case["id"],
            output.observation.limitations
        );
        let edges: BTreeSet<_> = output.extra["import_edges"]
            .as_array()
            .unwrap()
            .iter()
            .map(|edge| {
                vec![
                    edge["from"].as_str().unwrap().to_owned(),
                    edge["to"].as_str().unwrap().to_owned(),
                ]
            })
            .collect();
        let expected = case
            .get("corrected_edges")
            .unwrap_or(&case["original_edges"]);
        assert_eq!(
            serde_json::to_value(edges).unwrap(),
            *expected,
            "{}",
            case["id"]
        );
        let cycles = case
            .get("corrected_cycles")
            .unwrap_or(&case["original_cycles"])
            .as_array()
            .unwrap()
            .len();
        assert_eq!(output.extra["py_cycles"], json!(cycles), "{}", case["id"]);
        if case["id"] == "source-precedence" {
            assert_eq!(output.extra["module_index"]["a"], json!("first/a.py"));
        }
    }
}
#[test]
fn relative_import_leaving_declared_package_is_incomplete_instead_of_guessed() {
    let input = project_request(
        "cycle",
        &[
            ("pkg/a.py", "from ..b import x\n"),
            ("b.py", "import pkg.a\n"),
        ],
        json!({"source_roots":["."]}),
    );
    let output = analyze(&input);
    assert_eq!(output.observation.state, TaskState::Failed);
    assert!(
        output
            .observation
            .limitations
            .iter()
            .any(|message| message.contains("relative-import-context"))
    );
    assert_eq!(output.extra["py_cycles"], json!(0));
}

#[test]
fn historical_explicit_producer_details_retain_native_evidence() {
    let output = analyze(&request(
        "dead",
        "def _unused():\n    return 1\n    absent()\n",
    ));
    assert_eq!(output.observation.findings.len(), 2);
    for finding in &output.observation.findings {
        let detail = &output.extra["finding_details"][&finding.fingerprint];
        assert_eq!(detail["legacy_rule_id"], "ici.legacy.dead.target");
        assert_eq!(detail["legacy_confidence"], finding.confidence);
        assert_eq!(
            detail["explanation"],
            "Python dead-code evidence is derived from bounded AST reachability and name-reference analysis."
        );
        assert!(
            detail["remediation"]
                .as_str()
                .unwrap()
                .contains("intended reference")
        );
    }
    let output = analyze(&request(
        "resource",
        "def first(x=[]):\n    return x\ndef second(x=[]):\n    return x\n",
    ));
    assert_eq!(output.observation.findings.len(), 2);
    for finding in &output.observation.findings {
        let detail = &output.extra["finding_details"][&finding.fingerprint];
        assert_eq!(detail["legacy_rule_id"], "ici.legacy.resource.target");
        assert_eq!(detail["legacy_label"], "");
        assert_eq!(detail["legacy_confidence"], "exact");
        assert_eq!(detail["tool_name"], "ici Python AST");
        assert_eq!(detail["tool_rule_id"], "Correctness:MutableDefault");
        assert_eq!(detail["snippet"], "parameter=<mutable value>");
    }
}

#[test]
fn source_encodings_keep_frozen_intake_ast_and_raw_unicode_boundaries_visible() {
    use base64::Engine;
    use ruff_python_ast::{
        Expr, Stmt,
        visitor::{self, Visitor},
    };
    use ruff_text_size::Ranged;
    struct Shapes<'a> {
        file: &'a ParsedFile,
        functions: Vec<Value>,
        names: Vec<Value>,
    }
    fn row(name: String, span: ici_domain::SourceSpan) -> Value {
        json!({"name":name,"start_line":span.start_line,"start_column":span.start_column,"end_line":span.end_line,"end_column":span.end_column})
    }
    impl<'a> Visitor<'a> for Shapes<'a> {
        fn visit_stmt(&mut self, node: &'a Stmt) {
            if let Stmt::FunctionDef(function) = node {
                self.functions.push(row(
                    function.name.to_string(),
                    self.file
                        .source
                        .span(crate::ast_util::function_range(self.file, function)),
                ));
            }
            visitor::walk_stmt(self, node);
        }
        fn visit_expr(&mut self, node: &'a Expr) {
            if let Expr::Name(name) = node {
                self.names.push(row(
                    name.id.to_string(),
                    self.file.source.span(name.range()),
                ));
            }
            visitor::walk_expr(self, node);
        }
    }
    fn sorted(rows: &mut [Value]) {
        rows.sort_by_key(|row| {
            (
                row["start_line"].as_u64(),
                row["start_column"].as_u64(),
                row["end_line"].as_u64(),
                row["end_column"].as_u64(),
                row["name"].as_str().unwrap().to_owned(),
            )
        });
    }
    let evidence: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/source-encoding.json"
    ))
    .unwrap();
    let mut differences = vec![];
    for case in evidence["cases"].as_array().unwrap() {
        let input = SourceInput {
            path: "main.py".into(),
            bytes: base64::engine::general_purpose::STANDARD
                .decode(case["bytes_base64"].as_str().unwrap())
                .unwrap(),
        };
        let expected = &case["expected"];
        let decoded = SourceDocument::decode(&input);
        if expected["source_accepted"] == false {
            match decoded {
                Ok(_) => differences.push(format!("{}: invalid source was decoded", case["id"])),
                Err(error) => {
                    if error.code != expected["source_error"].as_str().unwrap() {
                        differences.push(format!("{}: source code {}", case["id"], error.code));
                    }
                }
            }
            continue;
        }
        let decoded = match decoded {
            Ok(source) => source,
            Err(error) => {
                differences.push(format!("{}: decode failed {error:?}", case["id"]));
                continue;
            }
        };
        assert_eq!(
            decoded.text,
            expected["text"].as_str().unwrap(),
            "{}",
            case["id"]
        );
        for boundary in expected["raw_boundaries"].as_array().unwrap() {
            assert_eq!(
                decoded.raw_offset(boundary[0].as_u64().unwrap() as usize),
                boundary[1].as_u64().map(|offset| offset as usize),
                "{} {boundary:?}",
                case["id"]
            );
        }
        for offset in 0..decoded.text.len() {
            if !decoded.text.is_char_boundary(offset) {
                assert_eq!(decoded.raw_offset(offset), None, "{}", case["id"]);
            }
        }
        let file = match ParsedFile::parse(decoded, "3.14") {
            Ok(file) => {
                if expected["parser_accepted"] == false {
                    differences.push(format!("{}: invalid syntax was parsed", case["id"]));
                    continue;
                }
                file
            }
            Err(error) => {
                if expected["parser_accepted"] == true {
                    differences.push(format!("{}: parse failed {error:?}", case["id"]));
                }
                continue;
            }
        };
        let mut shapes = Shapes {
            file: &file,
            functions: vec![],
            names: vec![],
        };
        shapes.visit_body(file.body());
        sorted(&mut shapes.functions);
        sorted(&mut shapes.names);
        if shapes.functions != *expected["functions"].as_array().unwrap()
            || shapes.names != *expected["names"].as_array().unwrap()
        {
            differences.push(format!(
                "{}: native shapes {}",
                case["id"],
                json!({"functions":shapes.functions,"names":shapes.names})
            ));
        }
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
}

#[test]
fn source_codec_failures_and_raw_byte_cache_identity_cannot_reuse_other_evidence() {
    let mut input = request(
        "complexity",
        "# coding: cp949\ndef hello(value):\n    return value\n",
    );
    let mut session = AnalysisSession::new();
    assert_eq!(
        session.analyze(&input).observation.state,
        TaskState::Succeeded
    );
    assert_eq!(session.parser_invocations(), 1);
    input.check_id = "python.cognitive".into();
    assert_eq!(
        session.analyze(&input).observation.state,
        TaskState::Succeeded
    );
    assert_eq!(
        session.parser_invocations(),
        1,
        "Same immutable source shares the parser across measures"
    );
    input.inputs[0].bytes = b"# coding: shift_jis\ndef hello(value):\n    return value\n".to_vec();
    let output = session.analyze(&input);
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert_eq!(
        session.parser_invocations(),
        2,
        "A changed encoding declaration is a different raw input"
    );
    assert_eq!(output.extra["source_encodings"]["main.py"], "shift-jis");
    input.inputs.push(SourceInput {
        path: "bad.py".into(),
        bytes: b"# coding: cp949\nx = '\x81".to_vec(),
    });
    let output = session.analyze(&input);
    assert_eq!(output.observation.state, TaskState::Failed);
    assert_eq!(output.status, "ERROR");
    assert!(
        output
            .targets
            .iter()
            .any(|target| target.file_path == "main.py" && target.status == "PASS")
    );
    assert!(
        output
            .targets
            .iter()
            .any(|target| target.file_path == "bad.py"
                && target.status == "ERROR"
                && target.start_line == 2
                && target.start_column == Some(6))
    );
    assert!(
        session.source_facts(&input).is_err(),
        "A partial source inventory cannot authorize runtime probes"
    );
}

#[test]
fn active_multibyte_decode_cancellation_retains_finished_line_evidence() {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::time::{Duration, Instant};
    for (header, atom) in [
        (b"# coding: cp949\n".as_slice(), b"\xc7\xd1".as_slice()),
        (
            b"# coding: iso2022_jp\n".as_slice(),
            b"\x1b$BF|\x1b(B".as_slice(),
        ),
        (b"# coding: hz\n".as_slice(), b"~{F|~}".as_slice()),
    ] {
        let mut input = request("line", "answer=1\n");
        let mut bytes = header.to_vec();
        let count = (crate::source::MAX_SOURCE_BYTES - bytes.len()) / atom.len();
        bytes.extend_from_slice(&atom.repeat(count));
        input.inputs.push(SourceInput {
            path: "large.py".into(),
            bytes,
        });
        let flag = AtomicBool::new(false);
        let started = Instant::now();
        let output = std::thread::scope(|scope| {
            let worker = scope.spawn(|| {
                std::thread::sleep(Duration::from_millis(2));
                flag.store(true, Ordering::Relaxed);
            });
            let output = AnalysisSession::new().analyze_cancellable(&input, &flag);
            worker.join().unwrap();
            output
        });
        assert_eq!(output.observation.state, TaskState::Cancelled);
        assert_eq!(output.status, "ERROR");
        assert!(started.elapsed() < Duration::from_secs(2));
        assert!(
            output
                .targets
                .iter()
                .any(|target| target.file_path == "main.py" && target.status == "PASS")
        );
        assert!(
            !output
                .targets
                .iter()
                .any(|target| target.file_path == "large.py")
        );
        assert!(output.observation.findings.is_empty());
    }
}

#[test]
fn shared_source_exclusions_use_frozen_full_casefold_for_every_path_part() {
    let evidence: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/source-encoding.json"
    ))
    .unwrap();
    for case in evidence["exclusions"].as_array().unwrap() {
        assert_eq!(
            json!(source_exclusion_reasons(case["path"].as_str().unwrap())),
            case["original_reasons"]
        );
    }
    assert_eq!(
        source_exclusion_reasons("node_moduleſ/checked.py"),
        vec!["vendor"]
    );
    assert_eq!(
        source_exclusion_reasons("SUBPROJECTſ/MOCſ_compilation.py"),
        vec!["vendor", "generated"]
    );
    assert_eq!(
        source_exclusion_reasons("src/mocſ_compilation.py"),
        vec!["generated"]
    );
    assert_eq!(source_exclusion_reasons("src/owned.py"), Vec::<&str>::new());
}
