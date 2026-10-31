//! Read-only native configuration evidence. Executable config is never evaluated here.
use crate::{ProviderError, plan::ProviderKind};
use ici_domain::sha256;
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct ConfigEvidence {
    pub files: BTreeMap<PathBuf, String>,
    pub complete: bool,
    pub limitations: Vec<String>,
}
impl ConfigEvidence {
    pub fn digest(&self) -> String {
        sha256(
            ici_domain::canonical_json(&self.files)
                .expect("configuration evidence is serializable"),
        )
    }
    pub fn input_refs(&self) -> Vec<String> {
        self.files
            .keys()
            .map(|path| path.to_string_lossy().into_owned())
            .collect()
    }
}
pub fn discover_native_configuration(
    kind: ProviderKind,
    root: &Path,
    cwd: &Path,
    targets: &[String],
) -> Result<ConfigEvidence, ProviderError> {
    let names: &[&str] = match kind {
        ProviderKind::RuffLint | ProviderKind::RuffFormat => &[
            "ruff.toml",
            ".ruff.toml",
            "pyproject.toml",
            ".gitignore",
            ".ignore",
            ".git/info/exclude",
        ],
        ProviderKind::Ty => &["ty.toml", "pyproject.toml"],
        ProviderKind::Mypy => &["mypy.ini", ".mypy.ini", "setup.cfg", "pyproject.toml"],
        ProviderKind::Clippy => &[
            "Cargo.toml",
            "Cargo.lock",
            ".cargo/config.toml",
            ".cargo/config",
            "rust-toolchain.toml",
            "rust-toolchain",
        ],
        ProviderKind::Eslint => &[
            "eslint.config.js",
            "eslint.config.mjs",
            "eslint.config.cjs",
            "eslint.config.ts",
            ".eslintrc.json",
            ".eslintrc.js",
            ".eslintrc.cjs",
            ".eslintrc.yaml",
            ".eslintrc.yml",
            ".eslintignore",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "bun.lock",
            "bun.lockb",
        ],
        ProviderKind::Tsc => &[
            "tsconfig.json",
            "jsconfig.json",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "bun.lock",
            "bun.lockb",
        ],
        ProviderKind::Compiler | ProviderKind::ClangTidy | ProviderKind::Clazy => {
            &["compile_commands.json", ".clang-tidy", ".clangd"]
        }
    };
    let mut directories = BTreeSet::from([root.to_path_buf()]);
    for start in std::iter::once(cwd.to_path_buf()).chain(
        targets
            .iter()
            .filter_map(|target| root.join(target).parent().map(Path::to_path_buf)),
    ) {
        let mut directory = start.as_path();
        while directory.starts_with(root) {
            directories.insert(directory.to_path_buf());
            let Some(parent) = directory.parent() else {
                break;
            };
            directory = parent;
        }
    }
    let mut evidence = ConfigEvidence::default();
    let mut visited = BTreeSet::new();
    for directory in directories {
        for name in names {
            let path = directory.join(name);
            if path.exists() {
                capture(&path, kind, &mut evidence, &mut visited, 0)?;
            }
        }
    }
    if matches!(kind, ProviderKind::RuffLint | ProviderKind::RuffFormat) {
        evidence.complete = ruff_local_policy(root, kind, &evidence.files)?;
    }
    if !evidence.complete {
        evidence.limitations.push("native imports, user settings, plugins, generated inputs and tool dependency closure are not fully enumerated".into());
    } else {
        evidence.limitations.push("closed native configuration still requires a fresh explicit file inventory before reuse".into());
    }
    Ok(evidence)
}
fn capture(
    path: &Path,
    kind: ProviderKind,
    evidence: &mut ConfigEvidence,
    visited: &mut BTreeSet<PathBuf>,
    depth: usize,
) -> Result<(), ProviderError> {
    if depth > 32 {
        return Err(ProviderError(
            "native configuration recursion exceeds 32 levels".into(),
        ));
    }
    let path = path
        .canonicalize()
        .map_err(|e| ProviderError(format!("native config {}: {e}", path.display())))?;
    if !visited.insert(path.clone()) {
        return Ok(());
    }
    let bytes = ici_execution::storage::read_bounded_regular(&path, 8 * 1024 * 1024)
        .map_err(|error| ProviderError(format!("native input {}: {error}", path.display())))?;
    evidence.files.insert(path.clone(), sha256(&bytes));
    let Ok(source) = std::str::from_utf8(&bytes) else {
        evidence
            .limitations
            .push(format!("binary native input: {}", path.display()));
        return Ok(());
    };
    let parent = path.parent().expect("canonical file has parent");
    if matches!(kind, ProviderKind::RuffLint | ProviderKind::RuffFormat)
        && (path
            .extension()
            .is_some_and(|extension| extension == "toml")
            || depth > 0)
    {
        let document = source
            .parse::<toml_edit::DocumentMut>()
            .map_err(|e| ProviderError(format!("native Ruff TOML {}: {e}", path.display())))?;
        let table = if path.file_name().and_then(|n| n.to_str()) == Some("pyproject.toml") {
            document.get("tool").and_then(|tool| tool.get("ruff"))
        } else {
            Some(document.as_item())
        };
        if let Some(extend) = table
            .and_then(|table| table.get("extend"))
            .and_then(toml_edit::Item::as_str)
        {
            let next = parent.join(extend);
            capture(&next, kind, evidence, visited, depth + 1)?;
        }
    }
    if kind == ProviderKind::Tsc
        && (depth > 0
            || matches!(
                path.file_name().and_then(|n| n.to_str()),
                Some("tsconfig.json" | "jsconfig.json")
            ))
    {
        if let Ok(document) = serde_json::from_str::<serde_json::Value>(source) {
            let extends: Vec<_> = match &document["extends"] {
                serde_json::Value::String(value) => vec![value.as_str()],
                serde_json::Value::Array(values) => {
                    values.iter().filter_map(|value| value.as_str()).collect()
                }
                _ => vec![],
            };
            for extend in extends {
                if extend.starts_with('.') || Path::new(extend).is_absolute() {
                    let mut next = parent.join(extend);
                    if !next.exists() {
                        next.set_extension("json");
                    }
                    if next.exists() {
                        capture(&next, kind, evidence, visited, depth + 1)?;
                    } else {
                        evidence
                            .limitations
                            .push(format!("unresolved TypeScript extends: {extend}"));
                    }
                } else {
                    evidence.limitations.push(format!(
                        "package TypeScript extends closure unresolved: {extend}"
                    ));
                }
            }
        } else {
            evidence.limitations.push(format!(
                "JSONC/config syntax is delegated to TypeScript: {}",
                path.display()
            ));
        }
    }
    if kind == ProviderKind::Eslint
        && matches!(
            path.extension().and_then(|s| s.to_str()),
            Some("js" | "mjs" | "cjs" | "ts")
        )
    {
        let imports =
            regex::Regex::new(r#"(?:from\s*|import\s*|require\s*\(\s*)["'](\.[^"']+)["']"#)
                .expect("constant regex");
        for capture_match in imports.captures_iter(source) {
            let next = parent.join(&capture_match[1]);
            let mut candidates = vec![next.clone()];
            if next.extension().is_none() {
                candidates.extend(
                    ["js", "mjs", "cjs", "json"]
                        .iter()
                        .map(|extension| next.with_extension(extension)),
                );
            }
            let mut found = false;
            for candidate in candidates {
                if candidate.is_file() {
                    capture(&candidate, kind, evidence, visited, depth + 1)?;
                    found = true;
                }
            }
            if !found {
                evidence.limitations.push(format!(
                    "unresolved relative ESLint import: {}",
                    &capture_match[1]
                ));
            }
        }
    }
    Ok(())
}

/// Initial cache qualification is intentionally narrow. Other Ruff rule sets still
/// run normally; they need additional dependency-closure evidence before reuse.
fn ruff_local_policy(
    root: &Path,
    kind: ProviderKind,
    files: &BTreeMap<PathBuf, String>,
) -> Result<bool, ProviderError> {
    let mut anchored = false;
    for name in ["ruff.toml", ".ruff.toml", "pyproject.toml"] {
        let path = root.join(name);
        if path.is_file() && ruff_rules(&path, &mut BTreeSet::new())?.is_some() {
            anchored = true;
            break;
        }
    }
    if !anchored {
        return Ok(false);
    }
    if kind == ProviderKind::RuffFormat {
        return Ok(true);
    }
    for path in files.keys() {
        if !matches!(
            path.file_name().and_then(|value| value.to_str()),
            Some("ruff.toml" | ".ruff.toml" | "pyproject.toml")
        ) {
            continue;
        }
        if let Some(rules) = ruff_rules(path, &mut BTreeSet::new())? {
            let Some(selected) = rules.selected else {
                return Ok(false);
            };
            if selected.is_empty()
                || selected
                    .iter()
                    .chain(&rules.extended)
                    .any(|rule| !["F821", "F841", "E501"].contains(&rule.as_str()))
            {
                return Ok(false);
            }
        }
    }
    Ok(true)
}
#[derive(Default)]
struct RuffRules {
    selected: Option<Vec<String>>,
    extended: Vec<String>,
}
fn ruff_rules(
    path: &Path,
    stack: &mut BTreeSet<PathBuf>,
) -> Result<Option<RuffRules>, ProviderError> {
    let path = path
        .canonicalize()
        .map_err(|error| ProviderError(error.to_string()))?;
    if stack.len() >= 32 || !stack.insert(path.clone()) {
        return Err(ProviderError(
            "cyclic/deep Ruff extends configuration".into(),
        ));
    }
    let bytes = ici_execution::storage::read_bounded_regular(&path, 8 * 1024 * 1024)
        .map_err(|e| ProviderError(e.to_string()))?;
    let source = std::str::from_utf8(&bytes).map_err(|e| ProviderError(e.to_string()))?;
    let document = source
        .parse::<toml_edit::DocumentMut>()
        .map_err(|e| ProviderError(e.to_string()))?;
    let table = if path.file_name().and_then(|name| name.to_str()) == Some("pyproject.toml") {
        document.get("tool").and_then(|table| table.get("ruff"))
    } else {
        Some(document.as_item())
    };
    let Some(table) = table else {
        stack.remove(&path);
        return Ok(None);
    };
    let mut rules = if let Some(extend) = table.get("extend").and_then(toml_edit::Item::as_str) {
        ruff_rules(
            &path.parent().expect("canonical file parent").join(extend),
            stack,
        )?
        .unwrap_or_default()
    } else {
        RuffRules::default()
    };
    if let Some(lint) = table.get("lint") {
        if let Some(select) = lint.get("select").and_then(toml_edit::Item::as_array) {
            rules.selected = Some(
                select
                    .iter()
                    .map(|rule| {
                        rule.as_str()
                            .map(str::to_string)
                            .ok_or_else(|| ProviderError("invalid Ruff select".into()))
                    })
                    .collect::<Result<_, _>>()?,
            );
        }
        if let Some(extend) = lint
            .get("extend-select")
            .and_then(toml_edit::Item::as_array)
        {
            for rule in extend {
                rules.extended.push(
                    rule.as_str()
                        .ok_or_else(|| ProviderError("invalid Ruff extend-select".into()))?
                        .into(),
                );
            }
        }
    }
    stack.remove(&path);
    Ok(Some(rules))
}
