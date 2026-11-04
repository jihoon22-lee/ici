//! Frozen next-path C++ sanitizer suites (`application/testing.expand_cpp_sanitizer`,
//! `workspace/instrumentation.py`, `adapters/providers/sanitize.py`) against the native next
//! path: `tests/rust-parity/cpp/capture_next_cpp_sanitizer.py` captured whole frozen
//! `ici next plan --json` and `ici next verify --json` documents over a fake `ctest` (a shell
//! script) and tiny static x86-64 ELF test binaries carrying the sanitizer runtime marker
//! strings, so the replay needs no compiler, CMake, Qt or sanitizer runtime and runs the same
//! bytes the capture ran (the ELF generator is pinned by the golden's `elf_sample`).
//!
//! Compared in full; every allowance is named in `verify_differences`/`plan_differences`. The
//! per-suite `cpp.sanitize`/`cpp.tsan` tasks fold into native's one check per sanitizer with
//! frozen-id steps (decisions.md "next `cpp.diagnostics`·`cpp.test` 실행 단위"); every blocked
//! step keeps frozen's reason in the gate ("next C++ suite step의 gate 사유").
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/next-cpp-sanitizer-oracle.json"
    ))
    .unwrap()
}

fn write(path: &Path, data: &[u8], executable: bool) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, data).unwrap();
    if executable {
        fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
    }
}

/// The capture's `elf_bytes`: a static x86-64 ELF writing `stdout`/`stderr` and exiting with
/// `code`, its marker strings after the text; an `empty` spec is a zero-byte file.
fn elf(spec: &Value, root: &str) -> Vec<u8> {
    if spec["empty"] == true {
        return vec![];
    }
    let base: u64 = 0x40_0000;
    let header: u64 = 64 + 56;
    let streams: Vec<(u32, Vec<u8>)> = [(1, "stdout"), (2, "stderr")]
        .into_iter()
        .map(|(fd, key)| {
            (
                fd,
                spec[key]
                    .as_str()
                    .unwrap()
                    .replace("{root}", root)
                    .into_bytes(),
            )
        })
        .filter(|(_, data)| !data.is_empty())
        .collect();
    let code_size = 27 * streams.len() as u64 + 12;
    let mut text = vec![];
    let mut payload = vec![];
    for (fd, data) in &streams {
        let address = base + header + code_size + payload.len() as u64;
        text.extend([0xb8, 1, 0, 0, 0, 0xbf]);
        text.extend(fd.to_le_bytes());
        text.extend([0x48, 0xbe]);
        text.extend(address.to_le_bytes());
        text.push(0xba);
        text.extend((data.len() as u32).to_le_bytes());
        text.extend([0x0f, 0x05]);
        payload.extend(data);
    }
    text.extend([0xb8, 0x3c, 0, 0, 0, 0xbf]);
    text.extend((spec["code"].as_u64().unwrap() as u32).to_le_bytes());
    text.extend([0x0f, 0x05]);
    assert_eq!(text.len() as u64, code_size);
    let markers: Vec<&str> = spec["markers"]
        .as_array()
        .unwrap()
        .iter()
        .map(|marker| marker.as_str().unwrap())
        .collect();
    payload.push(0);
    payload.extend(markers.join("\0").into_bytes());
    if !markers.is_empty() {
        payload.push(0);
    }
    let size = header + text.len() as u64 + payload.len() as u64;
    let mut file = b"\x7fELF\x02\x01\x01".to_vec();
    file.extend([0u8; 9]);
    file.extend(2u16.to_le_bytes());
    file.extend(0x3eu16.to_le_bytes());
    file.extend(1u32.to_le_bytes());
    file.extend((base + header).to_le_bytes());
    file.extend(64u64.to_le_bytes());
    file.extend(0u64.to_le_bytes());
    file.extend(0u32.to_le_bytes());
    for value in [64u16, 56, 1, 0, 0, 0] {
        file.extend(value.to_le_bytes());
    }
    file.extend(1u32.to_le_bytes());
    file.extend(5u32.to_le_bytes());
    for value in [0, base, base, size, size, 0x1000] {
        file.extend(value.to_le_bytes());
    }
    file.extend(text);
    file.extend(payload);
    file
}

/// Native `ici next <command> --json` for both commands with PATH pinned to the case's fake
/// tool directory (`<project>/tools`), HOME and the cache isolated, exactly like the capture.
fn run(case: &Value) -> BTreeMap<&'static str, (i32, Value)> {
    let work = tempfile::tempdir().unwrap();
    let project = work.path().join("project");
    let bin = project.join("tools");
    fs::create_dir_all(&bin).unwrap();
    let root = project.display().to_string();
    let text = |value: &Value| value.as_str().unwrap().replace("{root}", &root);
    for (name, value) in case["files"].as_object().unwrap() {
        write(&project.join(name), text(value).as_bytes(), false);
    }
    for (name, value) in case["executables"].as_object().unwrap() {
        write(&project.join(name), text(value).as_bytes(), true);
    }
    for (name, spec) in case["binaries"].as_object().unwrap() {
        write(&project.join(name), &elf(spec, &root), true);
    }
    for (name, value) in case["tools"].as_object().unwrap() {
        write(&bin.join(name), text(value).as_bytes(), true);
    }
    write(
        &project.join("ici.toml"),
        case["config"].as_str().unwrap().as_bytes(),
        false,
    );
    let environment = BTreeMap::from([
        ("PATH".to_string(), bin.display().to_string()),
        ("HOME".to_string(), work.path().display().to_string()),
        (
            "XDG_CACHE_HOME".to_string(),
            work.path().join("cache").display().to_string(),
        ),
    ]);
    let mut captured = BTreeMap::new();
    for command in ["plan", "verify"] {
        let invocation = registry::parse(["ici", "next", command, "--json"]).unwrap();
        let output = commands::dispatch(&invocation, &project, &environment).unwrap();
        let text = output
            .stdout
            .replace(&root, "{root}")
            .replace(&work.path().display().to_string(), "{work}");
        captured.insert(
            command,
            (
                output.exit_code,
                serde_json::from_str(&text).unwrap_or_else(|error| panic!("{error}: {text}")),
            ),
        );
    }
    captured
}

const DYNAMIC_NOTE: &str = "dynamic evidence is never reused from a previous run";

/// The native check id a frozen per-suite sanitizer task folds into, if it is one.
fn check_of(id: &str) -> Option<&'static str> {
    ["app.cpp.sanitize", "app.cpp.tsan"]
        .into_iter()
        .find(|check| {
            id.strip_prefix(check)
                .is_some_and(|rest| rest.starts_with('.'))
        })
}

fn fold(id: &Value) -> Value {
    let id = id.as_str().unwrap();
    json!(check_of(id).unwrap_or(id))
}

/// Frozen per-suite records of one list folded into one record per native check, at the first
/// suite's position (`key` names the id field).
fn fold_records(
    records: &[Value],
    key: &str,
    fold_one: impl Fn(&str, &[&Value]) -> Value,
) -> Vec<Value> {
    let mut folded = vec![];
    for record in records {
        let id = record[key].as_str().unwrap();
        match check_of(id) {
            None => folded.push(record.clone()),
            Some(check) => {
                let suites: Vec<&Value> = records
                    .iter()
                    .filter(|item| check_of(item[key].as_str().unwrap()) == Some(check))
                    .collect();
                if std::ptr::eq(record, suites[0]) {
                    folded.push(fold_one(check, &suites));
                }
            }
        }
    }
    folded
}

/// BLOCKED when a suite is blocked, else FAILED when one failed, else SUCCEEDED; complete only
/// when every suite is.
fn folded_check(check: &str, frozen: &[&Value]) -> Value {
    let states: Vec<&str> = frozen
        .iter()
        .map(|item| item["state"].as_str().unwrap())
        .collect();
    let state = ["BLOCKED", "FAILED"]
        .into_iter()
        .find(|state| states.contains(state))
        .unwrap_or("SUCCEEDED");
    let mut folded = frozen[0].clone();
    folded["task_id"] = json!(check);
    folded["state"] = json!(state);
    folded["evidence_complete"] =
        json!(frozen.iter().all(|item| item["evidence_complete"] == true));
    folded
}

/// Verify: run identity (run id, producer, identity, scope digests) is incidental; suite ids
/// fold into the check id (the gate keeps the frozen step ids); `native_rule_id` equal to the
/// rule id and the run-wide dynamic evidence note are native additions (next_cpp_suites.rs); a
/// repeated limitation is listed once ("next 실행의 run limitation 중복"). Everything else —
/// gate, metrics, findings with fingerprints and locations — is equal.
fn verify_differences(frozen: &Value, native: &Value) -> Vec<String> {
    let mut found = vec![];
    let keys = |document: &Value| -> Vec<String> {
        document.as_object().unwrap().keys().cloned().collect()
    };
    if keys(frozen) != keys(native) {
        found.push(format!("keys {:?} vs {:?}", keys(native), keys(frozen)));
    }
    for key in [
        "schema_id",
        "schema_version",
        "scope",
        "publication",
        "gate",
        "metrics",
    ] {
        if frozen[key] != native[key] {
            found.push(format!("{key}: {} vs {}", native[key], frozen[key]));
        }
    }
    let strip = |checks: Vec<Value>| -> Vec<Value> {
        checks
            .into_iter()
            .map(|mut check| {
                check.as_object_mut().unwrap().remove("scope_digest");
                check
            })
            .collect()
    };
    let expected = strip(fold_records(
        frozen["checks"].as_array().unwrap(),
        "task_id",
        folded_check,
    ));
    let actual = strip(native["checks"].as_array().unwrap().clone());
    if actual != expected {
        found.push(format!("checks {} vs {}", json!(actual), json!(expected)));
    }
    for key in ["blocked_task_ids", "failed_task_ids"] {
        let mut expected: Vec<Value> = vec![];
        for id in frozen["execution"][key].as_array().unwrap() {
            if !expected.contains(&fold(id)) {
                expected.push(fold(id));
            }
        }
        if native["execution"][key] != json!(expected) {
            found.push(format!(
                "execution.{key}: {} vs {}",
                native["execution"][key],
                json!(expected)
            ));
        }
    }
    for key in ["required_complete", "cancelled", "reused_task_ids"] {
        if native["execution"][key] != frozen["execution"][key] {
            found.push(format!(
                "execution.{key}: {} vs {}",
                native["execution"][key], frozen["execution"][key]
            ));
        }
    }
    let findings = |document: &Value| -> Vec<Value> {
        document["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                let mut finding = finding.clone();
                finding["task_id"] = fold(&finding["task_id"]);
                let object = finding.as_object_mut().unwrap();
                if object.get("native_rule_id") == object.get("rule_id") {
                    object.remove("native_rule_id");
                }
                finding
            })
            .collect()
    };
    if findings(native) != findings(frozen) {
        found.push(format!(
            "findings {} vs {}",
            json!(findings(native)),
            json!(findings(frozen))
        ));
    }
    let actual: Vec<&Value> = native["limitations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|item| *item != DYNAMIC_NOTE)
        .collect();
    let mut expected: Vec<&Value> = vec![];
    for limitation in frozen["limitations"].as_array().unwrap() {
        if !expected.contains(&limitation) {
            expected.push(limitation);
        }
    }
    if actual != expected {
        found.push(format!(
            "limitations {} vs {}",
            json!(actual),
            json!(expected)
        ));
    }
    found
}

/// The frozen plan's per-suite entries of one check folded into native's one entry
/// (next_cpp_suites.rs `folded_plan_entry`): `process` with the first runnable suite's argv
/// and every runnable suite as a step (frozen task id and argv) when one can run, else
/// `blocked` with the first suite's reason; every blocked suite keeps its frozen id and reason
/// in `blocked_steps`.
fn folded_plan_entry(check: &str, entries: &[&Value]) -> Value {
    let runnable: Vec<&&Value> = entries
        .iter()
        .filter(|entry| entry["kind"] == "process")
        .collect();
    let blocked: Vec<Value> = entries
        .iter()
        .filter(|entry| entry["kind"] == "blocked")
        .map(|entry| json!({"task_id": entry["task_id"], "reason": entry["blocked"]}))
        .collect();
    let mut entry = json!({"task_id": check, "check": entries[0]["check"]});
    if runnable.is_empty() {
        entry["kind"] = json!("blocked");
        entry["blocked"] = blocked[0]["reason"].clone();
    } else {
        entry["kind"] = json!("process");
        entry["argv"] = runnable[0]["argv"].clone();
        entry["steps"] = json!(
            runnable
                .iter()
                .map(|task| json!({"id": task["task_id"], "argv": task["argv"]}))
                .collect::<Vec<_>>()
        );
    }
    if !blocked.is_empty() {
        entry["blocked_steps"] = json!(blocked);
    }
    entry
}

/// Plan in full: suite entries fold (`folded_plan_entry`), native `work_dirs` and step fields
/// beyond id/argv are the native step model, and the static preparation preflight note is a
/// native addition (decisions.md "`next plan`의 static preparation preflight").
fn plan_differences(frozen: &Value, native: &Value) -> Vec<String> {
    let mut expected = frozen.clone();
    expected["plans"][0]["checks"] = json!(fold_records(
        frozen["plans"][0]["checks"].as_array().unwrap(),
        "task_id",
        folded_plan_entry,
    ));
    let mut actual = native.clone();
    for entry in actual["plans"][0]["checks"].as_array_mut().unwrap() {
        let object = entry.as_object_mut().unwrap();
        object.remove("work_dirs");
        if let Some(steps) = object.get_mut("steps") {
            *steps = json!(
                steps
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|step| json!({"id": step["id"], "argv": step["argv"]}))
                    .collect::<Vec<_>>()
            );
        }
    }
    actual["limitations"]
        .as_array_mut()
        .unwrap()
        .retain(|item| {
            !item
                .as_str()
                .unwrap()
                .ends_with("(static preparation preflight; nothing was built)")
        });
    if actual == expected {
        vec![]
    } else {
        vec![format!("{actual}\nvs\n{expected}")]
    }
}

/// The replay's ELF generator writes exactly the capture's bytes.
#[test]
fn next_cpp_sanitizer_elf_generator_matches_the_capture() {
    let oracle = oracle();
    let sample = &oracle["elf_sample"];
    let bytes = elf(&sample["spec"], "");
    let hex: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
    assert_eq!(hex, sample["hex"].as_str().unwrap());
}

/// Every frozen case, both documents and exit codes. The replay needs `/bin/sh` and an x86-64
/// Linux kernel to run the generated ELF test binaries.
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
#[test]
fn next_cpp_sanitizer_matches_frozen_plan_and_verify_documents_except_named_allowances() {
    let oracle = oracle();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let mut mismatches = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let native = run(case);
        for command in ["plan", "verify"] {
            let frozen = &case["frozen"][command];
            let (exit, document) = &native[command];
            let mut found = vec![];
            if json!(exit) != frozen["exit_code"] {
                found.push(format!("exit {exit} vs {}", frozen["exit_code"]));
            }
            found.extend(if command == "plan" {
                plan_differences(&frozen["document"], document)
            } else {
                verify_differences(&frozen["document"], document)
            });
            if !found.is_empty() {
                mismatches.push(format!("== {name} {command}\n{}", found.join("\n")));
            }
        }
    }
    assert!(
        mismatches.is_empty(),
        "{} mismatches\n{}",
        mismatches.len(),
        mismatches.join("\n")
    );
}
