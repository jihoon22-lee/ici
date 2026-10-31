//! Read-only native type-checker intent. Configuration contents remain owned by each tool.
use crate::{ConfigError, Origin, parse::Parsed};
use std::{fs, path::Path};

/// Select only declared/native intent. None leaves the recommended default/advisory policy to callers.
/// Conflicting native choices anywhere inside the bounded workspace require an explicit choice.
pub fn discover_type_provider(
    component_root: &Path,
    workspace_root: &Path,
    explicit: Option<&str>,
) -> Result<Option<String>, ConfigError> {
    let origin = Origin {
        file: component_root.display().to_string(),
        line: 1,
        column: 1,
        key: "python.type_provider".into(),
        layer: "discovered".into(),
    };
    if let Some(explicit) = explicit {
        return if ["mypy", "ty"].contains(&explicit) {
            Ok(Some(explicit.into()))
        } else {
            Err(ConfigError::new("type_provider must be mypy or ty", origin))
        };
    }
    let workspace = workspace_root.canonicalize().map_err(|e| {
        ConfigError::new(
            format!("cannot resolve workspace for native config: {e}"),
            origin.clone(),
        )
    })?;
    let component = component_root.canonicalize().map_err(|e| {
        ConfigError::new(
            format!("cannot resolve component for native config: {e}"),
            origin.clone(),
        )
    })?;
    if !component.starts_with(&workspace) {
        return Err(ConfigError::new(
            "native configuration search must stay inside the workspace",
            origin,
        ));
    }
    let mut mypy = Vec::new();
    let mut ty = Vec::new();
    for directory in component.ancestors() {
        for marker in ["mypy.ini", ".mypy.ini"] {
            let path = directory.join(marker);
            if path.is_file() {
                mypy.push(path);
            }
        }
        let ty_path = directory.join("ty.toml");
        if ty_path.is_file() {
            ty.push(ty_path);
        }
        let pyproject = directory.join("pyproject.toml");
        if pyproject.is_file() {
            let native = Parsed::read(&pyproject, false, "discovered")?;
            if native.value.pointer("/tool/mypy").is_some() {
                mypy.push(pyproject.clone());
            }
            if native.value.pointer("/tool/ty").is_some() {
                ty.push(pyproject);
            }
        }
        let setup = directory.join("setup.cfg");
        match fs::read_to_string(&setup) {
            Ok(text) => {
                if ini_has_mypy(&text) {
                    mypy.push(setup);
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(ConfigError::new(
                    format!(
                        "cannot read native configuration {}: {error}",
                        setup.display()
                    ),
                    origin.clone(),
                ));
            }
        }
        if directory == workspace {
            break;
        }
    }
    match (mypy.is_empty(), ty.is_empty()) {
        (false, false) => Err(ConfigError::new(
            format!(
                "both native mypy and ty settings exist inside the workspace ({}; {}); select python.type_provider explicitly",
                mypy.iter()
                    .map(|p| p.display().to_string())
                    .collect::<Vec<_>>()
                    .join(", "),
                ty.iter()
                    .map(|p| p.display().to_string())
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
            origin,
        )),
        (false, true) => Ok(Some("mypy".into())),
        (true, false) => Ok(Some("ty".into())),
        _ => Ok(None),
    }
}

fn ini_has_mypy(text: &str) -> bool {
    text.lines().any(|line| {
        line.trim()
            .strip_prefix('[')
            .and_then(|line| line.split_once(']'))
            .is_some_and(|(name, _)| name == "mypy")
    })
}
