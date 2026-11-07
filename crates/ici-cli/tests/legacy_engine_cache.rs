//! Frozen legacy `ici verify` engine cache fields (`VerifyOrchestrator.run_all`: per-engine
//! `AnalysisCache` key, `cache_hit` on reuse, no key under `--no-cache`) through the binary:
//! `tests/rust-parity/runtime/legacy-engine-cache-oracle.json` (`capture_legacy_engine_cache.py`).
//! The legacy view projects the native observation cache (keys in the native namespace, a hit
//! only when every check the engine projects was reused); the engines whose reuse differs are
//! listed with the reason, any other drift fails.
use serde_json::Value;
use std::{collections::BTreeMap, fs, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/runtime/legacy-engine-cache-oracle.json");

/// `(cache_hit, keyed)` of one engine.
type Cache = (bool, bool);
/// (engine, frozen on the warm run, native on the warm run, reason).
const REVIEWED: [(&str, Cache, Cache, &str); 6] = [
    (
        "type",
        (true, true),
        (false, false),
        "decisions.md 타입 기본값: the unconfigured type check is ty, refused when ty is missing; a blocked check is never stored (frozen cached its mypy-missing AST fallback)",
    ),
    (
        "lint",
        (false, true),
        (false, false),
        "Ruff is missing: the blocked native checks get no observation key (frozen keyed the engine but did not reuse it)",
    ),
    (
        "compile_db",
        (false, true),
        (true, true),
        "the native no-database compile_db verdict is a complete in-process check and is reused",
    ),
    (
        "dead",
        (false, false),
        (true, true),
        "native dead-code is a complete in-process check and is reused (frozen DeadCodeEngine was not cache-safe)",
    ),
    (
        "sanitize",
        (false, false),
        (false, true),
        "the native sanitize check is keyed; its pytest-backed evidence is never reused",
    ),
    (
        "python_compat",
        (false, false),
        (false, false),
        "same as frozen (listed so the table names every engine without a warm reuse)",
    ),
];

fn engines(root: &std::path::Path) -> BTreeMap<String, (bool, bool)> {
    let suite: Value =
        serde_json::from_slice(&fs::read(root.join("verify_report.json")).unwrap()).unwrap();
    suite["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|engine| {
            (
                engine["engine_name"].as_str().unwrap().to_owned(),
                (
                    engine["cache_hit"] == true,
                    engine["cache_key"]
                        .as_str()
                        .is_some_and(|key| !key.is_empty()),
                ),
            )
        })
        .collect()
}

#[test]
fn legacy_verify_engine_cache_fields_follow_frozen_reuse_and_no_cache() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().join("project");
    for (name, text) in oracle["tree"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::create_dir_all(root.join(".git")).unwrap();
    for (index, run) in oracle["runs"].as_array().unwrap().iter().enumerate() {
        let argv: Vec<&str> = run["argv"]
            .as_array()
            .unwrap()
            .iter()
            .map(|part| part.as_str().unwrap())
            .collect();
        let status = Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(&argv)
            .current_dir(&root)
            .env_clear()
            .env("HOME", home.path())
            .env("XDG_CONFIG_HOME", home.path())
            .env("PATH", "/usr/bin:/bin")
            .status()
            .unwrap();
        // Accepted exit policy: the incomplete native run exits 3 where frozen exited 1.
        assert_eq!(
            (run["exit_code"].as_i64(), status.code()),
            (Some(1), Some(3))
        );
        let native = engines(&root);
        for (engine, frozen) in run["engines"].as_object().unwrap() {
            let frozen = (frozen["cache_hit"] == true, frozen["keyed"] == true);
            let actual = native[engine.as_str()];
            match index {
                // Cold run: nothing is a hit; --no-cache: nothing is keyed or a hit, as frozen.
                0 => assert!(!actual.0 && !frozen.0, "{engine}"),
                2 => assert_eq!(actual, frozen, "{engine} --no-cache"),
                _ => match REVIEWED.iter().find(|(name, ..)| name == engine) {
                    Some((_, was, now, reason)) => {
                        assert_eq!((frozen, actual), (*was, *now), "{engine}: {reason}")
                    }
                    None => assert_eq!(actual, frozen, "{engine} warm run"),
                },
            }
        }
    }
}
