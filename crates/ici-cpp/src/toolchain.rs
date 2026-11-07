//! Observe the selected GCC driver's C++ library search; never guess a version or host path.
use crate::{CompilationUnit, CppError, ReplayPurpose};
use ici_domain::TaskSpec;
use ici_execution::TaskOutcome;
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    path::{Path, PathBuf},
};
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct GccStdlibProjection {
    pub arguments: Vec<String>,
    pub cpp_search: Vec<PathBuf>,
    pub c_search: Vec<PathBuf>,
    pub gnu_driver: bool,
}
/// Two bounded pure tasks. Explicit -nostdinc/-nostdinc++ needs no implicit projection.
pub fn gcc_include_search_tasks(
    root: &Path,
    unit: &CompilationUnit,
    compiler: &Path,
) -> Result<Vec<TaskSpec>, CppError> {
    let replay = crate::replay_command(root, unit, compiler, ReplayPurpose::Diagnostics)?;
    if replay
        .argv
        .iter()
        .any(|argument| matches!(argument.as_str(), "-nostdinc" | "-nostdinc++"))
    {
        return Ok(vec![]);
    }
    let mut selectors = vec![];
    let mut index = 1;
    while index < replay.argv.len() {
        let argument = &replay.argv[index];
        if [
            "--sysroot",
            "-isysroot",
            "-mabi",
            "-march",
            "-mcpu",
            "-mtune",
        ]
        .contains(&argument.as_str())
        {
            let value = replay
                .argv
                .get(index + 1)
                .filter(|value| !value.is_empty() && !value.starts_with('-'))
                .ok_or_else(|| {
                    CppError::new(
                        "toolchain-selector-value",
                        "GCC toolchain selector lacks a value",
                    )
                })?;
            selectors.extend([argument.clone(), value.clone()]);
            index += 2;
            continue;
        }
        if argument.starts_with("--sysroot=")
            || argument.starts_with("-isysroot=")
            || (argument.starts_with("-m") && argument.len() > 2)
        {
            selectors.push(argument.clone());
        }
        index += 1;
    }
    let digest = ici_execution::process::executable_digest(compiler)
        .map_err(|error| CppError::new("compiler-identity", error.to_string()))?;
    Ok(["c++", "c"]
        .into_iter()
        .map(|language| {
            let mut argv = vec![compiler.display().to_string()];
            argv.extend(selectors.clone());
            argv.extend([
                "-E".into(),
                "-x".into(),
                language.into(),
                "-v".into(),
                "/dev/null".into(),
            ]);
            let mut task = TaskSpec::command(
                format!("gcc-search:{}:{language}", unit.configuration),
                "gcc.include-search",
                argv,
                replay.cwd.display().to_string(),
            );
            task.tool_digest = Some(digest.clone());
            task.env_overlay = crate::replay_environment();
            task.timeout_seconds = Some(5.0);
            task.output_limit_bytes = Some(131072);
            task.variant = Some(unit.variant.clone());
            task
        })
        .collect())
}
pub fn parse_compiler_include_search(output: &str, cwd: &Path) -> Result<Vec<PathBuf>, CppError> {
    if output.len() > 131072
        || output.contains('\0')
        || !cwd.canonicalize().is_ok_and(|path| path.is_dir())
    {
        return Err(CppError::new(
            "include-search-bounds",
            "Compiler include search stream or working directory is invalid",
        ));
    }
    let lines: Vec<_> = output.lines().collect();
    let starts: Vec<_> = lines
        .iter()
        .enumerate()
        .filter_map(|(index, line)| {
            (line.trim() == "#include <...> search starts here:").then_some(index)
        })
        .collect();
    let ends: Vec<_> = lines
        .iter()
        .enumerate()
        .filter_map(|(index, line)| (line.trim() == "End of search list.").then_some(index))
        .collect();
    if starts.len() != 1 || ends.len() != 1 || ends[0] <= starts[0] {
        return Err(CppError::new(
            "include-search-shape",
            "Compiler include search block is missing, duplicated or malformed",
        ));
    }
    let mut roots = vec![];
    let mut seen = BTreeSet::new();
    for line in &lines[starts[0] + 1..ends[0]] {
        let path = line
            .trim()
            .strip_suffix(" (framework directory)")
            .unwrap_or(line.trim())
            .trim_end();
        if path.is_empty()
            || path.len() > 4096
            || path.contains(['\0', '\r', '\n'])
            || !Path::new(path).is_absolute()
        {
            return Err(CppError::new(
                "include-search-path",
                "Compiler include search entry is not a bounded absolute directory",
            ));
        }
        let path = Path::new(path).canonicalize().map_err(|_| {
            CppError::new(
                "include-search-path",
                "Compiler include search directory is unavailable",
            )
        })?;
        if !path.is_dir() || path.parent().is_none() || !seen.insert(path.clone()) {
            return Err(CppError::new(
                "include-search-path",
                "Compiler include search contains a root, duplicate or non-directory",
            ));
        }
        roots.push(path);
        if roots.len() > 64 {
            return Err(CppError::new(
                "include-search-count",
                "Compiler include search exceeds 64 entries",
            ));
        }
    }
    if roots.is_empty() {
        return Err(CppError::new(
            "include-search-empty",
            "Compiler include search has no directories",
        ));
    }
    Ok(roots)
}
pub fn gcc_standard_library_projection(
    cpp: &TaskOutcome,
    c: &TaskOutcome,
    cwd: &Path,
) -> Result<GccStdlibProjection, CppError> {
    if !cpp.prerequisite_succeeded() || !c.prerequisite_succeeded() {
        return Err(CppError::new(
            "gcc-include-probe-incomplete",
            "GCC C++ and C include probes must finish without timeout, truncation, cancellation or nonzero exit",
        ));
    }
    let cpp = std::str::from_utf8(&cpp.stderr).map_err(|_| {
        CppError::new(
            "gcc-include-probe-encoding",
            "GCC C++ include output is not UTF-8",
        )
    })?;
    if !cpp.lines().any(|line| {
        line.strip_prefix("COLLECT_GCC=")
            .is_some_and(|value| !value.trim().is_empty())
    }) {
        return Ok(GccStdlibProjection::default());
    }
    let c = std::str::from_utf8(&c.stderr).map_err(|_| {
        CppError::new(
            "gcc-include-probe-encoding",
            "GCC C include output is not UTF-8",
        )
    })?;
    let cpp_search = parse_compiler_include_search(cpp, cwd)?;
    let c_search = parse_compiler_include_search(c, cwd)?;
    let c_roots: BTreeSet<_> = c_search.iter().collect();
    let roots: Vec<_> = cpp_search
        .iter()
        .filter(|path| !c_roots.contains(path))
        .collect();
    if roots.is_empty() {
        return Err(CppError::new(
            "gcc-stdlib-unresolved",
            "GCC did not expose any C++-specific include search directories",
        ));
    }
    let mut arguments = vec!["-nostdinc++".into()];
    for root in roots {
        arguments.extend(["-isystem".into(), root.display().to_string()]);
    }
    Ok(GccStdlibProjection {
        arguments,
        cpp_search,
        c_search,
        gnu_driver: true,
    })
}
