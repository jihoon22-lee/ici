use ici_config::legacy_migration_report;
use std::{collections::BTreeMap, fs};

#[test]
fn legacy_layers_explain_winners_losers_and_source_lines_without_merging() {
    let tree = tempfile::tempdir().unwrap();
    let root = tree.path();
    fs::create_dir_all(root.join("config/ici")).unwrap();
    fs::write(
        root.join("config/ici/config.toml"),
        "[engines.test]\nrequired = false\nmin_score = 1\n",
    )
    .unwrap();
    fs::write(
        root.join("ici.toml"),
        "# original\n[engines.test]\nrequired = true\nmin_score = 1\n",
    )
    .unwrap();
    fs::write(root.join("dev.toml"), "[engines.test]\nmin_score = 2\n").unwrap();
    fs::write(
        root.join("explicit.toml"),
        "[engines.test]\nmin_score = 3\n",
    )
    .unwrap();
    let environment = BTreeMap::from([
        (
            "XDG_CONFIG_HOME".into(),
            root.join("config").display().to_string(),
        ),
        ("ICI_CONFIG".into(), "explicit.toml".into()),
    ]);
    let before = fs::read(root.join("ici.toml")).unwrap();
    let report = legacy_migration_report(root, &environment);
    assert_eq!(report.sources.len(), 4);
    assert!(report.policy_is_contested);
    assert_eq!(report.contested.len(), 2);
    let score = &report.contested[0];
    assert_eq!(score.key, "engines.test.min_score");
    assert_eq!(score.winner.label, "ICI_CONFIG");
    assert_eq!(score.winner.value, 3);
    assert_eq!(score.overridden.len(), 3);
    assert_eq!(report.contested[1].winner.origin.line, 3);
    assert_eq!(fs::read(root.join("ici.toml")).unwrap(), before);
}

#[test]
fn equal_values_are_not_contested_and_malformed_layers_are_visible() {
    let tree = tempfile::tempdir().unwrap();
    fs::write(
        tree.path().join("ici.toml"),
        "[engines.line]\nwarn_limit=500\n",
    )
    .unwrap();
    fs::write(
        tree.path().join("dev.toml"),
        "[engines.line]\nwarn_limit=500\n",
    )
    .unwrap();
    let environment = BTreeMap::from([
        ("HOME".into(), tree.path().display().to_string()),
        ("ICI_CONFIG".into(), "~/bad.toml".into()),
    ]);
    fs::write(tree.path().join("bad.toml"), "[invalid").unwrap();
    let report = legacy_migration_report(tree.path(), &environment);
    assert_eq!(report.sources.len(), 3);
    assert!(
        report.sources[2]
            .error
            .as_ref()
            .unwrap()
            .contains("invalid TOML")
    );
    assert!(report.contested.is_empty());
    assert!(!report.policy_is_contested);
}
