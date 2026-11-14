use ici_cli::{inputs, planning, registry};
use ici_domain::{Observation, TaskState};
use ici_execution::Runner;
use std::{collections::BTreeMap, fs, os::unix::fs::symlink};

#[test]
fn binary_code_changes_keep_scope_but_removed_selection_and_changed_bytes_remain_visible() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    fs::create_dir(root.path().join("build")).unwrap();
    fs::write(root.path().join("build/lib.a"), b"!<arch>\n").unwrap();
    let mut config = "schema_version=2\n".to_string();
    for check in ici_domain::builtin_catalog()
        .checks
        .iter()
        .filter(|c| c.language == "cpp")
    {
        config.push_str(&format!(
            "[checks.'{}']\nenabled={}\n",
            check.id,
            check.id == "cpp.binary-compat"
        ));
    }
    config.push_str("[builds.native]\nsystem='cmake'\ndirectory='build'\nartifacts=['*.a']\n[[components]]\nid='project'\nroot='.'\nlanguages=['cpp']\nsources=['main.cpp']\nbuild='native'\n");
    fs::write(root.path().join("ici.toml"), &config).unwrap();
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let env = BTreeMap::from([("PATH".into(), String::new())]);
    let prepare = || planning::prepare(&invocation, root.path(), &env, false).unwrap();
    let before = prepare();
    let snapshot = inputs::capture(&before).unwrap();
    let limits = inputs::binary_limits(&before);
    let scope = |plan: &planning::PreparedPlan,
                 snapshot: &ici_application::snapshot::InputSnapshot| {
        let mut observation = Observation::new("project.cpp.binary-compat", "scope-test");
        observation.state = TaskState::Succeeded;
        inputs::check_records(
            plan,
            &[observation],
            snapshot,
            &BTreeMap::new(),
            &Runner::with_environment(env.clone()),
            &inputs::InputVerification {
                stable: true,
                code_paths: inputs::code_evidence_paths(&before),
            },
            true,
        )
        .unwrap()[0]
            .scope_digest
            .clone()
    };
    let original_scope = scope(&before, &snapshot);
    fs::write(
        root.path().join("build/lib.a"),
        b"!<arch>\nchanged object bytes",
    )
    .unwrap();
    let after = prepare();
    let changed = inputs::capture(&after).unwrap();
    assert_eq!(scope(&after, &changed), original_scope);
    assert_eq!(snapshot.difference(&changed).changed, ["build/lib.a"]);
    // A provider switching to a generated artifact must still re-read the old user input.
    fs::write(
        root.path().join("ici.toml"),
        config.replace("artifacts=['*.a']", "artifacts=[]"),
    )
    .unwrap();
    let narrowed = prepare();
    let plain = inputs::capture(&narrowed).unwrap();
    assert!(!plain.files.contains_key("build/lib.a"));
    let pinned = inputs::capture_preserving(&narrowed, &snapshot, &limits, &[]).unwrap();
    assert!(pinned.files.contains_key("build/lib.a"));
    assert!(
        snapshot
            .difference(&pinned)
            .changed
            .contains(&"build/lib.a".into())
    );
    fs::remove_file(root.path().join("build/lib.a")).unwrap();
    assert!(inputs::capture_preserving(&narrowed, &snapshot, &limits, &[]).is_err());
    symlink("../main.cpp", root.path().join("build/lib.a")).unwrap();
    assert!(inputs::capture_preserving(&narrowed, &snapshot, &limits, &[]).is_err());
}
