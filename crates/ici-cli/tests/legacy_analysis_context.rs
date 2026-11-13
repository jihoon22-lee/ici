//! The historical `verify` report's top-level `analysis_context` against the frozen CLI
//! (`tests/rust-parity/cpp/capture_analysis_context.py`) for C++, Python and hybrid projects.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, process::Command};

#[path = "support/host_tools.rs"]
mod host_tools;

fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

fn run(oracle: &Value, name: &str, path: &str) -> Value {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    fs::create_dir_all(&root).unwrap();
    let (fixture, kind) = (
        oracle["case_projects"][name][0].as_str(),
        oracle["case_projects"][name][1].as_str().unwrap(),
    );
    if let Some(fixture) = fixture {
        copy_tree(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../parity-fixtures/cpp")
                .join(fixture),
            &root,
        );
    }
    if kind != "cpp" {
        for (name, text) in oracle["python_files"].as_object().unwrap() {
            let path = root.join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, text.as_str().unwrap()).unwrap();
        }
    }
    for (relative, text) in oracle["case_files"][name].as_object().into_iter().flatten() {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    let source_dirs = oracle["case_source_dirs"]
        .get(name)
        .map_or_else(|| json!(["src"]), Value::clone);
    fs::write(
        root.join("ici.toml"),
        format!(
            "[project]\ntype='{kind}'\nsource_dirs={source_dirs}\n{}",
            oracle["case_configs"][name].as_str().unwrap_or_default()
        ),
    )
    .unwrap();
    for args in [
        &["init", "-q", "."][..],
        &["add", "-A"],
        &["commit", "-qm", "fixture"],
    ] {
        let status = Command::new("git")
            .args([
                "-c",
                "user.email=ici@example.invalid",
                "-c",
                "user.name=ici",
            ])
            .args(args)
            .current_dir(&root)
            .status()
            .unwrap();
        assert!(status.success());
    }
    if let Some(tag) = oracle["case_tags"][name].as_str() {
        let status = Command::new("git")
            .args(["tag", tag])
            .current_dir(&root)
            .status()
            .unwrap();
        assert!(status.success());
    }
    let mut environment = BTreeMap::from([
        ("PATH".to_string(), path.to_string()),
        ("HOME".to_string(), work.path().display().to_string()),
        ("LC_CTYPE".to_string(), "C.UTF-8".to_string()),
    ]);
    for (key, value) in oracle["case_env"][name].as_object().into_iter().flatten() {
        environment.insert(
            key.clone(),
            value
                .as_str()
                .unwrap()
                .replace("<ROOT>", &root.display().to_string()),
        );
    }
    if fixture == Some("test-qmake") {
        // The qmake capture re-enters the ici binary (`__qmake-configure-capture`,
        // `__qmake-record`), so the real CLI runs.
        let output = Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(["verify", "--report"])
            .current_dir(&root)
            .env_clear()
            .envs(&environment)
            .output()
            .unwrap();
        assert!(output.status.code().is_some(), "{name}");
    } else {
        commands::dispatch(
            &registry::parse(["ici", "verify", "--report"]).unwrap(),
            &root,
            &environment,
        )
        .unwrap();
    }
    let text = fs::read_to_string(root.join("verify_report.json")).unwrap();
    let report: Value =
        serde_json::from_str(&text.replace(&root.display().to_string(), "<ROOT>")).unwrap();
    report["analysis_context"].clone()
}

fn digest(value: &Value) -> bool {
    value.as_str().is_some_and(|text| {
        text.strip_prefix("sha256:")
            .is_some_and(|hex| hex.len() == 64 && hex.bytes().all(|b| b.is_ascii_hexdigit()))
    })
}

/// Checks the run-specific values (commit, digests, shadow paths, sizes) for their shape and
/// removes them. Recorded difference: ici prepares its shadow builds under `.ici/rust-v1/build`
/// (frozen `build/ici-cmake*`); digests are the native identities. The release analysis
/// database is configured with frozen's `-DCMAKE_UNITY_BUILD=OFF`, so `unity_build` is compared.
fn comparable(mut context: Value, name: &str, tag: Option<&str>) -> Value {
    assert_eq!(
        context["schema_version"], "ici.analysis-context/v1",
        "{name}"
    );
    let commit = context["identity"]["source_commit"]
        .as_str()
        .unwrap()
        .to_owned();
    assert!(
        commit.len() == 40 && commit.bytes().all(|b| b.is_ascii_hexdigit()),
        "{name}"
    );
    // Frozen `git describe --tags --always`: the tag on a tagged commit, else the short commit.
    let version = tag.map_or_else(|| format!("v{}", &commit[..7]), str::to_owned);
    assert_eq!(context["project"]["version"], version, "{name}");
    context["project"]["version"] = Value::Null;
    for key in ["config_digest", "toolchain_digest"] {
        assert!(digest(&context["identity"][key]), "{name} {key}");
    }
    context["identity"] = Value::Null;
    let compilation = &mut context["compilation"];
    if compilation["database_path"].is_string() {
        assert!(
            compilation["database_path"]
                .as_str()
                .unwrap()
                .ends_with("/compile_commands.json")
        );
        assert!(digest(&compilation["database_digest"]), "{name}");
        compilation["database_path"] = json!("<shadow>");
        compilation["database_digest"] = Value::Null;
    }
    for unit in compilation["units"].as_array_mut().unwrap() {
        assert!(digest(&unit["configuration"]), "{name}");
        for key in ["directory", "output", "configuration"] {
            unit[key] = Value::Null;
        }
    }
    for manifest in context["artifact_manifests"].as_array_mut().unwrap() {
        assert_eq!(manifest["source_commit"], commit.as_str(), "{name}");
        for key in ["config_digest", "toolchain_digest"] {
            assert!(digest(&manifest[key]), "{name} {key}");
            manifest[key] = Value::Null;
        }
        for key in ["source_commit", "shadow_root"] {
            manifest[key] = Value::Null;
        }
        for artifact in manifest["artifacts"].as_array_mut().unwrap() {
            assert!(digest(&artifact["sha256"]), "{name}");
            assert!(artifact["size"].as_u64().unwrap() > 0, "{name}");
            for key in ["command", "sha256", "size"] {
                artifact[key] = Value::Null;
            }
        }
    }
    context
}

/// Positive: a CMake project reports its root descriptor, sources, the configured release
/// database's units (compiler `[external]`, project-relative sources) and the coverage/sanitize
/// manifests. Negative: a Python project has no backend, no database (null path, no digest) and
/// no manifests, but the same standard-profile requested variants. A hybrid project carries both.
/// A project enabling unity builds through its cache keeps one unit per source with
/// `unity_build` false: the release analysis database is configured `-DCMAKE_UNITY_BUILD=OFF`.
/// A tagged checkout reports the tag (`cpp-tagged`), and a project with `project.cpp_pkg_config`
/// appends the flags `verify` probed while it prepared, on a fallback-linted run
/// (`cpp-pkg-fallback`; root ruling 2026-11-13) and on CMake and qmake database runs alike
/// (`cpp-pkg-cmake`, `cpp-pkg-qmake`; root ruling 2026-11-14). The qmake case runs the real CLI
/// and compares its `project` object (its database units are compared by legacy_qmake_verify).
#[test]
#[ignore = "explicit actual CMake/ctest/g++/qmake runs: /usr/bin/cmake (4.2.3), ctest, /usr/bin/g++, /usr/bin/qmake6 and /usr/bin/pkg-config must be the captured tools"]
fn legacy_verify_analysis_context_matches_frozen_for_cpp_python_and_hybrid() {
    let oracle = oracle();
    let cmake = Command::new("cmake").arg("--version").output().unwrap();
    assert_eq!(
        String::from_utf8(cmake.stdout).unwrap().lines().next(),
        oracle["cmake"].as_str()
    );
    replay(&oracle, "/usr/bin:/bin", false);
}

/// The CI twin with the host's own cmake/ctest/g++ (any release, as on the GitHub runner) behind
/// a mirror of `/usr/bin` and `/bin` without clang*/clazy* (the capture host had none;
/// `support/host_tools.rs`). Incidental mask: each unit's `compiler`, the resolved driver name
/// frozen records (`x86_64-linux-gnu-g++-<major>`), names the host GCC release.
#[test]
fn legacy_verify_analysis_context_matches_frozen_with_the_host_cmake() {
    let host = host_tools::mirror(&host_tools::NO_LLVM);
    replay(&oracle(), &host.path, true);
}

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/analysis-context-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

fn replay(oracle: &Value, path: &str, host: bool) {
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 9);
    // Independent literals (root ruling 2026-11-13): a tagged checkout's version is the tag, and
    // the observed pkg-config flags follow the sorted include directories in package order, a
    // package pkg-config refuses (`absent-pkg`) adding none.
    assert_eq!(cases["cpp-tagged"]["project"]["version"], "v2.4.1");
    assert_eq!(
        cases["cpp-pkg-fallback"]["project"]["cpp_include_flags"],
        json!([
            "-Iinclude",
            "-Iinclude/demo",
            "-Ivendor/include",
            "-DDEMO_PKG=1",
            "-DSECOND_PKG"
        ])
    );
    // Root ruling 2026-11-14: frozen resolves the packages while it discovers the project model,
    // so the CMake and qmake database runs carry them too (no `include/` there).
    for case in ["cpp-pkg-cmake", "cpp-pkg-qmake"] {
        assert_eq!(
            cases[case]["project"]["cpp_include_flags"],
            json!(["-Ivendor/include", "-DDEMO_PKG=1", "-DSECOND_PKG"]),
            "{case}"
        );
        assert!(
            cases[case]["compilation"]["database_path"]
                .as_str()
                .is_some_and(|path| path.ends_with("/compile_commands.json")),
            "{case}"
        );
    }
    assert_eq!(cases["cpp-pkg-cmake"]["project"]["backend"], "cmake");
    assert_eq!(cases["cpp-pkg-qmake"]["project"]["backend"], "qmake");
    assert_eq!(cases["cpp-unity"]["compilation"]["unity_build"], false);
    assert_eq!(
        cases["cpp-unity"]["compilation"]["units"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert!(cases["python"]["compilation"]["database_path"].is_null());
    assert!(
        cases["python"]["compilation"]
            .get("database_digest")
            .is_none()
    );
    // A missing adapter tool is reported, and is a failure under ICI_REQUIRE_BUILD_ADAPTERS=1.
    let required = std::env::var("ICI_REQUIRE_BUILD_ADAPTERS").as_deref() == Ok("1");
    let mut mismatches = vec![];
    for (kind, frozen) in cases {
        let qmake = oracle["case_projects"][kind][0] == "test-qmake";
        if qmake && host_tools::version(Path::new("/usr/bin/qmake6")).is_none() {
            assert!(!required, "{kind}: /usr/bin/qmake6 is required");
            println!("{kind}: no /usr/bin/qmake6 on this host; this case did not run");
            continue;
        }
        let native = run(oracle, kind, path);
        if kind == "python" {
            assert!(native["compilation"].get("database_digest").is_none());
        }
        let tag = oracle["case_tags"][kind].as_str();
        let (mut native, mut frozen) = (
            comparable(native, kind, tag),
            comparable(frozen.clone(), kind, tag),
        );
        if qmake {
            native = native["project"].clone();
            frozen = frozen["project"].clone();
        } else if host {
            for context in [&mut native, &mut frozen] {
                for unit in context["compilation"]["units"].as_array_mut().unwrap() {
                    unit["compiler"] = json!("<host g++>");
                }
            }
        }
        if native != frozen {
            eprintln!(
                "== {kind}\nfrozen: {}\nnative: {}",
                serde_json::to_string_pretty(&frozen).unwrap(),
                serde_json::to_string_pretty(&native).unwrap()
            );
            mismatches.push(kind.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
