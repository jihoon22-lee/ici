//! Validated shell-free command plans for handwritten Make projects (frozen `core/make.py`).
//!
//! Validation order, messages and `{jobs}` substitution follow the frozen contract exactly.
use crate::CppError;
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const MAX_ARGV_TOKENS: usize = 64;
pub const MAX_ARGV_TOKEN_CHARS: usize = 1024;
pub const MAX_ARGV_CHARS: usize = 32 * 1024;
const SHELLS: [&str; 8] = [
    "sh",
    "bash",
    "dash",
    "zsh",
    "fish",
    "cmd",
    "powershell",
    "pwsh",
];
const SHELL_FLAGS: [&str; 3] = ["-c", "/c", "-command"];

/// One normalized Make command transport for one build variant.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MakeSettings {
    pub descriptor: String,
    /// Project-relative POSIX directory, `.` for the project root; cwd of every Make command.
    pub workdir: String,
    /// Project-relative POSIX shadow directory shared by every variant; never the root.
    pub shadow: String,
    pub configure_argv: Vec<String>,
    pub clean_argv: Vec<String>,
    pub build_argv: Vec<String>,
    pub test_argv: Vec<String>,
    /// Configured `build.make.jobs` (1..=64), substituted for whole `{jobs}` tokens.
    pub jobs: u64,
    pub out_of_tree: String,
}

fn error(message: impl Into<String>) -> CppError {
    CppError::new("make-config", message)
}

fn variant_prefix(variant: &str) -> Result<&'static str, CppError> {
    match variant {
        // `test` is the descriptor-free legacy request name; frozen Make tests use release argv.
        "release" | "test" => Ok(""),
        "coverage" => Ok("coverage_"),
        "sanitize" => Ok("sanitize_"),
        "thread-sanitize" => Ok("thread_sanitize_"),
        other => Err(error(format!(
            "build.make has no command contract for the {other} variant"
        ))),
    }
}

/// Frozen `_contained_path`, lexically: no backslash, drive, absolute path or `..` component.
/// Symlink traversal is refused later by the no-follow preparation guard.
pub fn contained_make_path(
    value: &Value,
    setting: &str,
    allow_root: bool,
) -> Result<String, CppError> {
    let Some(text) = value.as_str().filter(|text| !text.is_empty()) else {
        return Err(error(format!(
            "{setting} must be a project-relative POSIX path"
        )));
    };
    let bytes = text.as_bytes();
    let drive = bytes.len() >= 2 && bytes[1] == b':' && bytes[0].is_ascii_alphabetic();
    if text.contains('\\') || drive {
        return Err(error(format!(
            "{setting} must be a project-relative POSIX path"
        )));
    }
    if text.starts_with('/') || text.split('/').any(|part| part == "..") {
        return Err(error(format!(
            "{setting} must stay within the project root"
        )));
    }
    let parts: Vec<_> = text
        .split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .collect();
    if parts.is_empty() {
        if !allow_root {
            return Err(error(format!("{setting} must not be the project root")));
        }
        return Ok(".".into());
    }
    Ok(parts.join("/"))
}

/// Frozen `_argv`: bounded literal tokens, only a whole `{jobs}` placeholder, no shells.
pub fn make_argv(
    value: Option<&Value>,
    setting: &str,
    jobs: u64,
    required: bool,
) -> Result<Vec<String>, CppError> {
    let empty = match value {
        None | Some(Value::Null) => true,
        Some(Value::Array(items)) => items.is_empty(),
        _ => false,
    };
    if empty {
        if required {
            return Err(error(format!(
                "{setting} must contain a direct command argv"
            )));
        }
        return Ok(vec![]);
    }
    let Some(items) = value
        .and_then(Value::as_array)
        .filter(|items| (1..=MAX_ARGV_TOKENS).contains(&items.len()))
    else {
        return Err(error(format!(
            "{setting} must contain 1 to {MAX_ARGV_TOKENS} tokens"
        )));
    };
    let mut result = Vec::with_capacity(items.len());
    for (index, item) in items.iter().enumerate() {
        let Some(token) = item.as_str().filter(|token| !token.is_empty()) else {
            return Err(error(format!(
                "{setting}[{index}] must be a non-empty string"
            )));
        };
        if token.chars().count() > MAX_ARGV_TOKEN_CHARS || token.chars().any(|c| (c as u32) < 32) {
            return Err(error(format!(
                "{setting}[{index}] must be a safe token of at most {MAX_ARGV_TOKEN_CHARS} characters"
            )));
        }
        if token.contains(['$', '`']) {
            return Err(error(format!(
                "{setting}[{index}] contains a shell metacharacter or unknown placeholder"
            )));
        }
        if token.contains("{jobs}") && token != "{jobs}" {
            return Err(error(format!(
                "{setting}[{index}] must use {{jobs}} as a whole token"
            )));
        }
        if token.contains(['{', '}']) {
            if token != "{jobs}" {
                return Err(error(format!(
                    "{setting}[{index}] contains an unknown placeholder"
                )));
            }
            result.push(jobs.to_string());
        } else {
            result.push(token.to_owned());
        }
    }
    validate_direct_make_argv(&result, setting)?;
    Ok(result)
}

/// Shell and aggregate checks shared by configured and tool-resolved argv.
pub fn validate_direct_make_argv(argv: &[String], setting: &str) -> Result<(), CppError> {
    let executable = argv
        .first()
        .map(|first| {
            std::path::Path::new(first)
                .file_name()
                .map(|name| name.to_string_lossy().to_lowercase())
                .unwrap_or_default()
        })
        .unwrap_or_default();
    if SHELLS.contains(&executable.as_str())
        || argv
            .iter()
            .skip(1)
            .any(|token| SHELL_FLAGS.contains(&token.to_lowercase().as_str()))
    {
        return Err(error(format!("{setting} must not invoke a command shell")));
    }
    if argv
        .iter()
        .map(|token| token.chars().count())
        .sum::<usize>()
        > MAX_ARGV_CHARS
    {
        return Err(error(format!(
            "{setting} exceeds the aggregate character bound"
        )));
    }
    if argv.len() > MAX_ARGV_TOKENS
        || argv.iter().any(|token| {
            token.is_empty()
                || token.chars().count() > MAX_ARGV_TOKEN_CHARS
                || token.chars().any(|c| (c as u32) < 32)
                || token.contains(['$', '`'])
        })
    {
        return Err(error(format!(
            "{setting} must contain bounded literal tokens"
        )));
    }
    Ok(())
}

/// Normalize `build.make` for one variant. Pure: no filesystem access, process or mutation.
pub fn make_settings(
    table: Option<&Value>,
    variant: &str,
    descriptor: &str,
) -> Result<MakeSettings, CppError> {
    let table = table
        .and_then(Value::as_object)
        .filter(|table| table.get("enabled") == Some(&Value::Bool(true)))
        .ok_or_else(|| error("build.make.enabled must be true for the Make backend"))?;
    let jobs = match table.get("jobs") {
        None => 1,
        Some(Value::Number(number)) => number
            .as_u64()
            .filter(|jobs| (1..=64).contains(jobs))
            .ok_or_else(|| error("build.make.jobs must be an integer from 1 to 64"))?,
        Some(_) => return Err(error("build.make.jobs must be an integer from 1 to 64")),
    };
    let out_of_tree = match table.get("out_of_tree") {
        None => "allow",
        Some(Value::String(value)) if value == "allow" || value == "required" => value.as_str(),
        Some(_) => return Err(error("build.make.out_of_tree must be allow or required")),
    };
    let dot = Value::String(".".into());
    let default_shadow = Value::String("build/ici-make".into());
    let workdir = contained_make_path(
        table.get("workdir").unwrap_or(&dot),
        "build.make.workdir",
        true,
    )?;
    let shadow = contained_make_path(
        table.get("shadow_dir").unwrap_or(&default_shadow),
        "build.make.shadow_dir",
        false,
    )?;
    if out_of_tree == "required" && workdir == "." {
        return Err(error(
            "build.make.out_of_tree=required needs a non-root workdir",
        ));
    }
    let prefix = variant_prefix(variant)?;
    let build_key = format!("{prefix}build_argv");
    let test_key = format!("{prefix}test_argv");
    let truthy = |key: &str| match table.get(key) {
        None | Some(Value::Null) => false,
        Some(Value::Array(items)) => !items.is_empty(),
        Some(Value::String(text)) => !text.is_empty(),
        Some(Value::Bool(value)) => *value,
        Some(_) => true,
    };
    if !prefix.is_empty() && !truthy(&build_key) {
        return Err(error(format!(
            "build.make.{build_key} is required for {variant}"
        )));
    }
    let test_value = if !prefix.is_empty() && truthy(&test_key) {
        table.get(&test_key)
    } else {
        table.get("test_argv")
    };
    Ok(MakeSettings {
        descriptor: descriptor.into(),
        configure_argv: make_argv(
            table.get("configure_argv"),
            "build.make.configure_argv",
            jobs,
            false,
        )?,
        clean_argv: make_argv(
            table.get("clean_argv"),
            "build.make.clean_argv",
            jobs,
            false,
        )?,
        build_argv: make_argv(
            table.get(&build_key),
            &format!("build.make.{build_key}"),
            jobs,
            true,
        )?,
        test_argv: make_argv(test_value, &format!("build.make.{test_key}"), jobs, false)?,
        workdir,
        shadow,
        jobs,
        out_of_tree: out_of_tree.into(),
    })
}
