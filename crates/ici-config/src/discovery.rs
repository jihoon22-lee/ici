use crate::{ConfigError, Origin, parse::Parsed, paths};
use globset::{GlobBuilder, GlobSet, GlobSetBuilder};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};

pub const DEFAULT_EXCLUDES: &[&str] = &[
    "**/.git/**",
    "**/.hg/**",
    "**/.svn/**",
    "**/.venv/**",
    "**/venv/**",
    "**/node_modules/**",
    "**/__pycache__/**",
    "**/build/**",
    "**/dist/**",
    "**/target/**",
    "**/.ici/**",
    "**/.ici-native/**",
];
const VCS: &[&str] = &[".git", ".hg", ".svn"];
const PACKAGE_MARKERS: &[&str] = &[
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Cargo.toml",
    "package.json",
    "CMakeLists.txt",
    "Makefile",
];
const MAX_ENTRIES: usize = 100_000;

pub(crate) struct Selected {
    pub root: PathBuf,
    pub document: Option<Parsed>,
}

pub(crate) fn select(cwd: &Path, explicit: Option<&Path>) -> Result<Selected, ConfigError> {
    let origin = Origin {
        file: cwd.display().to_string(),
        line: 1,
        column: 1,
        key: "workspace".into(),
        layer: "discovered".into(),
    };
    let cwd = cwd.canonicalize().map_err(|e| {
        ConfigError::new(
            format!("cannot read working directory: {e}"),
            origin.clone(),
        )
    })?;
    if !cwd.is_dir() {
        return Err(ConfigError::new(
            "working directory must be a directory",
            origin,
        ));
    }
    if let Some(explicit) = explicit {
        let path = if explicit.is_absolute() {
            explicit.to_path_buf()
        } else {
            cwd.join(explicit)
        };
        let path = path.canonicalize().map_err(|e| {
            ConfigError::new(
                format!("explicit configuration does not exist: {e}"),
                origin,
            )
        })?;
        let document = Parsed::read(
            &path,
            path.file_name().is_some_and(|n| n == "pyproject.toml"),
            "root",
        )?;
        return Ok(Selected {
            root: path.parent().expect("config has parent").into(),
            document: Some(document),
        });
    }
    let mut fallback = cwd.clone();
    let mut unregistered: Option<PathBuf> = None;
    for directory in cwd.ancestors() {
        let ici = directory.join("ici.toml");
        if ici.exists() {
            let parsed = Parsed::read(&ici, false, "root")?;
            if parsed.value.get("component").is_some() && parsed.value.get("workspace").is_none() {
                unregistered = Some(ici);
            } else {
                return Ok(Selected {
                    root: directory.into(),
                    document: Some(parsed),
                });
            }
        } else {
            let pyproject = directory.join("pyproject.toml");
            if pyproject.is_file() {
                let raw = Parsed::read(&pyproject, false, "root")?;
                if raw.value.get("tool").and_then(|v| v.get("ici")).is_some() {
                    let parsed = Parsed::parse(&pyproject, raw.text, true, "root")?;
                    return Ok(Selected {
                        root: directory.into(),
                        document: Some(parsed),
                    });
                }
            }
        }
        if VCS.iter().any(|marker| directory.join(marker).exists()) {
            fallback = directory.into();
            break;
        }
        if PACKAGE_MARKERS
            .iter()
            .any(|marker| directory.join(marker).is_file())
        {
            fallback = directory.into();
        }
    }
    if let Some(path) = unregistered {
        return Err(ConfigError::new(
            "unregistered component configuration is not a workspace; pass --config for a standalone run",
            Origin {
                file: path.display().to_string(),
                ..origin
            },
        ));
    }
    Ok(Selected {
        root: fallback,
        document: None,
    })
}

pub(crate) struct Scan {
    pub files: Vec<String>,
    pub packages: BTreeSet<String>,
    pub native_configs: BTreeSet<String>,
    pub limitations: Vec<String>,
}

pub(crate) fn language(path: &str) -> Option<&'static str> {
    match Path::new(path).extension().and_then(|s| s.to_str())? {
        "py" | "pyi" => Some("python"),
        "c" | "cc" | "cpp" | "cxx" | "h" | "hh" | "hpp" | "hxx" | "ixx" => Some("cpp"),
        "rs" => Some("rust"),
        "js" | "jsx" | "mjs" | "cjs" => Some("javascript"),
        "ts" | "tsx" | "mts" | "cts" => Some("typescript"),
        _ => None,
    }
}

pub(crate) fn matcher(patterns: &[String], origin: &Origin) -> Result<GlobSet, ConfigError> {
    let mut builder = GlobSetBuilder::new();
    for pattern in patterns {
        paths::check_glob(pattern, origin)?;
        builder.add(
            GlobBuilder::new(pattern)
                .literal_separator(true)
                .backslash_escape(false)
                .build()
                .map_err(|e| ConfigError::new(format!("invalid glob: {e}"), origin.clone()))?,
        );
    }
    builder
        .build()
        .map_err(|e| ConfigError::new(format!("invalid glob set: {e}"), origin.clone()))
}

fn excluded(path: &str, matcher: &GlobSet) -> bool {
    matcher.is_match(path)
        || Path::new(path)
            .ancestors()
            .filter_map(|p| p.to_str())
            .any(|parent| {
                matcher.is_match(parent) || matcher.is_match(format!("{parent}/__ici_directory__"))
            })
}

pub(crate) fn scan(
    root: &Path,
    component_root: &str,
    excludes: &[String],
    origin: &Origin,
) -> Result<Scan, ConfigError> {
    let base = root.join(component_root);
    if !base.is_dir() {
        return Err(ConfigError::new(
            format!("component directory does not exist: {component_root}"),
            origin.clone(),
        ));
    }
    let matcher = matcher(excludes, origin)?;
    let mut result = Scan {
        files: vec![],
        packages: BTreeSet::new(),
        native_configs: BTreeSet::new(),
        limitations: vec![],
    };
    let mut pending = vec![(base.clone(), 0usize)];
    let mut entries = 0usize;
    while let Some((directory, depth)) = pending.pop() {
        if depth > 64 {
            return Err(ConfigError::new(
                "source discovery exceeds 64 directory levels",
                origin.clone(),
            ));
        }
        let mut children: Vec<_> = fs::read_dir(&directory)
            .map_err(|e| {
                ConfigError::new(
                    format!("cannot read source directory {}: {e}", directory.display()),
                    origin.clone(),
                )
            })?
            .collect::<Result<_, _>>()
            .map_err(|e| {
                ConfigError::new(
                    format!("cannot enumerate source directory: {e}"),
                    origin.clone(),
                )
            })?;
        children.sort_by_key(|entry| entry.file_name());
        for child in children {
            entries += 1;
            if entries > MAX_ENTRIES {
                return Err(ConfigError::new(
                    "source discovery exceeds 100000 entries; narrow the declared scope",
                    origin.clone(),
                ));
            }
            let path = child.path();
            let name = child.file_name();
            if VCS.iter().any(|marker| name == *marker) {
                continue;
            }
            let relative =
                paths::portable(path.strip_prefix(&base).expect("inside scan base"), origin)?;
            if excluded(&relative, &matcher) {
                continue;
            }
            let file_type = child.file_type().map_err(|e| {
                ConfigError::new(format!("cannot inspect source: {e}"), origin.clone())
            })?;
            if file_type.is_symlink() {
                result.limitations.push(format!(
                    "symlink not traversed during source discovery: {}",
                    path.strip_prefix(root).unwrap_or(&path).display()
                ));
                continue;
            }
            if file_type.is_dir() {
                if VCS.iter().any(|marker| path.join(marker).exists())
                    || is_nested_workspace(&path)?
                {
                    result.limitations.push(format!(
                        "independent nested workspace excluded: {}",
                        path.strip_prefix(root).unwrap_or(&path).display()
                    ));
                    continue;
                }
                pending.push((path, depth + 1));
            } else if file_type.is_file() {
                let workspace_relative =
                    paths::portable(path.strip_prefix(root).expect("workspace member"), origin)?;
                if language(&workspace_relative).is_some() {
                    result.files.push(workspace_relative.clone());
                }
                if PACKAGE_MARKERS.iter().any(|marker| name == *marker) {
                    result.packages.insert(paths::portable(
                        directory.strip_prefix(root).expect("workspace directory"),
                        origin,
                    )?);
                }
                if name == "ruff.toml"
                    || name == ".ruff.toml"
                    || name == "mypy.ini"
                    || name == "ty.toml"
                    || name == "tsconfig.json"
                    || name == "pyproject.toml"
                    || name == "setup.cfg"
                    || name
                        .to_str()
                        .is_some_and(|n| n.starts_with("eslint.config."))
                {
                    result.native_configs.insert(workspace_relative);
                }
            }
        }
    }
    result.files.sort();
    result.files.dedup();
    result.limitations.sort();
    Ok(result)
}

fn is_nested_workspace(directory: &Path) -> Result<bool, ConfigError> {
    let ici = directory.join("ici.toml");
    if ici.is_file() {
        let parsed = Parsed::read(&ici, false, "root")?;
        return Ok(parsed.value.get("workspace").is_some()
            || parsed.value.get("schema_version").and_then(|v| v.as_i64()) == Some(2));
    }
    let pyproject = directory.join("pyproject.toml");
    if pyproject.is_file() {
        let parsed = Parsed::read(&pyproject, false, "root")?;
        return Ok(parsed
            .value
            .get("tool")
            .and_then(|v| v.get("ici"))
            .is_some());
    }
    Ok(false)
}

pub(crate) fn partition(scan: &Scan) -> BTreeMap<String, Vec<String>> {
    let mut result: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for file in &scan.files {
        let owner = scan
            .packages
            .iter()
            .filter(|root| root.as_str() != "." && Path::new(file).starts_with(root))
            .max_by_key(|root| Path::new(root).components().count())
            .map_or(".", String::as_str);
        result.entry(owner.into()).or_default().push(file.clone());
    }
    result
}
