//! Frozen `core/make.py` contract and actual GNU Make preparation semantics.
use ici_cpp::{BuildLease, BuildRequest, make_settings, plan_build};
use ici_execution::{Cancellation, Runner, Scheduler};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::symlink, path::Path, time::Duration};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/make-plan-oracle.json"
    ))
    .unwrap()
}

#[test]
fn make_settings_match_every_frozen_make_plan_case() {
    let document = oracle();
    assert_eq!(
        document["oracle"],
        "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
    );
    let cases = document["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 46);
    for case in cases {
        let name = case["name"].as_str().unwrap();
        // Configured jobs are substituted unchanged, so argv must equal the frozen plan exactly.
        let actual = make_settings(
            Some(&case["make"]),
            case["variant"].as_str().unwrap(),
            "Makefile",
        );
        match (&case["error"], actual) {
            (Value::String(expected), Err(error)) => {
                assert_eq!(&error.message, expected, "{name}");
                assert_eq!(error.code, "make-config", "{name}");
            }
            (Value::Null, Ok(settings)) => {
                for field in [
                    "workdir",
                    "shadow",
                    "configure_argv",
                    "clean_argv",
                    "build_argv",
                    "test_argv",
                    "jobs",
                    "out_of_tree",
                ] {
                    assert_eq!(
                        serde_json::to_value(&settings).unwrap()[field],
                        case[field],
                        "{name}.{field}"
                    );
                }
            }
            (expected, actual) => panic!("{name}: expected {expected:?}, got {actual:?}"),
        }
    }
}

#[test]
fn configured_jobs_are_substituted_exactly_like_the_frozen_contract() {
    let root = tempfile::tempdir().unwrap();
    let table = json!({"enabled":true,"jobs":64,"shadow_dir":"out","build_argv":["make","-j","{jobs}","all"],"configure_argv":["make","--jobs","{jobs}","configure"]});
    let settings = make_settings(Some(&table), "release", "Makefile").unwrap();
    assert_eq!(settings.jobs, 64);
    assert_eq!(settings.build_argv, ["make", "-j", "64", "all"]);
    assert_eq!(
        settings.configure_argv,
        ["make", "--jobs", "64", "configure"]
    );
    let request = BuildRequest {
        system: "make".into(),
        jobs: 64,
        make: Some(settings),
        ..Default::default()
    };
    let plan = plan_build(root.path(), &request).unwrap();
    assert!(plan.limitations.is_empty(), "{:?}", plan.limitations);
    assert_eq!(plan.tasks[1].argv, ["make", "-j", "64", "all"]);
    let one = make_settings(
        Some(&json!({"enabled":true,"build_argv":["make","-j","{jobs}"]})),
        "release",
        "Makefile",
    )
    .unwrap();
    assert_eq!((one.jobs, one.shadow.as_str()), (1, "build/ici-make"));
    let request = BuildRequest {
        system: "make".into(),
        jobs: 1,
        make: Some(one),
        ..Default::default()
    };
    let plan = plan_build(root.path(), &request).unwrap();
    assert_eq!(plan.tasks.len(), 1);
    assert_eq!(plan.tasks[0].argv, ["make", "-j", "1"]);
    assert_eq!(plan.shadow, "build/ici-make");
    assert_eq!(plan.generated_roots, ["build/ici-make"]);
    assert!(
        !root.path().join("build").exists(),
        "planning must not mutate"
    );
}

#[test]
fn plan_rejects_missing_contract_forged_shadow_and_missing_or_symlinked_workdir() {
    let root = tempfile::tempdir().unwrap();
    // Frozen make_enabled is an exact boolean `true`.
    for table in [
        json!({"enabled":1,"build_argv":["make"]}),
        json!({"enabled":false,"build_argv":["make"]}),
        json!({"build_argv":["make"]}),
        json!(["make"]),
    ] {
        assert_eq!(
            make_settings(Some(&table), "release", "Makefile")
                .unwrap_err()
                .message,
            "build.make.enabled must be true for the Make backend"
        );
    }
    assert!(make_settings(None, "release", "Makefile").is_err());
    assert!(
        make_settings(
            Some(&json!({"enabled":true,"build_argv":["make"]})),
            "link-reachability",
            "Makefile",
        )
        .is_err()
    );
    let base = make_settings(
        Some(&json!({"enabled":true,"build_argv":["make"],"shadow_dir":"out"})),
        "release",
        "Makefile",
    )
    .unwrap();
    let error = plan_build(
        root.path(),
        &BuildRequest {
            system: "make".into(),
            build_argv: vec!["make".into()],
            ..Default::default()
        },
    )
    .unwrap_err();
    assert_eq!(error.code, "missing-make-contract");
    for shadow in [".", "../out", ".ici/out", "a/../b", "/abs"] {
        let mut forged = base.clone();
        forged.shadow = shadow.into();
        let request = BuildRequest {
            system: "make".into(),
            make: Some(forged),
            ..Default::default()
        };
        assert!(plan_build(root.path(), &request).is_err(), "{shadow}");
    }
    let mut forged = base.clone();
    forged.build_argv = vec!["bash".into(), "-c".into(), "make".into()];
    let request = BuildRequest {
        system: "make".into(),
        make: Some(forged),
        ..Default::default()
    };
    assert!(
        plan_build(root.path(), &request)
            .unwrap_err()
            .message
            .contains("command shell")
    );
    let mut missing = base.clone();
    missing.workdir = "native".into();
    let request = BuildRequest {
        system: "make".into(),
        make: Some(missing.clone()),
        ..Default::default()
    };
    assert!(
        plan_build(root.path(), &request)
            .unwrap_err()
            .message
            .contains("existing project directory")
    );
    let outside = tempfile::tempdir().unwrap();
    symlink(outside.path(), root.path().join("native")).unwrap();
    assert!(plan_build(root.path(), &request).is_err());
    let mut required = base;
    required.out_of_tree = "required".into();
    let request = BuildRequest {
        system: "make".into(),
        make: Some(required),
        ..Default::default()
    };
    assert!(plan_build(root.path(), &request).is_err());
}

fn make_tool() -> String {
    std::env::var("ICI_TEST_MAKE").expect("explicit GNU Make")
}
fn compiler() -> String {
    std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler")
}
fn project(root: &Path) {
    fs::create_dir_all(root.join("native")).unwrap();
    fs::write(root.join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(
        root.join("Makefile"),
        format!(
            "CXX := {cxx}\nall:\n\tpwd > out/cwd.txt\n\techo '$(MAKEFLAGS)' > out/flags.txt\n\t$(CXX) main.cpp -o out/app\nconfigure:\n\techo configure >> out/log.txt\nclean:\n\techo clean >> out/log.txt\n\trm -f out/app\nwipe:\n\trm -rf out\n\tmkdir out\nbad-clean:\n\texit 3\nbad-build:\n\texit 4\ncoverage:\n\t$(CXX) --coverage main.cpp -o out/app-coverage\n",
            cxx = compiler()
        ),
    )
    .unwrap();
}
fn settings(table: Value, variant: &str) -> ici_cpp::MakeSettings {
    let mut settings = make_settings(Some(&table), variant, "Makefile").unwrap();
    for argv in [
        &mut settings.configure_argv,
        &mut settings.clean_argv,
        &mut settings.build_argv,
    ] {
        if !argv.is_empty() {
            argv[0] = make_tool();
        }
    }
    settings
}
fn execute(
    root: &Path,
    settings: ici_cpp::MakeSettings,
    variant: &str,
) -> Result<Vec<ici_execution::process::TaskOutcome>, ici_cpp::CppError> {
    let request = BuildRequest {
        system: "make".into(),
        variant: variant.into(),
        project: settings.workdir.clone(),
        jobs: settings.jobs as usize,
        make: Some(settings),
        ..Default::default()
    };
    let plan = plan_build(root, &request)?;
    let cancellation = Cancellation::default();
    let lease = BuildLease::acquire(root, &plan, &cancellation, Duration::from_secs(5))?;
    lease.prepare(&plan)?;
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    let mut scheduler = Scheduler::new(Runner::with_environment(environment), 2);
    scheduler.lock_directory = Some(root.join(".ici/rust-v1/locks/tasks"));
    Ok(scheduler.run(&plan.tasks, &cancellation).unwrap().outcomes)
}

#[test]
#[ignore = "explicit actual GNU Make preparation acceptance (ICI_TEST_MAKE, ICI_TEST_CXX)"]
fn actual_make_runs_in_workdir_with_ordered_clean_configured_jobs_and_shared_owned_shadow() {
    let root = tempfile::tempdir().unwrap();
    let root_path = root.path().canonicalize().unwrap();
    project(&root_path);
    let table = json!({"enabled":true,"jobs":2,"shadow_dir":"out","configure_argv":["make","configure"],"clean_argv":["make","clean"],"build_argv":["make","-j","{jobs}","all"],"coverage_build_argv":["make","coverage"]});
    let outcomes = execute(&root_path, settings(table.clone(), "release"), "release").unwrap();
    assert_eq!(
        outcomes
            .iter()
            .map(|outcome| outcome.task_id.rsplit(':').next().unwrap().to_owned())
            .collect::<Vec<_>>(),
        ["configure", "clean", "compile"]
    );
    assert!(
        outcomes
            .iter()
            .all(|outcome| outcome.prerequisite_succeeded())
    );
    let out = root_path.join("out");
    // Frozen cwd is the workdir (project root), not the shadow.
    assert_eq!(
        fs::read_to_string(out.join("cwd.txt")).unwrap().trim(),
        root_path.display().to_string()
    );
    assert_eq!(
        fs::read_to_string(out.join("log.txt")).unwrap(),
        "configure\nclean\n"
    );
    let flags = fs::read_to_string(out.join("flags.txt")).unwrap();
    assert!(flags.contains("j2"), "{flags}");
    assert!(out.join("app").is_file() && out.join(".ici-owner.json").is_file());
    // Rerun adopts the owned shadow; the coverage variant shares it without ownership mismatch.
    assert!(
        execute(&root_path, settings(table.clone(), "release"), "release")
            .unwrap()
            .iter()
            .all(|outcome| outcome.prerequisite_succeeded())
    );
    assert_eq!(
        fs::read_to_string(out.join("log.txt")).unwrap(),
        "configure\nclean\nconfigure\nclean\n"
    );
    let coverage = execute(&root_path, settings(table.clone(), "coverage"), "coverage").unwrap();
    assert!(
        coverage
            .iter()
            .all(|outcome| outcome.prerequisite_succeeded())
    );
    assert!(out.join("app-coverage").is_file());

    // A clean recipe that recreates the whole shadow removes the in-shadow marker; the
    // Rust-namespace owner record still identifies this adopted path.
    let wipe = json!({"enabled":true,"shadow_dir":"out","clean_argv":["make","wipe"],"build_argv":["make","all"]});
    assert!(
        execute(&root_path, settings(wipe.clone(), "release"), "release")
            .unwrap()
            .iter()
            .all(|outcome| outcome.prerequisite_succeeded())
    );
    assert!(!out.join(".ici-owner.json").exists() && out.join("app").is_file());
    // The owner record lives outside the build-id namespace.
    let records = fs::read_dir(root_path.join(".ici/rust-v1/owners/make"))
        .unwrap()
        .map(|entry| entry.unwrap().file_name().into_string().unwrap())
        .collect::<Vec<_>>();
    assert!(
        records.len() == 1 && records[0].ends_with(".json") && records[0].len() == 37,
        "{records:?}"
    );
    assert!(!root_path.join(".ici/rust-v1/build/make-owners").exists());
    assert!(
        execute(&root_path, settings(wipe, "release"), "release")
            .unwrap()
            .iter()
            .all(|outcome| outcome.prerequisite_succeeded())
    );

    // A failing clean stops the chain before build.
    fs::remove_file(out.join("app")).unwrap();
    let failed = execute(
        &root_path,
        settings(
            json!({"enabled":true,"shadow_dir":"out","clean_argv":["make","bad-clean"],"build_argv":["make","all"]}),
            "release",
        ),
        "release",
    )
    .unwrap();
    assert_eq!(failed[0].exit_code, Some(2));
    assert!(!failed[1].prerequisite_succeeded() && failed[1].exit_code.is_none());
    assert!(!out.join("app").exists());
    let failed = execute(
        &root_path,
        settings(
            json!({"enabled":true,"shadow_dir":"out","build_argv":["make","bad-build"]}),
            "release",
        ),
        "release",
    )
    .unwrap();
    assert_eq!(failed.len(), 1);
    assert!(!failed[0].prerequisite_succeeded());

    // Custom workdir: commands run there, ici does not create or write it.
    fs::write(
        root_path.join("native/Makefile"),
        "all:\n\tpwd > ../out/native-cwd.txt\n",
    )
    .unwrap();
    let native = json!({"enabled":true,"workdir":"native","out_of_tree":"required","shadow_dir":"out","build_argv":["make","all"]});
    assert!(
        execute(&root_path, settings(native, "release"), "release")
            .unwrap()
            .iter()
            .all(|outcome| outcome.prerequisite_succeeded())
    );
    assert_eq!(
        fs::read_to_string(out.join("native-cwd.txt"))
            .unwrap()
            .trim(),
        root_path.join("native").display().to_string()
    );
    assert_eq!(fs::read_dir(root_path.join("native")).unwrap().count(), 1);
}

#[test]
#[ignore = "explicit actual GNU Make shadow ownership refusal (ICI_TEST_MAKE, ICI_TEST_CXX)"]
fn actual_make_refuses_unowned_symlinked_and_foreign_shadows_and_preserves_their_bytes() {
    let root = tempfile::tempdir().unwrap();
    let root_path = root.path().canonicalize().unwrap();
    project(&root_path);
    let table = json!({"enabled":true,"shadow_dir":"out","build_argv":["make","all"]});
    // Nonempty unowned shadow, as left by a pyz-era build: refused, bytes untouched.
    fs::create_dir(root_path.join("out")).unwrap();
    fs::write(root_path.join("out/user.txt"), "keep").unwrap();
    let error = execute(&root_path, settings(table.clone(), "release"), "release").unwrap_err();
    assert_eq!(error.code, "unowned-build-directory");
    assert!(error.message.contains("build.make.shadow_dir \"out\""));
    assert_eq!(
        fs::read_to_string(root_path.join("out/user.txt")).unwrap(),
        "keep"
    );
    assert_eq!(fs::read_dir(root_path.join("out")).unwrap().count(), 1);
    assert!(!root_path.join("out/cwd.txt").exists());
    // Symlinked shadow pointing outside: refused without touching the target.
    fs::remove_dir_all(root_path.join("out")).unwrap();
    let outside = tempfile::tempdir().unwrap();
    symlink(outside.path(), root_path.join("out")).unwrap();
    assert!(execute(&root_path, settings(table.clone(), "release"), "release").is_err());
    assert_eq!(fs::read_dir(outside.path()).unwrap().count(), 0);
    fs::remove_file(root_path.join("out")).unwrap();
    // Owned by this root; a copied tree in another root carries a foreign marker.
    assert!(
        execute(&root_path, settings(table.clone(), "release"), "release")
            .unwrap()
            .iter()
            .all(|outcome| outcome.prerequisite_succeeded())
    );
    let other = tempfile::tempdir().unwrap();
    let other_path = other.path().canonicalize().unwrap();
    project(&other_path);
    fs::create_dir(other_path.join("out")).unwrap();
    fs::copy(
        root_path.join("out/.ici-owner.json"),
        other_path.join("out/.ici-owner.json"),
    )
    .unwrap();
    fs::write(other_path.join("out/app"), "foreign").unwrap();
    let error = execute(&other_path, settings(table, "release"), "release").unwrap_err();
    assert_eq!(error.code, "ownership-mismatch");
    assert_eq!(
        fs::read_to_string(other_path.join("out/app")).unwrap(),
        "foreign"
    );
}
