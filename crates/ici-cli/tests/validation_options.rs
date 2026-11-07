//! Explicit legacy policy in modern configuration remains distinct from absent defaults.
use ici_cli::{planning, registry};
use std::{collections::BTreeMap, fs};

fn checks() -> String {
    let mut text = "schema_version=2\nprofile='deep'\n".to_owned();
    for check in ici_domain::builtin_catalog().checks {
        text.push_str(&format!(
            "[checks.\"{}\"]\nenabled={}\nrequired={}\n",
            check.id,
            matches!(check.id.as_str(), "python.test" | "python.coverage"),
            matches!(check.id.as_str(), "python.test" | "python.coverage")
        ));
    }
    text
}
#[test]
fn modern_explicit_coverage_policy_is_never_replaced_by_absent_defaults() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("app.py"), "def answer():\n    return 42\n").unwrap();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(
        root.path().join("tests/test_app.py"),
        "def test_answer():\n    pass\n",
    )
    .unwrap();
    let values = "min_line_cov=91.0\nmin_file_cov=92.0\nmin_branch_cov=93.0\nmin_func_cov=94.0\nmin_tem_score=3.25\nmin_changed_line_cov=95.0\n";
    let component = "\n[[components]]\nid='project'\nroot='.'\nlanguages=['python']\nsources=['app.py']\n[components.python]\nexecutable='/bin/sh'\n";
    for layout in ["defaults", "root"] {
        let mut text = checks();
        match layout {
            "defaults" => text.push_str(component),
            "root" => text.push_str(&format!("[engines.test]\n{values}{component}")),
            _ => unreachable!(),
        }
        fs::write(root.path().join("ici.toml"), text).unwrap();
        let prepared = planning::prepare(
            &registry::parse(["ici", "next", "verify"]).unwrap(),
            root.path(),
            &BTreeMap::new(),
            false,
        )
        .unwrap();
        let options = &prepared.validations["project.python.coverage"][0]
            .context
            .options;
        let actual = [
            options.min_line_cov,
            options.min_file_cov,
            options.min_branch_cov,
            options.min_func_cov,
            options.min_tem_score,
            options.min_changed_line_cov,
        ];
        assert_eq!(
            actual,
            if layout == "defaults" {
                [None; 6]
            } else {
                [
                    Some(91.0),
                    Some(92.0),
                    Some(93.0),
                    Some(94.0),
                    Some(3.25),
                    Some(95.0),
                ]
            },
            "{layout}: {:?}",
            prepared.project.origins
        );
        assert!(!root.path().join(".ici").exists());
    }
    fs::write(
        root.path().join("ici.toml"),
        format!("{}{component}[components.engines.test]\n{values}", checks()),
    )
    .unwrap();
    assert!(
        ici_config::resolve_with_environment(root.path(), None, None, &BTreeMap::new())
            .unwrap_err()
            .to_string()
            .contains("components[0].engines: unknown configuration key")
    );
}
