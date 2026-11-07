//! A URL credential in a recorded compile command, through the real CLI's saved results. Frozen
//! has no URL pattern and writes these to `verify_report.json`; native masks them on every output
//! it produces (decisions.md "argv URL 자격증명 노출 교정") and the compiler still gets the
//! original bytes. All values here are synthetic.
use serde_json::{Value, json};
use std::{fs, path::Path, process::Command};

const SECRETS: [&str; 7] = [
    "syntheticuserhttp",
    "syntheticpwhttp",
    "synthetictokenhttp",
    "syntheticuserpg",
    "syntheticpwpg",
    "syntheticpwssh",
    "synthetictokengit",
];
const ARGUMENTS: [&str; 8] = [
    "-DHTTP=https://syntheticuserhttp:syntheticpwhttp@host.invalid/x",
    "-DTOKEN_ONLY=https://synthetictokenhttp@host.invalid/x",
    "-DPG=postgres://syntheticuserpg:syntheticpwpg@db.invalid/x",
    "-DSSH=ssh://login:syntheticpwssh@host.invalid/x",
    "-DGIT=git+https://synthetictokengit@host.invalid/x",
    // Nothing secret in these three; they must stay readable.
    "-DLOGIN=ssh://git@host.invalid/x",
    "-DPLAIN=http://host.invalid:8080/a@b",
    "-DLITERAL=\"https://syntheticpwhttp@host.invalid/x\"",
];
const KEPT: [&str; 2] = ["ssh://git@host.invalid/x", "http://host.invalid:8080/a@b"];
const MASKED: [&str; 3] = [
    "https://***REDACTED***@host.invalid/x",
    "postgres://***REDACTED***@db.invalid/x",
    "ssh://***REDACTED***@host.invalid/x",
];
// Fails to compile unless the compiler got the literal's original length.
const SOURCE: &str = "static_assert(sizeof(LITERAL) == 39, \"literal\");\nint a() { return 0; }\n";

fn run(root: &Path, home: &Path, args: &[&str]) -> String {
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(args)
        .current_dir(root)
        .env_clear()
        .env("HOME", home)
        .env("PATH", "/usr/bin:/bin")
        .env("GIT_CEILING_DIRECTORIES", home)
        .output()
        .unwrap();
    format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    )
}

fn written(root: &Path, into: &mut Vec<(String, String)>) {
    for entry in fs::read_dir(root).unwrap() {
        let path = entry.unwrap().path();
        if path.is_dir() {
            written(&path, into);
        } else if !path.ends_with("compile_commands.json") {
            let text = String::from_utf8_lossy(&fs::read(&path).unwrap()).into_owned();
            into.push((path.display().to_string(), text));
        }
    }
}

/// Run the three commands that carry argv and return each output, the report and every file.
fn outputs(source: &str) -> (Vec<(String, String)>, Value) {
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap();
    fs::write(base.join(".hg"), "").unwrap();
    let root = base.join("project");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(
        root.join("ici.toml"),
        "type = \"cpp\"\n[project]\nname = \"p\"\n",
    )
    .unwrap();
    fs::write(root.join("src/a.cpp"), source).unwrap();
    let mut arguments = vec!["/usr/bin/g++", "-std=c++17"];
    arguments.extend(ARGUMENTS);
    arguments.extend(["-c", "src/a.cpp"]);
    let database = json!([{"directory": root, "arguments": arguments, "file": "src/a.cpp"}]);
    fs::write(root.join("compile_commands.json"), database.to_string()).unwrap();
    let mut seen = Vec::new();
    for args in [
        &["verify", "--report", "--no-cache"][..],
        &["next", "plan"],
        &["next", "verify"],
        &["next", "report"],
    ] {
        let text = run(&root, &base, args);
        seen.push((args.join(" "), text));
    }
    written(&root, &mut seen);
    let report = fs::read_to_string(root.join("verify_report.json")).unwrap();
    (seen, serde_json::from_str(&report).unwrap())
}

fn no_secret_anywhere(seen: &[(String, String)]) {
    assert!(
        seen.len() >= 6,
        "{:?}",
        seen.iter().map(|s| &s.0).collect::<Vec<_>>()
    );
    for (name, text) in seen {
        for secret in SECRETS {
            assert!(!text.contains(secret), "{secret} in {name}");
        }
    }
}

fn lint_messages(report: &Value) -> String {
    let lint = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "lint")
        .unwrap();
    lint["errors"].to_string() + &lint["warnings"].to_string() + &lint["targets"].to_string()
}

#[test]
fn a_url_credential_reaches_the_compiler_and_no_output() {
    let (seen, report) = outputs(SOURCE);
    no_secret_anywhere(&seen);
    let saved = &seen
        .iter()
        .find(|s| s.0.ends_with("verify_report.json"))
        .unwrap()
        .1;
    for text in KEPT.iter().chain(&MASKED) {
        assert!(saved.contains(text), "{text} missing");
    }
    // The compiler got the original bytes: masking would have failed the static_assert.
    assert!(!lint_messages(&report).contains("static assertion"));
}

#[test]
fn a_failing_compile_does_not_leak_the_credential_in_its_diagnostic() {
    let (seen, report) = outputs("int a() { return nosuch; }\n");
    no_secret_anywhere(&seen);
    assert!(lint_messages(&report).contains("nosuch"));
    // The check above does see a wrong length, so its silence means the bytes arrived intact.
    let (seen, report) = outputs("static_assert(sizeof(LITERAL) == 1, \"literal\");\n");
    no_secret_anywhere(&seen);
    assert!(lint_messages(&report).contains("static assertion"));
}
