//! Frozen command/option union, parsed natively. This metadata is not implementation parity evidence.
use clap::{Arg, ArgAction, ArgMatches, Command};
use serde::Deserialize;
use std::{collections::BTreeMap, ffi::OsString};

#[derive(Clone, Debug, Deserialize)]
pub struct Surface {
    pub oracle_source: String,
    pub commands: Vec<CommandDefinition>,
}
#[derive(Clone, Debug, Deserialize)]
pub struct CommandDefinition {
    pub route: Vec<String>,
    pub about: Option<String>,
    pub options: Vec<OptionDefinition>,
}
#[derive(Clone, Debug, Deserialize)]
pub struct OptionDefinition {
    pub id: String,
    pub flags: Vec<String>,
    pub positional: bool,
    pub boolean: bool,
    pub repeatable: bool,
    pub required: bool,
    pub help: Option<String>,
    pub default: Option<String>,
    pub choices: Vec<String>,
    pub integer: bool,
    pub minimum: Option<i64>,
}
#[derive(Clone, Debug)]
pub enum Argument {
    Flag(bool),
    Values(Vec<String>),
}
#[derive(Clone, Debug)]
pub struct Invocation {
    pub route: Vec<String>,
    pub arguments: BTreeMap<String, Argument>,
}
impl Invocation {
    pub fn flag(&self, id: &str) -> bool {
        matches!(self.arguments.get(id), Some(Argument::Flag(true)))
    }
    pub fn values(&self, id: &str) -> &[String] {
        match self.arguments.get(id) {
            Some(Argument::Values(values)) => values,
            _ => &[],
        }
    }
    pub fn value(&self, id: &str) -> Option<&str> {
        self.values(id).first().map(String::as_str)
    }
    pub fn command(&self) -> String {
        self.route.join(" ")
    }
}
pub fn surface() -> Surface {
    serde_json::from_str(include_str!("../data/commands.json"))
        .expect("frozen command surface validated by tests")
}
pub fn command() -> Command {
    let surface = surface();
    build(&surface, &[])
}
fn build(surface: &Surface, route: &[String]) -> Command {
    let name = route.last().map_or("ici", String::as_str);
    let mut command = Command::new(name.to_owned())
        .version(crate::VERSION)
        .disable_version_flag(true);
    if let Some(definition) = surface.commands.iter().find(|c| c.route == route) {
        if let Some(about) = &definition.about {
            command = command.about(about);
        }
        for option in &definition.options {
            command = command.arg(argument(option));
        }
    }
    let mut children: Vec<_> = surface
        .commands
        .iter()
        .filter(|c| c.route.len() == route.len() + 1 && c.route.starts_with(route))
        .collect();
    children.sort_by_key(|c| &c.route);
    if !children.is_empty() {
        command = command
            .subcommand_required(true)
            .arg_required_else_help(true);
    }
    for child in children {
        command = command.subcommand(build(surface, &child.route));
    }
    command
}
fn argument(option: &OptionDefinition) -> Arg {
    let mut arg = Arg::new(option.id.clone()).required(option.required);
    if let Some(help) = &option.help {
        arg = arg.help(help);
    }
    for flag in &option.flags {
        if let Some(long) = flag.strip_prefix("--") {
            if arg.get_long().is_some() {
                arg = arg.visible_alias(long.to_owned());
            } else {
                arg = arg.long(long.to_owned());
            }
        } else if let Some(short) = flag.strip_prefix('-').and_then(|s| s.chars().next()) {
            arg = arg.short(short);
        }
    }
    if option.id == "version" {
        return arg.action(ArgAction::Version);
    }
    if option.boolean {
        return arg.action(ArgAction::SetTrue);
    }
    arg = arg.action(if option.repeatable {
        ArgAction::Append
    } else {
        ArgAction::Set
    });
    if let Some(default) = &option.default {
        arg = arg.default_value(default);
    }
    if !option.choices.is_empty() {
        arg = arg.value_parser(clap::builder::PossibleValuesParser::new(
            option.choices.clone(),
        ));
    }
    // Parse as text for one stable argument representation, validating range before dispatch.
    if option.integer {
        let minimum = option.minimum;
        arg = arg.value_parser(move |value: &str| -> std::result::Result<String, String> {
            let number: i64 = value
                .parse()
                .map_err(|_| "expected an integer".to_owned())?;
            if minimum.is_some_and(|minimum| number < minimum) {
                return Err(format!("must be at least {}", minimum.expect("checked")));
            }
            Ok(value.into())
        });
    }
    arg
}
pub fn parse(
    args: impl IntoIterator<Item = impl Into<OsString> + Clone>,
) -> std::result::Result<Invocation, clap::Error> {
    let matches = command().try_get_matches_from(args)?;
    Ok(from_matches(matches))
}
fn from_matches(matches: ArgMatches) -> Invocation {
    let mut route = vec![];
    let mut current = &matches;
    while let Some((name, nested)) = current.subcommand() {
        route.push(name.to_owned());
        current = nested;
    }
    let definition = surface()
        .commands
        .into_iter()
        .find(|c| c.route == route)
        .expect("parsed registry command");
    let mut arguments = BTreeMap::new();
    for option in definition.options {
        if option.id == "version" {
            continue;
        }
        let value = if option.boolean {
            Argument::Flag(current.get_flag(&option.id))
        } else {
            Argument::Values(
                current
                    .get_many::<String>(&option.id)
                    .map(|values| values.cloned().collect())
                    .unwrap_or_default(),
            )
        };
        arguments.insert(option.id, value);
    }
    Invocation { route, arguments }
}
