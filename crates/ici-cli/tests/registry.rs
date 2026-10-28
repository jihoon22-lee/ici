use ici_cli::registry::{command, parse, surface};
use serde_json::Value;

#[test]
fn every_published_command_parameter_and_alias_remains_registered() {
    let inventory: Value = serde_json::from_str(include_str!(
        "../../../docs/design/rust-replacement/inventory.json"
    ))
    .unwrap();
    let surface = surface();
    assert_eq!(
        surface.oracle_source,
        inventory["oracle"]["source_sha"].as_str().unwrap()
    );
    let rows: Vec<_> = inventory["requirements"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|r| r["kind"] == "command" || r["kind"] == "command_group")
        .collect();
    assert_eq!(rows.len(), 37);
    assert_eq!(surface.commands.len(), 37);
    let mut parameters = 0;
    for row in rows {
        let label = row["facts"]["command"].as_str().unwrap();
        let route: Vec<_> = label
            .split_whitespace()
            .skip(1)
            .filter(|p| *p != "<global>")
            .collect();
        let definition = surface
            .commands
            .iter()
            .find(|c| c.route.iter().map(String::as_str).eq(route.iter().copied()))
            .unwrap();
        let options = row["facts"]["options"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        assert_eq!(definition.options.len(), options.len(), "{label}");
        for option in options {
            parameters += 1;
            let native = definition
                .options
                .iter()
                .find(|o| o.id == option["parameter"].as_str().unwrap())
                .unwrap();
            assert_eq!(
                serde_json::to_value(&native.flags).unwrap(),
                option["flags"]
            );
            assert_eq!(
                native.repeatable,
                option["type"].as_str().unwrap().starts_with("list[")
            );
            assert_eq!(native.positional, option["kind"] == "argument");
        }
    }
    assert_eq!(parameters, 115);
    command().debug_assert();
}
#[test]
fn native_help_and_version_need_no_project_or_external_tool() {
    for definition in surface().commands {
        let mut args = vec!["ici".to_owned()];
        args.extend(definition.route);
        args.push("--help".into());
        let error = parse(args).unwrap_err();
        assert_eq!(error.exit_code(), 0);
        assert_eq!(error.kind(), clap::error::ErrorKind::DisplayHelp);
    }
    let error = parse(["ici", "--version"]).unwrap_err();
    assert_eq!(error.exit_code(), 0);
    assert!(error.to_string().contains(ici_cli::VERSION));
    assert_eq!(parse(["ici", "-v"]).unwrap_err().exit_code(), 0);
}
#[test]
fn repeatable_selection_aliases_enum_and_integer_bounds_are_enforced() {
    let invocation = parse([
        "ici",
        "analyze",
        "--component",
        "one",
        "--component",
        "two",
        "--language",
        "python",
        "--language",
        "rust",
        "--output",
        "run.json",
        "--profile",
        "fast",
        "--json",
    ])
    .unwrap();
    assert_eq!(invocation.values("component"), ["one", "two"]);
    assert_eq!(invocation.values("language"), ["python", "rust"]);
    assert_eq!(invocation.value("result"), Some("run.json"));
    assert!(invocation.flag("json_mode"));
    assert!(parse(["ici", "analyze", "--profile", "unknown"]).is_err());
    assert!(parse(["ici", "verify", "--max-findings", "-1"]).is_err());
    assert_eq!(
        parse(["ici", "verify"]).unwrap().value("max_findings"),
        Some("5")
    );
    assert!(parse(["ici", "unknown"]).is_err());
    assert!(parse(["ici", "import-report", "report.sarif", "--format", "sarif"]).is_err());
    assert_eq!(
        parse(["ici", "next", "migrate"]).unwrap().value("source"),
        Some("ici.toml")
    );
    assert_eq!(
        parse(["ici", "diff", "old.json"])
            .unwrap()
            .value("baseline"),
        Some("old.json")
    );
}
