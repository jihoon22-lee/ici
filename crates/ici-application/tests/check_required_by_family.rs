//! Which families lower a named check's `required`. Only schema 1 does: frozen next composition
//! gives a named check `DEFAULT_REQUIRED = False` (`config/composition.py:74`). Schema 2 and the
//! legacy family keep the catalog value.
use ici_application::plan::configured_check;
use ici_domain::builtin_catalog;
use std::{collections::BTreeMap, fs};

const NAMED: &str = "[checks.\"python.security\"]\nenabled = true\n";
const SCHEMA_ONE: &str = "schema_version = 1\n[workspace]\nname = \"demo\"\n[[components]]\n\
                          id = \"app\"\nroot = \".\"\nlanguages = [\"python\"]\n";

fn required(config: &str) -> Option<bool> {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join(".git")).unwrap();
    fs::write(root.path().join("main.py"), "x = 1\n").unwrap();
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let project =
        ici_config::resolve_with_environment(root.path(), None, None, &BTreeMap::new()).ok()?;
    let catalog = builtin_catalog();
    let check = catalog
        .checks
        .iter()
        .find(|check| check.id == "python.security")
        .unwrap();
    Some(configured_check(&project, &project.components[0], check).1)
}

#[test]
fn schema_one_makes_a_check_named_without_required_not_required() {
    assert_eq!(
        required(SCHEMA_ONE),
        Some(true),
        "unnamed keeps the catalog"
    );
    assert_eq!(
        required(&format!("{SCHEMA_ONE}{NAMED}")),
        Some(false),
        "named with only `enabled`"
    );
    assert_eq!(
        required(&format!("{SCHEMA_ONE}{NAMED}required = true\n")),
        Some(true),
        "named with an explicit value"
    );
}

#[test]
fn schema_two_and_legacy_keep_the_catalog_for_a_named_check() {
    assert_eq!(
        required(&format!("schema_version = 2\n{NAMED}")),
        Some(true)
    );
    assert_eq!(
        required("[engines.security]\nenabled = true\n"),
        Some(true),
        "legacy engine table"
    );
    // A legacy file cannot name a check; a resolved one must still not be lowered.
    assert_ne!(
        required(&format!("[project]\nname = \"demo\"\n{NAMED}")),
        Some(false)
    );
}
