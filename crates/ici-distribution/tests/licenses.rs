use ici_distribution::{LicenseSource, collect_licenses};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn package(root: &Path, name: &str, source: Value, kind: &str) -> Value {
    fs::create_dir_all(root).unwrap();
    fs::write(
        root.join("Cargo.toml.orig"),
        format!("[package]\nname = '{name}'\nlicense = 'MIT'\n"),
    )
    .unwrap();
    fs::write(
        root.join("README.md"),
        format!("{name} upstream declaration fixture\n"),
    )
    .unwrap();
    fs::write(
        root.join(".cargo_vcs_info.json"),
        r#"{"git":{"sha1":"0123456789abcdef0123456789abcdef01234567"},"path_in_vcs":""}"#,
    )
    .unwrap();
    json!({"id":name,"name":name,"version":"1.0.0","source":source,"repository":"https://example.invalid/upstream","manifest_path":root.join("Cargo.toml"),"license":"MIT","license_file":null,"authors":["Recorded upstream author"],"targets":[{"kind":[kind]}]})
}
#[test]
fn normal_build_proc_macro_roles_and_missing_upstream_notice_remain_distinct() {
    let root = tempfile::tempdir().unwrap();
    let workspace = root.path().join("workspace");
    let product = package(&workspace.join("product"), "product", Value::Null, "bin");
    fs::write(
        workspace.join("LICENSE"),
        "Actual workspace MIT notice\nCopyright upstream fixture\n",
    )
    .unwrap();
    let proc_macro = package(
        &root.path().join("macro"),
        "macro",
        json!("registry+https://github.com/rust-lang/crates.io-index"),
        "proc-macro",
    );
    let helper = package(
        &root.path().join("helper"),
        "helper",
        json!("registry+https://github.com/rust-lang/crates.io-index"),
        "lib",
    );
    let runtime = package(
        &root.path().join("runtime"),
        "runtime",
        json!("registry+https://github.com/rust-lang/crates.io-index"),
        "lib",
    );
    fs::write(
        root.path().join("runtime/LICENSE"),
        "Actual runtime MIT notice\n",
    )
    .unwrap();
    let extra = root.path().join("toolchain-LICENSE");
    fs::write(&extra, "Actual toolchain terms\n").unwrap();
    let edge = |id: &str, kind: Value| json!({"pkg":id,"dep_kinds":[{"kind":kind,"target":null}]});
    let metadata = json!({"workspace_root":workspace,"packages":[product,proc_macro,helper,runtime],"resolve":{"nodes":[
        {"id":"product","deps":[edge("macro",Value::Null),edge("runtime",Value::Null),edge("missing-dev",json!("dev"))]},
        {"id":"macro","deps":[edge("helper",Value::Null)]},{"id":"helper","deps":[]},{"id":"runtime","deps":[]}
    ]}});
    let input = root.path().join("metadata.json");
    fs::write(&input, serde_json::to_vec(&metadata).unwrap()).unwrap();
    let output = root.path().join("NOTICES.txt");
    let inventory = collect_licenses(
        &input,
        "product",
        "x86_64-unknown-linux-gnu",
        &[LicenseSource {
            id: "toolchain".into(),
            source: "https://example.invalid/toolchain".into(),
            declared_license: "MIT".into(),
            path: extra,
        }],
        &output,
        &root.path().join("licenses.json"),
    )
    .unwrap();
    let packages: BTreeMap<_, _> = inventory
        .packages
        .iter()
        .map(|package| (package.name.as_str(), package))
        .collect();
    assert_eq!(packages.len(), 4);
    assert_eq!(packages["macro"].roles, ["proc_macro_host"]);
    assert_eq!(packages["helper"].roles, ["host_dependency"]);
    assert_eq!(packages["runtime"].roles, ["target_dependency"]);
    assert_eq!(
        packages["helper"].evidence_status,
        "missing_upstream_notice"
    );
    assert_eq!(packages["helper"].authors, ["Recorded upstream author"]);
    assert_eq!(
        packages["runtime"].evidence_status,
        "source_notices_retained"
    );
    let text = fs::read_to_string(output).unwrap();
    assert!(text.contains("SPDX MIT standard terms, declaration reference only"));
    assert!(text.contains("No upstream copyright notice is invented"));
    assert!(text.contains("Actual toolchain terms"));
    assert!(!text.contains("Copyright (c) 2026 ici contributors"));
}
#[test]
fn credential_source_urls_and_escaped_license_file_are_rejected_without_copying_text() {
    let root = tempfile::tempdir().unwrap();
    let workspace = root.path().join("workspace");
    let mut product = package(
        &workspace.join("product"),
        "product",
        json!("registry+https://credential-secret@example.invalid/index"),
        "bin",
    );
    let input = root.path().join("metadata.json");
    let mut metadata = json!({"workspace_root":workspace,"packages":[product],"resolve":{"nodes":[{"id":"product","deps":[]}]}});
    fs::write(&input, serde_json::to_vec(&metadata).unwrap()).unwrap();
    let result = collect_licenses(
        &input,
        "product",
        "x86_64-unknown-linux-gnu",
        &[],
        &root.path().join("NOTICES.txt"),
        &root.path().join("licenses.json"),
    )
    .unwrap_err();
    assert!(!result.to_string().contains("credential-secret"));
    product = metadata["packages"][0].clone();
    product["source"] = json!("registry+https://github.com/rust-lang/crates.io-index");
    product["license_file"] = json!("../../private-key");
    metadata["packages"][0] = product;
    fs::write(
        root.path().join("private-key"),
        "private source must not enter notices",
    )
    .unwrap();
    fs::write(&input, serde_json::to_vec(&metadata).unwrap()).unwrap();
    assert!(
        collect_licenses(
            &input,
            "product",
            "x86_64-unknown-linux-gnu",
            &[],
            &root.path().join("NOTICES.txt"),
            &root.path().join("licenses.json")
        )
        .is_err()
    );
    assert!(!root.path().join("NOTICES.txt").exists());
}
