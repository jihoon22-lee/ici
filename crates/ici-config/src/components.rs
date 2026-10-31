use crate::resolve::{apply_component_local, dedup, merge, required_string, strings, text};
use crate::{discovery, parse::Parsed, paths, *};
use serde_json::{Value, json};
use std::{collections::BTreeMap, path::Path};

pub(crate) fn checks(
    value: Option<&Value>,
    parsed: &Parsed,
    prefix: &str,
) -> Result<BTreeMap<String, CheckSetting>, ConfigError> {
    let mut result = BTreeMap::new();
    if let Some(table) = value.and_then(Value::as_object) {
        for (id, settings) in table {
            if id.trim().is_empty() || id.len() > 128 {
                return Err(parsed.error(prefix, "check id must be a bounded nonempty string"));
            }
            let mut exemptions = BTreeMap::new();
            if let Some(entries) = settings.get("exemptions").and_then(Value::as_object) {
                for (component, entry) in entries {
                    let reason = required_string(
                        entry,
                        "reason",
                        parsed,
                        &format!("{prefix}.{id}.exemptions.{component}"),
                    )?;
                    exemptions.insert(component.clone(), reason);
                }
            }
            result.insert(
                id.clone(),
                CheckSetting {
                    enabled: settings
                        .get("enabled")
                        .and_then(Value::as_bool)
                        .unwrap_or(true),
                    required: settings
                        .get("required")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                    exemptions,
                    settings: settings.clone(),
                },
            );
        }
    }
    Ok(result)
}

fn component_checks(
    id: &str,
    body: &Value,
    root: &BTreeMap<String, CheckSetting>,
    parsed: &Parsed,
    prefix: &str,
) -> Result<BTreeMap<String, CheckSetting>, ConfigError> {
    let mut result = root.clone();
    for (check, adjustment) in checks(body.get("checks"), parsed, &format!("{prefix}.checks"))? {
        let inherited = result.entry(check.clone()).or_default();
        let lower = inherited.required
            && (!adjustment.enabled
                || (adjustment.settings.get("required").is_some() && !adjustment.required));
        if lower && !inherited.exemptions.contains_key(id) {
            return Err(parsed.error(&format!("{prefix}.checks.{check}"), "component cannot disable or weaken a root-required check without a reasoned root exemption"));
        }
        if adjustment.settings.get("enabled").is_some() {
            inherited.enabled = adjustment.enabled;
        }
        if adjustment.settings.get("required").is_some() {
            inherited.required = adjustment.required;
        }
        merge(&mut inherited.settings, &adjustment.settings);
    }
    Ok(result)
}

#[derive(Clone, Copy)]
pub(crate) struct ComponentContext<'a> {
    pub(crate) root: &'a Path,
    pub(crate) effective: &'a Value,
    pub(crate) parsed: &'a Parsed,
    pub(crate) family: SchemaFamily,
    pub(crate) root_checks: &'a BTreeMap<String, CheckSetting>,
    pub(crate) environment: &'a BTreeMap<String, String>,
}
pub(crate) fn component(
    context: ComponentContext<'_>,
    body: &Value,
    prefix: &str,
    limitations: &mut Vec<String>,
) -> Result<Component, ConfigError> {
    let ComponentContext {
        root,
        effective,
        parsed,
        family,
        root_checks,
        environment,
    } = context;
    let id = required_string(body, "id", parsed, prefix)?;
    if id.len() > 128 || id.chars().any(|c| c < ' ') {
        return Err(parsed.error(
            &format!("{prefix}.id"),
            "component id must be bounded printable text",
        ));
    }
    let raw_root = match text(body, "root") {
        Some(value) => value,
        None if family != SchemaFamily::NextV1 => ".".into(),
        None => return Err(parsed.error(&format!("{prefix}.root"), "component root is required")),
    };
    let base = parsed.path.parent().expect("declaration parent");
    let component_root = paths::anchor(
        &raw_root,
        base,
        root,
        environment,
        &parsed.origin(&format!("{prefix}.root")),
        false,
    )?;
    let mut excludes = if body.get("exclude").is_some() {
        strings(body, "exclude")
    } else {
        strings(effective, "exclude")
    };
    excludes.extend(strings(body, "extend_exclude"));
    dedup(&mut excludes);
    let scan = discovery::scan(root, &component_root, &excludes, &parsed.origin(prefix))?;
    limitations.extend(scan.limitations.clone());
    let mut languages = strings(body, "languages");
    if languages.is_empty() {
        if family == SchemaFamily::NextV1 {
            return Err(parsed.error(
                &format!("{prefix}.languages"),
                "component must declare languages",
            ));
        }
        languages = scan
            .files
            .iter()
            .filter_map(|f| discovery::language(f))
            .map(str::to_string)
            .collect();
        languages.sort();
        languages.dedup();
    }
    let sources = strings(body, "sources");
    let include = strings(body, "include");
    let mut selected_patterns = sources.clone();
    selected_patterns.extend(include.iter().cloned());
    let matcher = discovery::matcher(&selected_patterns, &parsed.origin(prefix))?;
    let source_dirs = strings(body, "source_dirs");
    for directory in &source_dirs {
        paths::check_glob(directory, &parsed.origin(prefix))?;
    }
    let base_path = root.join(&component_root);
    let files: Vec<_> = scan
        .files
        .iter()
        .filter(|file| {
            let within = root
                .join(file)
                .strip_prefix(&base_path)
                .expect("component file")
                .to_path_buf();
            (selected_patterns.is_empty() || matcher.is_match(&within))
                && (!body.get("source_dirs").is_some()
                    || source_dirs
                        .iter()
                        .any(|directory| directory == "." || within.starts_with(directory))
                    || (family == SchemaFamily::Legacy && legacy_include_header(&within)))
                && discovery::language(file)
                    .is_some_and(|language| languages.iter().any(|name| name == language))
        })
        .cloned()
        .collect();
    let mut settings = json!({});
    for key in ["python", "cpp", "engines"] {
        if let Some(value) = effective.get(key) {
            settings[key] = value.clone();
        }
    }
    merge(&mut settings, body);
    apply_component_local(&mut settings, effective);
    settings["native_configs"] = json!(scan.native_configs);
    let runtime_raw = settings
        .pointer("/python/executable")
        .and_then(Value::as_str);
    let runtime_key = format!("components.{id}.python.executable");
    let runtime_origin = effective
        .get("_local_origins")
        .and_then(|v| v.get(&runtime_key))
        .and_then(|v| serde_json::from_value::<Origin>(v.clone()).ok())
        .unwrap_or_else(|| parsed.origin(&format!("{prefix}.python.executable")));
    let runtime_base = Path::new(&runtime_origin.file)
        .parent()
        .filter(|p| p.is_absolute())
        .unwrap_or(base);
    let python_executable = runtime_raw
        .map(|raw| paths::executable(raw, runtime_base, root, environment, &runtime_origin))
        .transpose()?;
    let explicit_provider = settings
        .pointer("/python/type_provider")
        .and_then(Value::as_str);
    let python_type_provider = if languages.iter().any(|name| name == "python") {
        discover_type_provider(&base_path, root, explicit_provider)?
    } else {
        explicit_provider.map(str::to_string)
    };
    let test_paths = settings
        .get("python")
        .map(|p| strings(p, "test_paths"))
        .unwrap_or_default();
    for pattern in sources
        .iter()
        .chain(&include)
        .chain(&excludes)
        .chain(&test_paths)
        .chain(strings(body, "vendor").iter())
    {
        paths::check_glob(pattern, &parsed.origin(prefix))?;
    }
    let external = strings(body, "external")
        .iter()
        .map(|raw| {
            paths::anchor(
                raw,
                base,
                root,
                environment,
                &parsed.origin(&format!("{prefix}.external")),
                true,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    if files.is_empty() {
        limitations.push(format!(
            "component {id} has no selected source files; this is not evidence of an analysis PASS"
        ));
    }
    let declared_in = paths::portable(
        parsed.path.strip_prefix(root).unwrap_or(&parsed.path),
        &parsed.origin(prefix),
    )?;
    Ok(Component {
        id: id.clone(),
        root: component_root,
        languages,
        files,
        source_dirs,
        sources,
        include,
        exclude: excludes,
        vendor: strings(body, "vendor"),
        external,
        required: true,
        build: text(body, "build"),
        needs: strings(body, "needs"),
        python_executable,
        python_type_provider,
        test_paths,
        checks: component_checks(&id, body, root_checks, parsed, prefix)?,
        integrations: body
            .get("integrations")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default(),
        settings,
        declared_in,
        origin: parsed.origin(prefix),
    })
}

pub(crate) fn automatic_components(
    root: &Path,
    effective: &Value,
    parsed: &Parsed,
    checks: &BTreeMap<String, CheckSetting>,
    environment: &BTreeMap<String, String>,
    limitations: &mut Vec<String>,
) -> Result<Vec<Component>, ConfigError> {
    let scan = discovery::scan(
        root,
        ".",
        &strings(effective, "exclude"),
        &Origin::defaults("components"),
    )?;
    limitations.extend(scan.limitations.clone());
    let mut result = vec![];
    for (directory, owned_files) in discovery::partition(&scan) {
        let id = if directory == "." {
            "project".into()
        } else {
            directory.clone()
        };
        let mut body = json!({"id":id,"root":directory});
        if effective.get("languages").is_some() {
            body["languages"] = effective["languages"].clone();
        }
        let mut resolved = component(
            ComponentContext {
                root,
                effective,
                parsed,
                family: SchemaFamily::Automatic,
                root_checks: checks,
                environment,
            },
            &body,
            "components",
            limitations,
        )?;
        let source_dirs = strings(effective, "source_dirs");
        for directory in &source_dirs {
            paths::check_glob(directory, &parsed.origin("source_dirs"))?;
        }
        resolved.files.retain(|file| {
            owned_files.contains(file)
                && (effective.get("source_dirs").is_none()
                    || source_dirs.iter().any(|directory| {
                        directory == "." || Path::new(file).starts_with(directory)
                    }))
        });
        if resolved.files.is_empty() {
            continue;
        }
        if body.get("languages").is_none() {
            resolved.languages = resolved
                .files
                .iter()
                .filter_map(|f| discovery::language(f))
                .map(str::to_string)
                .collect();
            resolved.languages.sort();
            resolved.languages.dedup();
        }
        resolved.origin = Origin {
            file: "<automatic discovery>".into(),
            layer: "discovered".into(),
            ..Origin::defaults(format!("components.{}", resolved.id))
        };
        result.push(resolved);
    }
    if result.is_empty() {
        return Err(parsed.error("components", "no supported source files discovered; declare component scope or add supported sources"));
    }
    Ok(result)
}

/// Frozen `get_all_cpp_headers` always adds the project's top-level `include/` headers to the
/// legacy C/C++ header scope (cycle, dup, dead and lint inputs), whatever `source_dirs` says.
fn legacy_include_header(within: &Path) -> bool {
    within.starts_with("include")
        && within
            .extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| matches!(extension, "h" | "hh" | "hpp" | "hxx"))
}

pub(crate) fn legacy_components(
    root: &Path,
    effective: &Value,
    parsed: &Parsed,
    excludes: &[String],
    checks: &BTreeMap<String, CheckSetting>,
    environment: &BTreeMap<String, String>,
    limitations: &mut Vec<String>,
) -> Result<Vec<Component>, ConfigError> {
    let source_dirs = effective
        .get("project")
        .map(|p| strings(p, "source_dirs"))
        .unwrap_or_default();
    let mut body = json!({"id":"project","root":".","source_dirs":source_dirs,"exclude":excludes});
    if let Some(kind) = effective
        .pointer("/project/type")
        .or_else(|| effective.get("type"))
        .and_then(Value::as_str)
    {
        body["languages"] = match kind {
            "python" => json!(["python"]),
            "cpp" => json!(["cpp"]),
            _ => json!(["python", "cpp"]),
        };
    }
    if let Some(runtime) = effective.pointer("/engines/test/python") {
        body["python"] = json!({"executable":runtime});
    }
    let context = ComponentContext {
        root,
        effective,
        parsed,
        family: SchemaFamily::Legacy,
        root_checks: checks,
        environment,
    };
    let mut value = component(context, &body, "project", limitations)?;
    if value.languages.is_empty() {
        // Frozen `detect_project_type` falls back to "python" when no source is recognized
        // (a documentation-only tree), so the historical engines still apply.
        body["languages"] = json!(["python"]);
        value = component(context, &body, "project", limitations)?;
    }
    value.settings["legacy"] = effective.clone();
    limitations.push("legacy source_dirs and engine options are preserved; use migrate preview before adopting automatic v2 scope".into());
    Ok(vec![value])
}
