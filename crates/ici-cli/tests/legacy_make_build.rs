//! Actual legacy `ici build` through a configured handwritten Make backend.
use ici_cli::{commands, planning, registry};
use std::{collections::BTreeMap, fs, path::Path};

fn fixture(make: &str) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler");
    let tool = std::env::var("ICI_TEST_MAKE").expect("explicit GNU Make");
    fs::create_dir(root.path().join("src")).unwrap();
    fs::write(root.path().join("src/main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(
        root.path().join("Makefile"),
        format!(
            "all:\n\tmkdir -p build/ici-make\n\tpwd > build/ici-make/cwd.txt\n\techo '$(MAKEFLAGS)' > build/ici-make/flags.txt\n\t{compiler} src/main.cpp -o build/ici-make/app\nclean:\n\techo clean >> build/ici-make/log.txt\nbroken:\n\texit 5\n"
        ),
    )
    .unwrap();
    fs::write(
        root.path().join("pyproject.toml"),
        "[project]\nname='legacy-make-proof'\nversion='1.2.3'\n",
    )
    .unwrap();
    fs::write(
        root.path().join("ici.toml"),
        format!(
            "[project]\ntype='cpp'\nsource_dirs=['src']\n[build.make]\nenabled=true\n{}\n",
            make.replace(
                "['make'",
                &format!("[{}", serde_json::to_string(&tool).unwrap())
            )
        ),
    )
    .unwrap();
    root
}
fn environment() -> BTreeMap<String, String> {
    BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())])
}
fn dispatch(root: &Path) -> commands::CommandOutput {
    let invocation = registry::parse(["ici", "build"]).unwrap();
    commands::dispatch(&invocation, root, &environment()).unwrap()
}

#[test]
#[ignore = "explicit actual GNU Make legacy build acceptance (ICI_TEST_MAKE, ICI_TEST_CXX)"]
fn actual_legacy_make_build_runs_in_workdir_with_configured_jobs_clean_and_owned_default_shadow() {
    let root =
        fixture("jobs=2\nclean_argv=['make','clean']\nbuild_argv=['make','-j','{jobs}','all']");
    let canonical = root.path().canonicalize().unwrap();
    let invocation = registry::parse(["ici", "build"]).unwrap();
    let planned = planning::prepare(&invocation, root.path(), &environment(), false).unwrap();
    assert_eq!(
        planned.context_preparations.len(),
        1,
        "{:?}",
        planned.plan.limitations
    );
    let preparation = &planned.context_preparations[0];
    assert_eq!(preparation.build.system, "make");
    assert_eq!(preparation.build.shadow, "build/ici-make");
    assert_eq!(preparation.build.generated_roots, ["build/ici-make"]);
    assert_eq!(
        preparation
            .build
            .tasks
            .iter()
            .map(|task| (task.id.rsplit(':').next().unwrap(), task.cwd.as_str()))
            .collect::<Vec<_>>(),
        [
            ("clean", canonical.to_str().unwrap()),
            ("compile", canonical.to_str().unwrap())
        ]
    );
    assert!(
        preparation.build.tasks[1]
            .argv
            .ends_with(&["-j".into(), "2".into(), "all".into()])
    );
    assert_eq!(preparation.effects["limitations"], serde_json::json!([]));
    assert!(!root.path().join("build").exists() && !root.path().join(".ici").exists());
    let output = dispatch(root.path());
    let shadow = root.path().join("build/ici-make");
    assert!(
        shadow.join("app").is_file(),
        "{} {}",
        output.stdout,
        output.stderr
    );
    assert_eq!(
        fs::read_to_string(shadow.join("cwd.txt")).unwrap().trim(),
        canonical.to_str().unwrap()
    );
    let flags = fs::read_to_string(shadow.join("flags.txt")).unwrap();
    assert!(flags.contains("j2"), "{flags}");
    assert_eq!(
        fs::read_to_string(shadow.join("log.txt")).unwrap(),
        "clean\n"
    );
    assert!(shadow.join(".ici-owner.json").is_file());
    assert!(
        !output.stdout.contains("changed during") && !output.stderr.contains("changed during"),
        "{} {}",
        output.stdout,
        output.stderr
    );
    // The owned shadow is adopted on the next run and clean runs again before build.
    dispatch(root.path());
    assert_eq!(
        fs::read_to_string(shadow.join("log.txt")).unwrap(),
        "clean\nclean\n"
    );
    assert_eq!(
        fs::read_to_string(root.path().join("src/main.cpp")).unwrap(),
        "int main(){return 0;}\n"
    );
}

#[test]
#[ignore = "explicit actual GNU Make legacy build failure acceptance (ICI_TEST_MAKE, ICI_TEST_CXX)"]
fn actual_legacy_make_build_failures_and_unowned_shadow_are_not_reported_as_success() {
    // Failing recipe: the build is not a pass and nothing is published.
    let root = fixture("build_argv=['make','broken']");
    let output = dispatch(root.path());
    assert_ne!(output.exit_code, 0, "{} {}", output.stdout, output.stderr);
    assert!(!root.path().join("build/ici-make/app").exists());
    // A pre-existing unowned shadow is refused before any Make command and kept byte-for-byte.
    let root = fixture("build_argv=['make','all']");
    fs::create_dir_all(root.path().join("build/ici-make")).unwrap();
    fs::write(root.path().join("build/ici-make/user.txt"), "keep").unwrap();
    // The frozen pyz leaves no ownership marker in its default shadow, so a pyz-era
    // `build/ici-make` is refused already at planning time with the cause and the fix.
    let invocation = registry::parse(["ici", "build"]).unwrap();
    let planned = planning::prepare(&invocation, root.path(), &environment(), false).unwrap();
    assert!(planned.context_preparations.is_empty());
    let explained = planned.plan.limitations.iter().any(|limitation| {
        limitation.contains("\"build/ici-make\" already contains files that ici did not create")
            && limitation.contains("frozen pyz Make builds leave no ownership marker")
            && limitation.contains("remove that directory or set build.make.shadow_dir")
    });
    assert!(explained, "{:?}", planned.plan.limitations);
    assert!(!root.path().join("build/ici-make/.ici-owner.json").exists());
    let output = dispatch(root.path());
    assert_ne!(output.exit_code, 0, "{} {}", output.stdout, output.stderr);
    assert!(
        format!("{}{}", output.stdout, output.stderr).contains("shadow_dir")
            || fs::read_to_string(root.path().join(".ici/rust-v1/result.json"))
                .is_ok_and(|text| text.contains("shadow_dir")),
        "{} {}",
        output.stdout,
        output.stderr
    );
    assert_eq!(
        fs::read_to_string(root.path().join("build/ici-make/user.txt")).unwrap(),
        "keep"
    );
    assert!(!root.path().join("build/ici-make/cwd.txt").exists());
    // Frozen config validation rejects shell flags such as `-C` (casefolded `-c`) and invalid
    // jobs before any process or filesystem effect.
    for (make, message) in [
        ("build_argv=['make','-C','src','all']", "command shell"),
        ("jobs=65\nbuild_argv=['make','all']", "build.make.jobs"),
        (
            "out_of_tree='required'\nbuild_argv=['make','all']",
            "non-root workdir",
        ),
        ("build_argv=['make','-j{jobs}']", "placeholder"),
    ] {
        let root = fixture(make);
        let invocation = registry::parse(["ici", "build"]).unwrap();
        let error = commands::dispatch(&invocation, root.path(), &environment())
            .err()
            .unwrap_or_else(|| panic!("{make} was accepted"));
        assert!(error.to_string().contains(message), "{make}: {error}");
        assert!(!root.path().join("build").exists() && !root.path().join(".ici").exists());
    }
}
