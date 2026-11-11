//! R8 rehearsal blockers replayed against frozen v0.13.0 captures
//! (tests/rust-parity/runtime/r8-legacy-oracle.json, capture_r8_legacy.py). Every run is
//! in-process with an isolated HOME/XDG_CACHE_HOME and PATH=/usr/bin:/bin; no case needs a
//! project interpreter or an ICI_TEST_* tool.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/runtime/r8-legacy-oracle.json"
    ))
    .unwrap()
}
fn environment(base: &Path) -> BTreeMap<String, String> {
    for directory in ["home", "cache"] {
        fs::create_dir_all(base.join(directory)).unwrap();
    }
    BTreeMap::from([
        (
            "HOME".into(),
            base.join("home").to_string_lossy().into_owned(),
        ),
        (
            "XDG_CACHE_HOME".into(),
            base.join("cache").to_string_lossy().into_owned(),
        ),
        // Git discovery stops at the case's base directory (no host repository is the case's).
        (
            "GIT_CEILING_DIRECTORIES".into(),
            base.to_string_lossy().into_owned(),
        ),
        ("PATH".into(), "/usr/bin:/bin".into()),
        ("LANG".into(), "C.UTF-8".into()),
    ])
}
fn run(argv: &[&str], cwd: &Path, base: &Path) -> commands::CommandOutput {
    let invocation = registry::parse(std::iter::once("ici").chain(argv.iter().copied())).unwrap();
    // The binary's own mapping (main.rs): a refusal is the diagnostic on stderr with exit 2.
    commands::dispatch(&invocation, cwd, &environment(base)).unwrap_or_else(|error| {
        commands::CommandOutput {
            stdout: String::new(),
            stderr: commands::diagnostic(&error),
            exit_code: 2,
        }
    })
}
fn strings(value: &Value) -> Vec<&str> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item.as_str().unwrap())
        .collect()
}
fn engines(report: &Value) -> BTreeMap<String, Value> {
    report["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|engine| {
            let targets: Vec<Value> = engine["targets"]
                .as_array()
                .unwrap()
                .iter()
                .map(|target| {
                    json!({"target_name":target["target_name"],"status":target["status"],
                    "message":target["message"],"file_path":target["file_path"],
                    "start_line":target["start_line"]})
                })
                .collect();
            (
                engine["engine_name"].as_str().unwrap().to_owned(),
                json!({"status":engine["status"],"evidence":engine["evidence"],
                "required":engine["required"],"summary":engine["summary"],"targets":targets}),
            )
        })
        .collect()
}
fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), &target).unwrap();
        }
    }
}

/// Blocker 5: a configuration-less `ici verify` whose only Python file lies outside the default
/// `src` scope. Frozen reports compile_db, sanitize and (deep) thread_sanitize as not-applicable
/// SKIPs and the suite FAILs on "No tests collected" (exit 1); native once left those three as
/// required NOT_RUN engines, making the suite ERROR (exit 3).
#[test]
fn configless_verify_without_selected_sources_matches_frozen_engines_and_exit() {
    let oracle = oracle();
    for name in ["verify/root-file-standard", "verify/root-file-deep"] {
        let case = &oracle["cases"][name];
        let base = tempfile::tempdir().unwrap();
        let project = base.path().join("project");
        fs::create_dir_all(project.join(".git")).unwrap();
        for (path, text) in case["tree"].as_object().unwrap() {
            fs::write(project.join(path), text.as_str().unwrap()).unwrap();
        }
        let output = run(&strings(&case["argv"]), &project, base.path());
        // Positive: the frozen suite verdict and exit.
        assert_eq!(
            output.exit_code, case["exit_code"],
            "{name}: {}",
            output.stderr
        );
        let report: Value =
            serde_json::from_slice(&fs::read(project.join("verify_report.json")).unwrap()).unwrap();
        assert_eq!(report["suite_status"], case["suite_status"], "{name}");
        let native = engines(&report);
        let frozen: BTreeMap<String, Value> = case["engines"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect();
        assert_eq!(
            native.keys().collect::<Vec<_>>(),
            frozen.keys().collect::<Vec<_>>(),
            "{name}"
        );
        for (engine, expected) in &frozen {
            let mut actual = native[engine].clone();
            if engine == "test" {
                // Recorded intended difference (runtime.json legacy-command row, validation
                // test.py:611/774): native never invents frozen's estimated `(est)` coverage, so
                // its "No tests collected" verdict has a coverage-free summary; the evidence
                // label is frozen's ESTIMATED.
                assert_eq!(actual["evidence"], "ESTIMATED");
                assert_eq!(actual["summary"], "0/0 Tests Passed; No tests collected");
                actual["summary"] = expected["summary"].clone();
            }
            // Negative: every other engine field and target equals frozen.
            assert_eq!(&actual, expected, "{name}: {engine}");
        }
        // Failure boundary: no not-applicable engine is reported as missing evidence.
        assert!(
            report["results"]
                .as_array()
                .unwrap()
                .iter()
                .all(|engine| engine["evidence"] != "NOT_RUN"),
            "{name}"
        );
    }
}

/// Blockers 2/3: the quality-zoo `python.dead-private-function` scenario. Its legacy dead
/// findings carry frozen's empty tool fields (native once wrote the target name as
/// `tool_rule_id` and `ici.deadcode` as `tool_name`), and the suite carries frozen
/// `analysis_metadata` (policy digests equal frozen's) and a frozen-shaped capability snapshot.
#[test]
fn zoo_dead_scenario_findings_and_suite_metadata_match_frozen() {
    let oracle = oracle();
    let case = &oracle["cases"]["dead/zoo-private-function"];
    let base = tempfile::tempdir().unwrap();
    let project = base.path().join("project");
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .join(case["scenario"].as_str().unwrap()),
        &project,
    );
    let output = run(&strings(&case["argv"]), &project, base.path());
    assert_eq!(output.exit_code, case["exit_code"], "{}", output.stderr);
    let report: Value =
        serde_json::from_slice(&fs::read(project.join("verify_report.json")).unwrap()).unwrap();
    assert_eq!(report["suite_status"], case["suite_status"]);
    let dead = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "dead")
        .unwrap();
    let findings: Vec<Value> = dead["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| {
            let mut row = serde_json::Map::new();
            for key in case["findings"][0].as_object().unwrap().keys() {
                let value = match key.as_str() {
                    "path" | "start_line" | "label" => finding["primary_location"][key].clone(),
                    _ => finding[key].clone(),
                };
                row.insert(key.clone(), value);
            }
            Value::Object(row)
        })
        .collect();
    // Positive: every compared field of both findings, fingerprints included.
    assert_eq!(Value::Array(findings), case["findings"]);
    // Negative: the dead target's name stays the label, never the tool rule.
    assert!(
        dead["findings"]
            .as_array()
            .unwrap()
            .iter()
            .all(|finding| finding["tool_rule_id"] == "" && finding["tool_name"] == "")
    );
    // Positive: the zoo's engine extra predicate (frozen's "+"-joined provenance string).
    assert_eq!(dead["extra"]["analysis_provenance"], "python-ast-heuristic");
    let metadata = &report["analysis_metadata"];
    let frozen = &case["analysis_metadata"];
    assert_eq!(
        metadata.as_object().unwrap().keys().collect::<Vec<_>>(),
        strings(&case["analysis_metadata_keys"])
    );
    for key in ["fingerprint_version", "policy_digest", "tool_policy_digest"] {
        assert_eq!(metadata[key], frozen[key], "{key}");
    }
    // The producer is this build (frozen wrote 0.13.0).
    assert_eq!(metadata["producer_version"], env!("CARGO_PKG_VERSION"));
    let inventory = &report["capability_inventory"];
    assert_eq!(inventory["schema_version"], "ici.capabilities/v1");
    let tools = inventory["tools"].as_array().unwrap();
    assert_eq!(inventory["counts"]["total"], tools.len());
    // Frozen keys the snapshot by tool name: one row per capability.
    let names: std::collections::BTreeSet<&str> = tools
        .iter()
        .map(|tool| tool["name"].as_str().unwrap())
        .collect();
    assert_eq!(names.len(), tools.len(), "{tools:?}");
    // Failure boundary: every tool row names one frozen state consistent with its probe.
    for tool in tools {
        let state = tool["state"].as_str().unwrap();
        assert!(
            matches!(state, "ready" | "incomplete" | "unavailable"),
            "{tool}"
        );
        assert_eq!(state == "ready", tool["complete"] == true, "{tool}");
    }
}

/// Blocker 6: `ici analyze` from a directory without its own VCS marker. Frozen `discover`
/// passes over an enclosing project's historical `ici.toml` (no `[workspace]`) and refuses
/// (exit 2, nothing written); native once rooted the run at that enclosing project and wrote
/// its result there. Without an `ici.toml` above, both root at the checkout (or the working
/// directory when there is none).
#[test]
fn nested_directories_never_adopt_an_enclosing_historical_configuration() {
    let oracle = oracle();
    for name in [
        "nested/legacy-config-above",
        "nested/legacy-config-above-package",
        "nested/checkout-without-config",
        "nested/no-checkout",
    ] {
        let case = &oracle["cases"][name];
        let base = tempfile::tempdir().unwrap();
        // These cases test root discovery itself, so no marker can bound them: frozen captured
        // them with no VCS marker above the tree. A host marker there (e.g. a sandbox's
        // transient /tmp/.git) changes the discovered root, so name it instead of diffing.
        let enclosing: Vec<_> = base
            .path()
            .ancestors()
            .flat_map(|directory| [".git", ".hg", ".svn"].map(|marker| directory.join(marker)))
            .filter(|marker| marker.exists())
            .collect();
        assert!(
            enclosing.is_empty(),
            "{name}: host precondition broken, VCS marker above the fixture: {enclosing:?}"
        );
        let tree = base.path().join("t");
        for (path, text) in case["tree"].as_object().unwrap() {
            let target = tree.join(path);
            fs::create_dir_all(target.parent().unwrap()).unwrap();
            fs::write(target, text.as_str().unwrap()).unwrap();
        }
        for checkout in strings(&case["checkouts"]) {
            fs::create_dir_all(tree.join(checkout).join(".git")).unwrap();
        }
        let cwd = tree.join(case["cwd"].as_str().unwrap());
        let output = run(&["analyze"], &cwd, base.path());
        let created: Vec<String> = walk(&tree)
            .into_iter()
            .filter(|path| !path.split('/').any(|part| part == ".git"))
            .filter(|path| !case["tree"].as_object().unwrap().contains_key(path))
            .collect();
        if case["exit_code"] == 2 {
            // Positive (refusal): frozen's exit, problem text and an untouched tree.
            assert_eq!(output.exit_code, 2, "{name}: {}", output.stderr);
            let frozen = case["stderr_first_line"]
                .as_str()
                .unwrap()
                .replace("<tmp>", &tree.canonicalize().unwrap().to_string_lossy());
            let detail = frozen.split_once("found ici.toml above").unwrap().1;
            assert!(output.stderr.contains(detail), "{name}: {}", output.stderr);
            assert_eq!(created, Vec::<String>::new(), "{name}");
        } else {
            // Negative: the run is rooted where frozen rooted it (frozen `.ici/next`, native
            // `.ici/rust-v1`); failure boundary: never an `.ici` anywhere else.
            assert_ne!(output.exit_code, 2, "{name}: {}", output.stderr);
            let frozen_root = strings(&case["created"])[0]
                .split(".ici/")
                .next()
                .unwrap()
                .to_owned();
            assert!(!created.is_empty(), "{name}");
            for path in &created {
                assert!(
                    path.starts_with(&format!("{frozen_root}.ici/rust-v1/")),
                    "{name}: {path}"
                );
            }
        }
    }
}
fn walk(root: &Path) -> Vec<String> {
    let mut out = vec![];
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory).unwrap() {
            let entry = entry.unwrap();
            if entry.file_type().unwrap().is_dir() {
                pending.push(entry.path());
            } else {
                out.push(
                    entry
                        .path()
                        .strip_prefix(root)
                        .unwrap()
                        .to_string_lossy()
                        .into_owned(),
                );
            }
        }
    }
    out.sort();
    out
}

/// Blocker 2: the quality-zoo C++ Qt scenario configures `doctor.required_tools = ["clazy"]`
/// while `lint:cpp` requires the same tool. Frozen probes each tool once (one `clazy` row,
/// owned by both); native once resolved the configured name without frozen's candidates and
/// wrote two `clazy` rows, which the zoo runner rejects as a duplicate capability.
#[cfg(unix)]
#[test]
fn a_configured_required_tool_and_the_policy_tool_of_the_same_name_are_one_capability() {
    use std::os::unix::fs::PermissionsExt;
    let base = tempfile::tempdir().unwrap();
    let bin = base.path().join("bin");
    fs::create_dir_all(&bin).unwrap();
    for name in ["clazy-standalone", "clazy"] {
        let path = bin.join(name);
        fs::write(&path, format!("#!/bin/sh\necho '{name} 1.0'\n")).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
    }
    let mut environment = environment(base.path());
    environment.insert("PATH".into(), bin.to_string_lossy().into_owned());
    let matrix = json!({"entries": [{
        "applicable": true, "enabled": true, "engine_name": "lint", "language": "cpp",
        "required_tools": ["clazy"], "optional_tools": [],
    }]});
    let inventory = ici_cli::doctor::suite_capability_inventory(
        &matrix,
        &json!({"doctor": {"required_tools": ["clazy", "cmake"]}}),
        base.path(),
        &environment,
        &ici_execution::process::Cancellation::default(),
    )
    .unwrap();
    let tools = inventory["tools"].as_array().unwrap();
    let clazy: Vec<&Value> = tools
        .iter()
        .filter(|tool| tool["name"] == "clazy")
        .collect();
    // Positive: one row, the frozen probe candidate, owned by the policy and the configuration.
    assert_eq!(clazy.len(), 1, "{tools:?}");
    assert!(
        clazy[0]["path"]
            .as_str()
            .unwrap()
            .ends_with("/clazy-standalone"),
        "{tools:?}"
    );
    assert_eq!(
        clazy[0]["required_by"],
        json!(["doctor.config", "lint:cpp"])
    );
    assert_eq!(clazy[0]["state"], "ready");
    // Negative: a configured tool absent from PATH is still its own (unavailable) row.
    let cmake: Vec<&Value> = tools
        .iter()
        .filter(|tool| tool["name"] == "cmake")
        .collect();
    assert_eq!(cmake.len(), 1, "{tools:?}");
    assert_eq!(cmake[0]["state"], "unavailable");
    // Failure boundary: the counts describe the de-duplicated rows, one per registered tool
    // (like frozen `probe_all_tools=True`); the two policy names are among them.
    assert_eq!(inventory["counts"]["total"], tools.len());
    assert_eq!(
        tools.iter().filter(|tool| tool["required"] == true).count(),
        2,
        "{tools:?}"
    );
    assert_eq!(inventory["missing_required"], json!(["cmake"]));

    // Frozen `_probe_registered_candidates`: a `clazy-standalone` whose probe fails (it cannot
    // load its plugin) yields to the next complete candidate, `clazy`.
    let broken = bin.join("clazy-standalone");
    fs::write(
        &broken,
        "#!/bin/sh
echo 'cannot open ClazyPlugin.so' >&2
exit 1
",
    )
    .unwrap();
    fs::set_permissions(&broken, fs::Permissions::from_mode(0o755)).unwrap();
    let inventory = ici_cli::doctor::suite_capability_inventory(
        &matrix,
        &json!({"doctor": {"required_tools": ["clazy"]}}),
        base.path(),
        &environment,
        &ici_execution::process::Cancellation::default(),
    )
    .unwrap();
    let clazy = |inventory: &Value| -> Vec<Value> {
        inventory["tools"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|tool| tool["name"] == "clazy")
            .cloned()
            .collect()
    };
    let tools = clazy(&inventory);
    assert_eq!(tools.len(), 1, "{tools:?}");
    assert!(
        tools[0]["path"].as_str().unwrap().ends_with("/bin/clazy"),
        "{tools:?}"
    );
    assert_eq!(tools[0]["state"], "ready", "{tools:?}");
    // Negative: with every candidate failing, the first resolved candidate is reported, and
    // (frozen `collect_tool_capability`) a failed probe is unavailable with its exit reason, so
    // the required tool is missing rather than incomplete (core/capabilities.py).
    fs::copy(&broken, bin.join("clazy")).unwrap();
    let inventory = ici_cli::doctor::suite_capability_inventory(
        &matrix,
        &json!({}),
        base.path(),
        &environment,
        &ici_execution::process::Cancellation::default(),
    )
    .unwrap();
    let tools = clazy(&inventory);
    assert_eq!(tools.len(), 1, "{tools:?}");
    assert!(
        tools[0]["path"]
            .as_str()
            .unwrap()
            .ends_with("/clazy-standalone"),
        "{tools:?}"
    );
    assert_eq!(tools[0]["state"], "unavailable", "{tools:?}");
    assert_eq!(tools[0]["error"], "probe exited 1", "{tools:?}");
    assert_eq!(inventory["missing_required"], json!(["clazy"]));
    assert_eq!(inventory["incomplete_required"], json!([]));
}

/// Frozen `core/toolchain` probes pytest as `<interpreter> -m pytest --version`; native runs that
/// probe from an empty directory with PYTHONSAFEPATH, so a project file named like the module is
/// never imported by the capability probe (the workspace `.venv` CI creates has pytest).
#[test]
fn a_python_module_probe_never_imports_a_project_file_named_like_the_module() {
    let base = tempfile::tempdir().unwrap();
    let project = base.path().join("project");
    fs::create_dir_all(&project).unwrap();
    let marker = base.path().join("imported");
    fs::write(
        project.join("pytest.py"),
        format!(
            "open({:?}, 'w').close()\nprint('pytest 99.0')\n",
            marker.display().to_string()
        ),
    )
    .unwrap();
    let mut environment = environment(base.path());
    let python = Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .unwrap()
        .join(".venv/bin/python");
    environment.insert("ICI_PYTHON".into(), python.display().to_string());
    let matrix = json!({"entries": [{
        "applicable": true, "enabled": true, "engine_name": "test", "language": "python",
        "required_tools": ["pytest"], "optional_tools": [],
    }]});
    let inventory = ici_cli::doctor::suite_capability_inventory(
        &matrix,
        &json!({}),
        &project,
        &environment,
        &ici_execution::process::Cancellation::default(),
    )
    .unwrap();
    let pytest = inventory["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "pytest")
        .unwrap();
    assert_eq!(pytest["name"], "pytest", "{inventory:#}");
    assert!(
        !marker.exists(),
        "the probe imported the project's pytest.py"
    );
    assert_eq!(pytest["state"], "ready", "{inventory:#}");
    assert_ne!(pytest["version"], "pytest 99.0", "{inventory:#}");
    assert_eq!(pytest["details"]["provider"], "python-module");
}

/// decisions.md "legacy capability inventory의 Python 모듈 도구" without ICI_PYTHON: the unused
/// mypy/pytest/coverage rows of the legacy suite inventory are probed through the launcher-rule
/// interpreter (frozen used its own). The only interpreter on PATH is a logging `python3` in front
/// of /usr/bin/python3, so the selection and its `-m <module> --version` calls are recorded; the
/// rows are neither required nor optional and report what that interpreter can import.
#[test]
fn an_unused_module_row_probes_the_launcher_rule_interpreter() {
    use std::os::unix::fs::PermissionsExt;
    let base = tempfile::tempdir().unwrap();
    let bin = base.path().join("bin");
    fs::create_dir_all(&bin).unwrap();
    let log = base.path().join("python.log");
    let python = bin.join("python3");
    fs::write(
        &python,
        format!(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{}'\nexec /usr/bin/python3 \"$@\"\n",
            log.display()
        ),
    )
    .unwrap();
    fs::set_permissions(&python, fs::Permissions::from_mode(0o755)).unwrap();
    let mut environment = environment(base.path());
    environment.insert("PATH".into(), bin.display().to_string());
    environment.remove("ICI_PYTHON");
    let project = base.path().join("project");
    fs::create_dir_all(&project).unwrap();
    let inventory = ici_cli::doctor::suite_capability_inventory(
        &json!({"entries": []}),
        &json!({}),
        &project,
        &environment,
        &ici_execution::process::Cancellation::default(),
    )
    .unwrap();
    let calls = fs::read_to_string(&log).unwrap();
    let tools = inventory["tools"].as_array().unwrap();
    for module in ["mypy", "pytest", "coverage"] {
        let row = tools
            .iter()
            .find(|tool| tool["name"] == module)
            .unwrap_or_else(|| panic!("{module}: {tools:?}"));
        assert_eq!(row["required"], false, "{row}");
        assert_eq!(row["optional"], false, "{row}");
        assert_eq!(row["details"]["provider"], "python-module", "{row}");
        assert_eq!(row["path"], json!(python.display().to_string()), "{row}");
        assert_eq!(
            calls
                .lines()
                .filter(|line| *line == format!("-m {module} --version"))
                .count(),
            1,
            "{calls}"
        );
        let imports = std::process::Command::new("/usr/bin/python3")
            .args(["-I", "-m", module, "--version"])
            .current_dir(base.path())
            .output()
            .unwrap()
            .status
            .success();
        assert_eq!(
            row["available"] == true && row["complete"] == true,
            imports,
            "{row}"
        );
    }
    assert!(inventory["missing_required"].as_array().unwrap().is_empty());
    assert_eq!(inventory["healthy"], true);
}

/// decisions.md "legacy `verify`의 capability inventory probe": the processes of the legacy
/// suite inventory, recorded by logging stand-ins for a few registered tools (nothing else on
/// PATH). Frozen `collect_capability_inventory` order: tool by tool in registry order, each
/// candidate's version probe until one is available and complete (clazy-standalone fails, so
/// clazy follows; qmake6 fails, so qmake follows), then that candidate's metadata probe. The
/// selected candidate is not probed again, and writing the report probes nothing: `verify` with
/// and without `--report` runs the same processes once. This pins these tools only, not the
/// order of all registered tools on another host.
#[test]
fn the_legacy_verify_inventory_probes_each_tool_once_in_registry_order() {
    use std::os::unix::fs::PermissionsExt;
    let expected = [
        "gcc --version",
        "gcc -dumpmachine",
        "clazy-standalone --version",
        "clazy --version",
        "cmake --version",
        "cmake -E capabilities",
        "qmake6 -v",
        "qmake -v",
        "qmake -query",
        "pkg-config --version",
        "pkg-config --modversion Qt5Core",
        "pkg-config --modversion Qt6Core",
    ];
    for report in [false, true] {
        let base = tempfile::tempdir().unwrap();
        let bin = base.path().join("bin");
        fs::create_dir_all(&bin).unwrap();
        let log = base.path().join("probes.log");
        for (name, fails) in [
            ("gcc", false),
            ("clazy-standalone", true),
            ("clazy", false),
            ("cmake", false),
            ("qmake6", true),
            ("qmake", false),
            ("pkg-config", false),
        ] {
            let path = bin.join(name);
            let answer = if fails {
                "exit 1".to_owned()
            } else {
                format!(
                    "case \"$1\" in -dumpmachine) echo x86_64-linux-gnu;; *) echo '{name} version 1.2.3';; esac"
                )
            };
            fs::write(
                &path,
                format!(
                    "#!/bin/sh\nprintf '%s %s\\n' {name} \"$*\" >> '{}'\n{answer}\n",
                    log.display()
                ),
            )
            .unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        }
        let project = base.path().join("project");
        fs::create_dir_all(project.join(".git")).unwrap();
        fs::create_dir_all(project.join("src")).unwrap();
        fs::write(project.join("src/a.cpp"), "int a() { return 1; }\n").unwrap();
        let disabled: String = [
            "build",
            "binary_compat",
            "integration",
            "lint",
            "compile_db",
            "test",
            "type",
            "python_compat",
            "complexity",
            "sanitize",
            "thread_sanitize",
            "dup",
            "exception",
            "cycle",
            "cognitive",
            "security",
            "resource",
            "dead",
        ]
        .iter()
        .map(|name| format!("[engines.{name}]\nenabled = false\n"))
        .collect();
        fs::write(
            project.join("ici.toml"),
            format!("[project]\ntype = \"cpp\"\nsource_dirs = [\"src\"]\n{disabled}"),
        )
        .unwrap();
        let mut environment = environment(base.path());
        environment.insert("PATH".into(), bin.display().to_string());
        let argv: &[&str] = if report {
            &["ici", "verify", "--report"]
        } else {
            &["ici", "verify"]
        };
        let invocation = registry::parse(argv.iter().copied()).unwrap();
        let output = commands::dispatch(&invocation, &project, &environment).unwrap();
        assert_eq!(output.exit_code, 0, "{}{}", output.stdout, output.stderr);
        let lines = fs::read_to_string(&log).unwrap();
        assert_eq!(
            lines.lines().collect::<Vec<_>>(),
            expected,
            "report={report}"
        );
        if report {
            let saved: Value = serde_json::from_str(
                &fs::read_to_string(project.join("verify_report.json")).unwrap(),
            )
            .unwrap();
            let names: Vec<&str> = saved["capability_inventory"]["tools"]
                .as_array()
                .unwrap()
                .iter()
                .map(|tool| tool["name"].as_str().unwrap())
                .collect();
            assert_eq!(
                names.len(),
                saved["capability_inventory"]["counts"]["total"]
            );
            assert_eq!(names.first(), Some(&"gcc"));
            let clazy = saved["capability_inventory"]["tools"]
                .as_array()
                .unwrap()
                .iter()
                .find(|tool| tool["name"] == "clazy")
                .unwrap();
            assert!(
                clazy["path"].as_str().unwrap().ends_with("/bin/clazy"),
                "{clazy}"
            );
            assert_eq!(clazy["required"], false);
        }
    }
}
