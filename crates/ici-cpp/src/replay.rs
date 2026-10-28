use crate::{input::scoped, replay_policy::*, *};
use regex::Regex;
use std::{collections::BTreeMap, os::unix::fs::PermissionsExt, path::Path, sync::LazyLock};
static DRIVER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^(?:[A-Za-z0-9_+.]+-)*(?:gcc|g\+\+|clang|clang\+\+|cc|c\+\+)(?:-[0-9][A-Za-z0-9_.-]*)?(?:\.exe)?$").unwrap()
});
static OPT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^-O(?:[0-3sgz]|fast)?$").unwrap());
fn dropped(token: &str) -> bool {
    DROP_EXACT.contains(&token)
        || DROP_JOINED
            .iter()
            .any(|p| token.starts_with(p) && token != *p)
        || DROP_PREFIXES.iter().any(|p| token.starts_with(p))
}
fn rejected(token: &str) -> bool {
    REJECT_EXACT.contains(&token) || REJECT_PREFIXES.iter().any(|p| token.starts_with(p))
}
fn safe(token: &str) -> bool {
    SAFE_EXACT.contains(&token)
        || OPT.is_match(token)
        || (token.starts_with("-m") && token.len() > 2)
        || ["-xc", "-xc++"].contains(&token)
        || token
            .strip_prefix("--language=")
            .is_some_and(|v| ["c", "c++"].contains(&v))
        || SAFE_PREFIXES
            .iter()
            .any(|p| token.starts_with(p) && token != *p)
}
fn option_value(argv: &[String], index: usize) -> Result<&str, CppError> {
    argv.get(index + 1)
        .filter(|v| !v.is_empty() && !v.starts_with('-') && !v.contains('\0'))
        .map(String::as_str)
        .ok_or_else(|| {
            CppError::new(
                "missing-option-value",
                format!("{} has no safe value", argv[index]),
            )
        })
}

pub fn diagnostic_warning_argument(argument: &str) -> Result<Option<String>, CppError> {
    let mut value = argument.to_string();
    loop {
        if value == "-Werror" {
            return Ok(None);
        }
        if ["-pedantic-errors", "--pedantic-errors"].contains(&value.as_str()) {
            value = "-pedantic".into();
            continue;
        }
        if value == "-Werror-implicit-function-declaration" {
            value = "-Wimplicit-function-declaration".into();
            continue;
        }
        if let Some(warning) = value.strip_prefix("-Werror=") {
            if warning.is_empty() {
                return Ok(None);
            }
            value = format!("-W{warning}");
            continue;
        }
        break;
    }
    if value != argument && (dropped(&value) || rejected(&value) || !safe(&value)) {
        return Err(CppError::new(
            "unsafe-tooling-warning-policy",
            "warning demotion is not a safe compiler option",
        ));
    }
    Ok(Some(value))
}

/// The caller supplies an approved resolved tool; R2 verifies its pinned digest before execution.
/// Project-contained or wrapper drivers are not accepted for read-only analysis replay.
pub fn replay_command(
    root: &Path,
    unit: &CompilationUnit,
    compiler: &Path,
    purpose: ReplayPurpose,
) -> Result<ReplayCommand, CppError> {
    compiler_command(root, unit, compiler, Some(purpose))
}

/// Construct an explicit object compile inside the leased Rust build namespace.
/// Output controls from captured flags are replaced; warning and debug policy is preserved.
pub fn owned_object_command(
    root: &Path,
    unit: &CompilationUnit,
    compiler: &Path,
    output: &Path,
) -> Result<ReplayCommand, CppError> {
    let root = root
        .canonicalize()
        .map_err(|error| CppError::new("invalid-root", error.to_string()))?;
    let owned = root.join(".ici/rust-v1/build");
    if !output.is_absolute()
        || output
            .components()
            .any(|part| matches!(part, std::path::Component::ParentDir))
        || output
            .strip_prefix(&owned)
            .map_or(true, |relative| relative.components().count() < 3)
    {
        return Err(CppError::new(
            "unowned-compile-output",
            "object output must remain inside an owned Rust build variant",
        ));
    }
    let mut command = compiler_command(&root, unit, compiler, None)?;
    command
        .argv
        .extend(["-c".into(), "-o".into(), output.display().to_string()]);
    Ok(command)
}
fn debug_flag(token: &str) -> bool {
    [
        "-g",
        "-g0",
        "-g1",
        "-g2",
        "-g3",
        "-ggdb",
        "-ggdb0",
        "-ggdb1",
        "-ggdb2",
        "-ggdb3",
        "-gdwarf-2",
        "-gdwarf-3",
        "-gdwarf-4",
        "-gdwarf-5",
    ]
    .contains(&token)
}
fn compiler_command(
    root: &Path,
    unit: &CompilationUnit,
    compiler: &Path,
    purpose: Option<ReplayPurpose>,
) -> Result<ReplayCommand, CppError> {
    let root = root
        .canonicalize()
        .map_err(|e| CppError::new("invalid-root", e.to_string()))?;
    if unit.diagnostics.iter().any(|d| d.level == "error") {
        return Err(CppError::new(
            "invalid-compilation-unit",
            "unit has incomplete/invalid input evidence",
        ));
    }
    let original = unit
        .argv
        .first()
        .ok_or_else(|| CppError::new("missing-compiler", "compile argv is empty"))?;
    if !DRIVER.is_match(original.rsplit(['/', '\\']).next().unwrap_or(original)) {
        return Err(CppError::new(
            "unsupported-compiler-driver",
            "analysis requires a direct GCC/Clang driver",
        ));
    }
    if !compiler.is_absolute() {
        return Err(CppError::new(
            "compiler-unavailable",
            "compiler invocation path must be absolute",
        ));
    }
    let physical_compiler = compiler
        .canonicalize()
        .map_err(|e| CppError::new("compiler-unavailable", e.to_string()))?;
    let metadata = physical_compiler
        .metadata()
        .map_err(|e| CppError::new("compiler-unavailable", e.to_string()))?;
    if !metadata.is_file()
        || metadata.permissions().mode() & 0o111 == 0
        || physical_compiler.starts_with(&root)
    {
        return Err(CppError::new(
            "project-compiler-rejected",
            "compiler must be an approved regular executable outside the project",
        ));
    }
    let (_, scope, cwd) = scoped(&root, &root, &unit.directory)?;
    let (_, source_scope, source) = scoped(&root, &root, &unit.source)?;
    if scope != "project" || source_scope != "project" || !cwd.is_dir() || !source.is_file() {
        return Err(CppError::new(
            "stale-compilation-unit",
            "cwd/source no longer belongs to the project",
        ));
    }
    // A compiler alias may select C++ mode through argv[0]; validate the target but execute the alias.
    let mut argv = vec![compiler.display().to_string()];
    let mut i = 1;
    let mut source_seen = false;
    while i < unit.argv.len() {
        let token = &unit.argv[i];
        if token == "--" {
            for operand in &unit.argv[i + 1..] {
                let (_, _, path) = scoped(&root, &cwd, operand)?;
                if path != source || source_seen {
                    return Err(CppError::new(
                        "extra-source-operand",
                        "unexpected compiler operand",
                    ));
                }
                source_seen = true;
                argv.push(source.display().to_string());
            }
            break;
        }
        if DROP_VALUE.contains(&token.as_str()) {
            option_value(&unit.argv, i)?;
            i += 2;
            continue;
        }
        let keep_debug = purpose.is_none() && debug_flag(token);
        if (dropped(token) && !keep_debug)
            || (purpose == Some(ReplayPurpose::UnusedFunctions) && token == "-w")
        {
            i += 1;
            continue;
        }
        if rejected(token) || token.starts_with('@') {
            return Err(CppError::new(
                "unsafe-compiler-option",
                format!("compiler option {token} is not allowed during replay"),
            ));
        }
        if PRESERVE_VALUE.contains(&token.as_str()) {
            let value = option_value(&unit.argv, i)?;
            if ["-x", "--language"].contains(&token.as_str())
                && !(["c", "c++", "c-header", "c++-header", "none"].contains(&value))
            {
                return Err(CppError::new(
                    "unsafe-compiler-language",
                    "unsupported analysis language",
                ));
            }
            argv.extend([token.clone(), value.into()]);
            i += 2;
            continue;
        }
        if token.starts_with('-') {
            if !safe(token) && !keep_debug {
                return Err(CppError::new(
                    "unsupported-compiler-option",
                    format!("unknown compiler option {token}; no unsafe guess"),
                ));
            }
            let value = if purpose.is_none() {
                Some(token.clone())
            } else {
                diagnostic_warning_argument(token)?
            };
            if let Some(value) = value {
                argv.push(value);
            }
            i += 1;
            continue;
        }
        let (_, _, operand) = scoped(&root, &cwd, token)?;
        if operand != source || source_seen {
            return Err(CppError::new(
                "extra-source-operand",
                format!("unexpected compiler operand {token}"),
            ));
        }
        source_seen = true;
        argv.push(source.display().to_string());
        i += 1;
    }
    if !source_seen {
        return Err(CppError::new(
            "missing-source-operand",
            "no selected translation unit in argv",
        ));
    }
    match purpose {
        None => {}
        Some(ReplayPurpose::Diagnostics | ReplayPurpose::Boundaries) => argv.extend([
            "-Wall".into(),
            "-Wextra".into(),
            "-fsyntax-only".into(),
            "-fdiagnostics-color=never".into(),
        ]),
        Some(ReplayPurpose::UnusedFunctions) => {
            argv.extend([
                "-Wunused-function".into(),
                "-Wno-error=unused-function".into(),
                "-fdiagnostics-show-option".into(),
                "-fdiagnostics-color=never".into(),
                "-S".into(),
                "-o".into(),
                "/dev/null".into(),
            ]);
        }
        Some(ReplayPurpose::IncludeTrace) => argv.extend([
            "-w".into(),
            "-E".into(),
            "-H".into(),
            "-o".into(),
            "/dev/null".into(),
        ]),
        Some(ReplayPurpose::AstJson) => argv.extend([
            "-fsyntax-only".into(),
            "-Xclang".into(),
            "-ast-dump=json".into(),
        ]),
    }
    crate::arguments::validate(&argv)?;
    Ok(ReplayCommand {
        argv,
        cwd,
        source,
        configuration: unit.configuration.clone(),
    })
}

/// No compiler override hooks are introduced; per-tool library paths may be supplied by its sealed pack.
pub fn replay_environment() -> BTreeMap<String, String> {
    BTreeMap::from([
        ("LANG".into(), "C".into()),
        ("LC_ALL".into(), "C".into()),
        ("TERM".into(), "dumb".into()),
    ])
}
