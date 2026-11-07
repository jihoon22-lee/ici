//! A next clang-tidy check is never complete evidence: its providers prove no header/module
//! closure and, when the recorded database covers only part of the scope, not the scope either.
//! A run whose tidy evidence is incomplete therefore cannot resolve a baseline tidy finding,
//! even when the tool reports nothing the second time. Nothing here is an unread input.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path};

fn config() -> String {
    "schema_version = 1\n[workspace]\nname = \"probe\"\nprofile = \"standard\"\n\
     [[components]]\nid = \"app\"\nroot = \"app\"\nlanguages = [\"cpp\"]\n"
        .to_owned()
        + &ici_domain::builtin_catalog()
            .checks
            .iter()
            .map(|check| {
                format!(
                    "[checks.\"{}\"]\nenabled = {}\n",
                    check.id,
                    matches!(check.id.as_str(), "cpp.compile" | "cpp.tidy")
                )
            })
            .collect::<String>()
}

fn verify(project: &Path, base: &Path, tools: &Path, extra: &[&str]) -> (i32, Value) {
    let environment = BTreeMap::from([
        ("HOME".to_owned(), base.join("home").display().to_string()),
        (
            "PATH".to_owned(),
            format!("{}:/usr/bin:/bin", tools.display()),
        ),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            base.display().to_string(),
        ),
    ]);
    let invocation = registry::parse(
        ["ici", "next", "verify", "--json"]
            .iter()
            .chain(extra)
            .copied(),
    )
    .unwrap();
    let output = commands::dispatch(&invocation, project, &environment).unwrap();
    (
        output.exit_code,
        serde_json::from_str(&output.stdout).unwrap(),
    )
}

/// A fake clang-tidy: a version banner, then one warning on `a.cpp` while `warn` exists.
fn fake_tidy(tools: &Path, warn: &Path) {
    let script = tools.join("clang-tidy");
    fs::write(
        &script,
        format!(
            "#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'LLVM version 18.1.3'; exit 0; fi\n\
             if [ -e '{}' ]; then for arg in \"$@\"; do case \"$arg\" in *a.cpp) \
             echo \"$arg:1:5: warning: use auto [modernize-use-auto]\";; esac; done; fi\nexit 0\n",
            warn.display()
        ),
    )
    .unwrap();
    fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();
}

fn tidy(document: &Value) -> &Value {
    document["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["check_id"] == "cpp.tidy")
        .unwrap_or_else(|| panic!("{document}"))
}

#[test]
fn incomplete_tidy_evidence_never_resolves_a_baseline_finding() {
    for covered in [&["a.cpp", "b.cpp"][..], &["a.cpp"][..]] {
        let work = tempfile::tempdir().unwrap();
        let base = work.path().canonicalize().unwrap();
        let project = base.join("project");
        let tools = base.join("tools");
        fs::create_dir_all(project.join("app")).unwrap();
        fs::create_dir_all(base.join("home")).unwrap();
        fs::create_dir_all(&tools).unwrap();
        fs::write(project.join(".hg"), "").unwrap();
        fs::write(project.join("ici.toml"), config()).unwrap();
        fs::write(project.join("app/a.cpp"), "int a() { return 1; }\n").unwrap();
        fs::write(project.join("app/b.cpp"), "int b() { return 2; }\n").unwrap();
        let rows: Vec<Value> = covered
            .iter()
            .map(|source| {
                serde_json::json!({"directory": project, "file": format!("app/{source}"),
                    "arguments": ["/usr/bin/g++", "-c", format!("app/{source}")]})
            })
            .collect();
        fs::write(
            project.join("compile_commands.json"),
            serde_json::to_string(&rows).unwrap(),
        )
        .unwrap();
        let warn = base.join("warn");
        fs::write(&warn, "").unwrap();
        fake_tidy(&tools, &warn);
        let (_, first) = verify(&project, &base, &tools, &["--result", "first.json"]);
        let check = tidy(&first);
        assert_eq!(check["evidence_complete"], false, "{covered:?}: {check}");
        let fingerprints: Vec<Value> = first["findings"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|finding| finding["task_id"] == check["task_id"])
            .map(|finding| finding["fingerprint"].clone())
            .collect();
        // The tool may refuse the partial scope; when it ran, its finding must not resolve.
        fs::remove_file(&warn).unwrap();
        let (_, now) = verify(
            &project,
            &base,
            &tools,
            &["--result", "now.json", "--baseline", "first.json"],
        );
        let check = tidy(&now);
        assert_eq!(check["evidence_complete"], false, "{covered:?}: {check}");
        let resolved = now["baseline"]["resolved"].as_array().cloned();
        for fingerprint in &fingerprints {
            assert!(
                !resolved.iter().flatten().any(|item| item == fingerprint),
                "{covered:?}: {}",
                now["baseline"]
            );
        }
        if covered.len() == 2 {
            assert_eq!(fingerprints.len(), 1, "{first}");
        }
    }
}
