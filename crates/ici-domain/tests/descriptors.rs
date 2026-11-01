//! The 19 frozen engine descriptors (core/pipeline.py) against the native registry metadata.
use ici_domain::{LegacyDescriptor, Profile, builtin_catalog, validate_legacy_descriptors};
use serde_json::{Value, json};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/oracle/descriptors-oracle.json"
    ))
    .unwrap()
}
fn profile_name(profile: Profile) -> &'static str {
    match profile {
        Profile::Fast => "fast",
        Profile::Standard => "standard",
        Profile::Deep => "deep",
    }
}

#[test]
fn native_descriptor_metadata_and_profile_selection_equal_the_frozen_pipeline() {
    let oracle = oracle();
    let native = builtin_catalog().legacy;
    let actual: Vec<Value> = native
        .iter()
        .map(|item| {
            let mut profiles: Vec<_> = item.profiles.iter().map(|p| profile_name(*p)).collect();
            profiles.sort();
            json!({
                "name": item.name,
                "factory_name": item.factory_name,
                "dependencies": item.dependencies,
                "produces": item.produces,
                "consumes": item.consumes,
                "profiles": profiles,
                "execution": item.execution,
                "build_variant": item.build_variant,
            })
        })
        .collect();
    let expected: Vec<Value> = oracle["descriptors"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| {
            let mut item = item.clone();
            item.as_object_mut().unwrap().remove("factory_resolves");
            item
        })
        .collect();
    assert_eq!(Value::from(actual), Value::from(expected));
    for profile in [Profile::Fast, Profile::Standard, Profile::Deep] {
        let selected: Vec<_> = native
            .iter()
            .filter(|item| item.profiles.contains(&profile))
            .map(|item| item.name.clone())
            .collect();
        assert_eq!(
            Value::from(selected),
            oracle["profiles"][profile_name(profile)],
            "{profile:?}"
        );
    }
    assert!(validate_legacy_descriptors(&native).is_ok());
}

#[test]
fn every_native_check_family_maps_to_a_frozen_descriptor() {
    let native = builtin_catalog();
    let names: Vec<_> = native
        .legacy
        .iter()
        .map(|item| item.name.as_str())
        .collect();
    for check in &native.checks {
        // Ecosystem checks (Rust/JS/TS) have no frozen legacy engine of their own; they report
        // under `lint`/`type` like their frozen next-path projection.
        let family = match check.id.as_str() {
            "rust.clippy" | "javascript.lint" | "typescript.lint" => "lint",
            "typescript.type" => "type",
            "python.compat-runtime" | "python.compat" => "python_compat",
            "cpp.compile" => "compile_db",
            "cpp.artifact" => "build",
            "cpp.tsan" => "thread_sanitize",
            "cpp.binary-compat" => "binary_compat",
            "integration" => "integration",
            id => id.split_once('.').unwrap().1,
        };
        let family = match family {
            "format" | "diagnostics" | "tidy" => "lint",
            "coverage" => "test",
            other => other,
        };
        assert!(names.contains(&family), "{} -> {family}", check.id);
    }
}

#[test]
fn malformed_descriptor_graphs_are_refused_with_the_frozen_messages() {
    let oracle = oracle();
    let base = builtin_catalog().legacy;
    let named = |name: &str| base.iter().find(|item| item.name == name).unwrap().clone();
    let mut outside: Vec<LegacyDescriptor> = base[..base.len() - 1].to_vec();
    let mut integration = named("integration");
    integration.profiles = vec![Profile::Fast, Profile::Standard, Profile::Deep];
    outside.push(integration);
    let mut duplicate = base.clone();
    let mut line2 = named("line");
    line2.name = "line2".into();
    duplicate.push(line2);
    let mut unknown = base.clone();
    let mut x = named("line");
    x.name = "x".into();
    x.dependencies = vec!["nope".into()];
    x.produces = vec![];
    unknown.push(x);
    for (case, descriptors) in [
        ("dependency_outside_profile", outside),
        ("duplicate_artifact", duplicate),
        ("unknown_dependency", unknown),
    ] {
        assert_eq!(
            validate_legacy_descriptors(&descriptors).unwrap_err(),
            oracle["refusals"][case].as_str().unwrap(),
            "{case}"
        );
    }
    let mut cyclic = base.clone();
    cyclic
        .iter_mut()
        .find(|item| item.name == "build")
        .unwrap()
        .dependencies = vec!["integration".into()];
    assert!(
        validate_legacy_descriptors(&cyclic)
            .unwrap_err()
            .starts_with("verification pipeline contains a cycle")
    );
    assert!(validate_legacy_descriptors(&[]).is_err());
}
