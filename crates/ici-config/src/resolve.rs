use crate::{
    discovery::{self, DEFAULT_EXCLUDES},
    parse::Parsed,
    paths, schema, *,
};
use ici_domain::{Profile, ScopeKind};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

pub fn resolve(
    cwd: &Path,
    explicit: Option<&Path>,
    local: Option<&Path>,
) -> Result<ResolvedProject, ConfigError> {
    resolve_with_environment(cwd, explicit, local, &std::env::vars().collect())
}

/// Deterministic environment seam; resolution never executes or installs tools.
pub fn resolve_with_environment(
    cwd: &Path,
    explicit: Option<&Path>,
    local: Option<&Path>,
    environment: &BTreeMap<String, String>,
) -> Result<ResolvedProject, ConfigError> {
    let env_config = environment
        .get("ICI_CONFIG")
        .filter(|s| !s.is_empty())
        .map(PathBuf::from);
    let selected = discovery::select(cwd, explicit.or(env_config.as_deref()))?;
    let root = selected.root;
    let config_source = selected.document.as_ref().map(|p| p.path.clone());
    let parsed = match selected.document {
        Some(parsed) => parsed,
        None => Parsed::parse(
            &root.join("<automatic>"),
            "schema_version = 2\n".into(),
            false,
            "defaults",
        )?,
    };
    let family = if config_source.is_none() {
        SchemaFamily::Automatic
    } else {
        schema::family(&parsed)?
    };
    schema::validate(&parsed, family)?;
    let scope_kind = if parsed.value.get("component").is_some() {
        ScopeKind::Standalone
    } else {
        ScopeKind::Full
    };
    let mut effective = defaults(family);
    merge(&mut effective, &parsed.value);
    let mut origins = BTreeMap::new();
    default_origins(&defaults(family), "", &mut origins);
    origins.extend(parsed.origins.clone());
    let mut limitations = vec![];
    let mut configuration_files = config_source.iter().cloned().collect::<Vec<_>>();
    if explicit.is_none() && env_config.is_none() {
        preferences(
            &root,
            environment,
            &mut effective,
            &mut origins,
            &mut configuration_files,
            &mut limitations,
        )?;
    }
    if let Some(local) = local {
        let path = if local.is_absolute() {
            local.to_path_buf()
        } else {
            cwd.join(local)
        };
        let overlay = Parsed::read(&path, false, "local")?;
        apply_overlay(&overlay, &mut effective, &mut origins, false)?;
        configuration_files.push(path);
    }
    schema::relationships(&parsed, &effective, "")?;
    validate_legacy_paths(&root, &effective, &parsed, environment)?;
    // Metadata aliases are explicit precedence, independent of table insertion order.
    let (profile_value, profile_key) = if parsed.value.get("profile").is_some() {
        (&parsed.value["profile"], "profile")
    } else if parsed.value.pointer("/workspace/profile").is_some() {
        (&parsed.value["workspace"]["profile"], "workspace.profile")
    } else if parsed.value.pointer("/ici/profile").is_some() {
        (&parsed.value["ici"]["profile"], "ici.profile")
    } else {
        (&effective["profile"], "profile")
    };
    let profile: Profile = serde_json::from_value(profile_value.clone())
        .map_err(|_| parsed.error(profile_key, "profile must be fast, standard or deep"))?;
    effective["profile"] = serde_json::to_value(profile).expect("profile serialization");
    if parsed.origins.contains_key(profile_key) {
        origins.insert("profile".into(), parsed.origin(profile_key));
    }
    let workspace_name = parsed
        .value
        .pointer("/workspace/name")
        .or_else(|| parsed.value.pointer("/project/name"))
        .or_else(|| parsed.value.get("name"))
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| {
            root.file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned()
        });
    let mut excludes = strings(&effective, "exclude");
    excludes.extend(strings(&effective, "extend_exclude"));
    dedup(&mut excludes);
    for pattern in &excludes {
        paths::check_glob(pattern, &parsed.origin("exclude"))?;
    }
    effective["exclude"] = json!(excludes);
    let checks = checks(effective.get("checks"), &parsed, "checks")?;
    let builds = builds(&effective, &root, &parsed, environment, &origins)?;
    let tools = tools(&effective, &root, &parsed, environment, &origins)?;
    let suppressions = suppressions(&effective, &parsed)?;
    let publish = publish(&effective);
    let mut components = if family == SchemaFamily::Legacy {
        legacy_components(
            &root,
            &effective,
            &parsed,
            &excludes,
            &checks,
            environment,
            &mut limitations,
        )?
    } else if scope_kind == ScopeKind::Standalone {
        let mut body = parsed.value["component"].clone();
        add_language_sections(&mut body, &parsed.value);
        if body.get("id").is_none() {
            body["id"] = json!(root.file_name().unwrap_or_default().to_string_lossy());
        }
        vec![component(
            ComponentContext {
                root: &root,
                effective: &effective,
                parsed: &parsed,
                family,
                root_checks: &checks,
                environment,
            },
            &body,
            "component",
            &mut limitations,
        )?]
    } else if let Some(declared) = parsed.value.get("components") {
        let entries = declared.as_array().expect("schema validated components");
        if entries.is_empty() {
            return Err(parsed.error(
                "components",
                "explicitly empty components select no inspection; omit components for discovery",
            ));
        }
        let mut result = vec![];
        for (index, entry) in entries.iter().enumerate() {
            let prefix = format!("components[{index}]");
            if let Some(config) = entry.get("config").and_then(Value::as_str) {
                let id = required_string(entry, "id", &parsed, &prefix)?;
                let relative = paths::anchor(
                    config,
                    parsed.path.parent().expect("config parent"),
                    &root,
                    environment,
                    &parsed.origin(&format!("{prefix}.config")),
                    false,
                )?;
                let child_path = root.join(relative);
                let child = Parsed::read(&child_path, false, "component")?;
                let child_family = schema::family(&child)?;
                schema::validate(&child, child_family)?;
                if child.value.get("component").is_none() || child.value.get("workspace").is_some()
                {
                    return Err(child.error(
                        "component",
                        "referenced file must declare a component, not a nested workspace",
                    ));
                }
                let mut body = child.value["component"].clone();
                body["id"] = json!(id);
                add_language_sections(&mut body, &child.value);
                let value = component(
                    ComponentContext {
                        root: &root,
                        effective: &effective,
                        parsed: &child,
                        family: child_family,
                        root_checks: &checks,
                        environment,
                    },
                    &body,
                    "component",
                    &mut limitations,
                )?;
                for (key, origin) in &child.origins {
                    origins.insert(format!("components.{id}.{key}"), origin.clone());
                }
                configuration_files.push(child_path);
                result.push(value);
            } else {
                let mut entry = entry.clone();
                apply_component_local(&mut entry, &effective);
                result.push(component(
                    ComponentContext {
                        root: &root,
                        effective: &effective,
                        parsed: &parsed,
                        family,
                        root_checks: &checks,
                        environment,
                    },
                    &entry,
                    &prefix,
                    &mut limitations,
                )?);
            }
        }
        result
    } else {
        if family == SchemaFamily::NextV1 {
            return Err(parsed.error("components", "schema_version=1 requires declared components; migrate to schema_version=2 for discovery"));
        }
        automatic_components(
            &root,
            &effective,
            &parsed,
            &checks,
            environment,
            &mut limitations,
        )?
    };
    let ids: BTreeSet<_> = components.iter().map(|c| c.id.clone()).collect();
    if ids.len() != components.len() {
        return Err(parsed.error("components", "duplicate component id"));
    }
    if let Some(local) = effective
        .get("_local_components")
        .and_then(Value::as_object)
    {
        for id in local.keys() {
            if !ids.contains(id) {
                let key = format!("components.{id}.python.executable");
                return Err(ConfigError::new(
                    "local overlay names an undeclared component",
                    origins
                        .get(&key)
                        .cloned()
                        .unwrap_or_else(|| parsed.origin(&key)),
                ));
            }
        }
    }
    let build_ids: BTreeSet<_> = builds.iter().map(|b| b.id.as_str()).collect();
    for value in &components {
        if value
            .build
            .as_ref()
            .is_some_and(|b| !build_ids.contains(b.as_str()))
        {
            return Err(ConfigError::new(
                "component references an undeclared build",
                value.origin.clone(),
            ));
        }
        for need in &value.needs {
            if need == &value.id || (!ids.contains(need) && !build_ids.contains(need.as_str())) {
                return Err(ConfigError::new(
                    format!("invalid component dependency: {need}"),
                    value.origin.clone(),
                ));
            }
        }
    }
    check_component_cycles(&components, &parsed)?;
    components.sort_by(|a, b| a.id.cmp(&b.id));
    for value in &components {
        origins
            .entry(format!("components.{}", value.id))
            .or_insert_with(|| value.origin.clone());
    }
    if environment.contains_key("ICI_PYTHON") {
        limitations.push("ICI_PYTHON selects the historical pyz interpreter and is not a Rust runtime setting; declare project python.executable when needed".into());
    }
    if root.join("dev.toml").is_file() {
        limitations.push("legacy dev.toml is not implicitly merged; migrate its intended policy into project config and machine paths into --local-config".into());
    }
    limitations.sort();
    limitations.dedup();
    configuration_files.sort();
    configuration_files.dedup();
    Ok(ResolvedProject {
        root,
        config_source,
        schema_family: family,
        profile,
        scope_kind,
        workspace_name,
        components,
        builds,
        tools,
        checks,
        suppressions,
        publish,
        effective,
        origins,
        configuration_files,
        limitations,
    })
}

pub fn merge(base: &mut Value, override_value: &Value) {
    if let (Some(current), Some(overrides)) = (base.as_object_mut(), override_value.as_object()) {
        for (key, value) in overrides {
            if let Some(existing) = current.get_mut(key) {
                merge(existing, value);
            } else {
                current.insert(key.clone(), value.clone());
            }
        }
    } else {
        *base = override_value.clone();
    }
}

fn defaults(family: SchemaFamily) -> Value {
    let mut value: Value =
        serde_json::from_str(include_str!("legacy-defaults.json")).expect("frozen defaults JSON");
    value["ici"]["version"] = json!(env!("CARGO_PKG_VERSION"));
    value["profile"] = json!("standard");
    value["exclude"] = json!(DEFAULT_EXCLUDES);
    value["checks"] = json!({});
    value["tools"] = json!({});
    value["builds"] = json!({});
    value["suppressions"] = json!([]);
    if family != SchemaFamily::Legacy {
        value["schema_version"] = json!(2);
    }
    value
}

pub(crate) fn strings(value: &Value, key: &str) -> Vec<String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}
pub(crate) fn text(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(Value::as_str).map(str::to_string)
}
fn dedup(values: &mut Vec<String>) {
    let mut seen = BTreeSet::new();
    values.retain(|item| seen.insert(item.clone()));
}
fn required_string(
    value: &Value,
    key: &str,
    parsed: &Parsed,
    prefix: &str,
) -> Result<String, ConfigError> {
    text(value, key)
        .filter(|s| !s.trim().is_empty())
        .ok_or_else(|| {
            parsed.error(
                &schema::join(prefix, key),
                "required nonempty string is missing",
            )
        })
}
fn add_language_sections(body: &mut Value, whole: &Value) {
    for key in ["python", "cpp", "checks"] {
        if let Some(settings) = whole.get(key)
            && body.get(key).is_none()
        {
            body[key] = settings.clone();
        }
    }
}

fn default_origins(value: &Value, prefix: &str, origins: &mut BTreeMap<String, Origin>) {
    if let Some(table) = value.as_object() {
        for (key, child) in table {
            default_origins(child, &schema::join(prefix, key), origins);
        }
    } else {
        origins.insert(prefix.into(), Origin::defaults(prefix));
    }
}

fn checks(
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

struct ComponentContext<'a> {
    root: &'a Path,
    effective: &'a Value,
    parsed: &'a Parsed,
    family: SchemaFamily,
    root_checks: &'a BTreeMap<String, CheckSetting>,
    environment: &'a BTreeMap<String, String>,
}
fn component(
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
                        .any(|directory| directory == "." || within.starts_with(directory)))
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
        type_provider(&base_path, explicit_provider, parsed, prefix)?
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

fn automatic_components(
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
        if effective.get("source_dirs").is_some() {
            body["source_dirs"] = effective["source_dirs"].clone();
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
        resolved.files.retain(|file| owned_files.contains(file));
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

fn legacy_components(
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
    let mut value = component(
        ComponentContext {
            root,
            effective,
            parsed,
            family: SchemaFamily::Legacy,
            root_checks: checks,
            environment,
        },
        &body,
        "project",
        limitations,
    )?;
    value.settings["legacy"] = effective.clone();
    limitations.push("legacy source_dirs and engine options are preserved; use migrate preview before adopting automatic v2 scope".into());
    Ok(vec![value])
}

fn builds(
    value: &Value,
    root: &Path,
    parsed: &Parsed,
    environment: &BTreeMap<String, String>,
    origins: &BTreeMap<String, Origin>,
) -> Result<Vec<Build>, ConfigError> {
    let mut result = vec![];
    if let Some(table) = value.get("builds").and_then(Value::as_object) {
        for (id, settings) in table {
            let anchor = |name: &str| {
                text(settings, name)
                    .map(|raw| {
                        let origin = origins
                            .get(&format!("builds.{id}.{name}"))
                            .cloned()
                            .unwrap_or_else(|| parsed.origin(&format!("builds.{id}.{name}")));
                        let base = Path::new(&origin.file)
                            .parent()
                            .filter(|p| p.is_absolute())
                            .unwrap_or_else(|| parsed.path.parent().unwrap());
                        paths::anchor(&raw, base, root, environment, &origin, false)
                    })
                    .transpose()
            };
            let artifacts = strings(settings, "artifacts");
            for pattern in &artifacts {
                paths::check_glob(pattern, &parsed.origin(&format!("builds.{id}.artifacts")))?;
            }
            result.push(Build {
                id: id.clone(),
                system: text(settings, "system"),
                project: anchor("project")?,
                directory: anchor("directory")?,
                variant: text(settings, "variant").unwrap_or_else(|| "default".into()),
                prepare: text(settings, "prepare"),
                artifacts,
                settings: settings.clone(),
                origin: parsed.origin(&format!("builds.{id}")),
            });
        }
    }
    Ok(result)
}
fn tools(
    value: &Value,
    root: &Path,
    parsed: &Parsed,
    environment: &BTreeMap<String, String>,
    origins: &BTreeMap<String, Origin>,
) -> Result<BTreeMap<String, ToolSetting>, ConfigError> {
    let mut result = BTreeMap::new();
    if let Some(table) = value.get("tools").and_then(Value::as_object) {
        for (id, settings) in table {
            let origin = origins
                .get(&format!("tools.{id}.path"))
                .cloned()
                .unwrap_or_else(|| parsed.origin(&format!("tools.{id}.path")));
            let base = Path::new(&origin.file).parent().unwrap_or(root);
            let path = text(settings, "path")
                .map(|raw| paths::executable(&raw, base, root, environment, &origin))
                .transpose()?;
            let config = text(settings, "config")
                .map(|raw| {
                    paths::anchor(
                        &raw,
                        parsed.path.parent().unwrap(),
                        root,
                        environment,
                        &parsed.origin(&format!("tools.{id}.config")),
                        false,
                    )
                })
                .transpose()?;
            result.insert(
                id.clone(),
                ToolSetting {
                    id: id.clone(),
                    source: text(settings, "source"),
                    path,
                    config,
                    settings: settings.clone(),
                },
            );
        }
    }
    Ok(result)
}
fn suppressions(value: &Value, parsed: &Parsed) -> Result<Vec<Suppression>, ConfigError> {
    let mut result = vec![];
    if let Some(entries) = value.get("suppressions").and_then(Value::as_array) {
        for (index, value) in entries.iter().enumerate() {
            let path = text(value, "path").unwrap_or_default();
            if !path.is_empty() {
                paths::check_glob(
                    &path,
                    &parsed.origin(&format!("suppressions[{index}].path")),
                )?;
            }
            result.push(Suppression {
                fingerprint: text(value, "fingerprint").unwrap_or_default(),
                rule: text(value, "rule").unwrap_or_default(),
                path,
                component: text(value, "component").unwrap_or_default(),
                reason: text(value, "reason").unwrap_or_default(),
                declared_in: parsed.path.display().to_string(),
            });
        }
    }
    Ok(result)
}
fn publish(value: &Value) -> Option<Publish> {
    value.get("publish").map(|p| Publish {
        repo: text(p, "repo").unwrap_or_default(),
        api_url: text(p, "api_url").unwrap_or_default(),
        server_url: text(p, "server_url").unwrap_or_default(),
        branch: text(p, "branch").unwrap_or_else(|| "gh-pages".into()),
        token_env: text(p, "token_env").unwrap_or_else(|| "GITHUB_TOKEN".into()),
        settings: p.clone(),
    })
}

fn type_provider(
    root: &Path,
    explicit: Option<&str>,
    parsed: &Parsed,
    prefix: &str,
) -> Result<Option<String>, ConfigError> {
    if let Some(explicit) = explicit {
        return Ok(Some(explicit.into()));
    }
    let mut mypy = root.join("mypy.ini").is_file();
    let mut ty = root.join("ty.toml").is_file();
    let pyproject = root.join("pyproject.toml");
    if pyproject.is_file() {
        let native = Parsed::read(&pyproject, false, "discovered")?;
        mypy |= native.value.pointer("/tool/mypy").is_some();
        ty |= native.value.pointer("/tool/ty").is_some();
    }
    if let Ok(setup) = std::fs::read_to_string(root.join("setup.cfg")) {
        mypy |= setup
            .lines()
            .any(|line| line.trim().eq_ignore_ascii_case("[mypy]"));
    }
    match (mypy, ty) {
        (true, true) => Err(parsed.error(
            &format!("{prefix}.python.type_provider"),
            "both native mypy and ty settings exist; select python.type_provider explicitly",
        )),
        (true, false) => Ok(Some("mypy".into())),
        (false, true) => Ok(Some("ty".into())),
        _ => Ok(None),
    }
}

fn check_component_cycles(components: &[Component], parsed: &Parsed) -> Result<(), ConfigError> {
    let mut remaining: BTreeMap<_, _> = components
        .iter()
        .map(|c| {
            (
                c.id.as_str(),
                c.needs.iter().map(String::as_str).collect::<BTreeSet<_>>(),
            )
        })
        .collect();
    let all: BTreeSet<_> = remaining.keys().copied().collect();
    for needs in remaining.values_mut() {
        needs.retain(|id| all.contains(id));
    }
    while !remaining.is_empty() {
        let ready: Vec<_> = remaining
            .iter()
            .filter(|(_, needs)| needs.is_empty())
            .map(|(id, _)| *id)
            .collect();
        if ready.is_empty() {
            return Err(parsed.error("components", "component dependency cycle"));
        }
        for id in ready {
            remaining.remove(id);
            for needs in remaining.values_mut() {
                needs.remove(id);
            }
        }
    }
    Ok(())
}

fn apply_component_local(body: &mut Value, effective: &Value) {
    let Some(id) = body.get("id").and_then(Value::as_str).map(str::to_string) else {
        return;
    };
    if let Some(overlay) = effective.get("_local_components").and_then(|v| v.get(&id)) {
        merge(body, overlay);
    }
}

fn preferences(
    root: &Path,
    environment: &BTreeMap<String, String>,
    effective: &mut Value,
    origins: &mut BTreeMap<String, Origin>,
    files: &mut Vec<PathBuf>,
    limitations: &mut Vec<String>,
) -> Result<(), ConfigError> {
    let home = environment
        .get("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .or_else(|| {
            environment
                .get("HOME")
                .map(|v| PathBuf::from(v).join(".config"))
        });
    if let Some(home) = home {
        let path = home.join("ici/preferences.toml");
        if path.is_file() {
            let parsed = Parsed::read(&path, false, "global_preference")?;
            apply_overlay(&parsed, effective, origins, true)?;
            files.push(path);
        }
        if home.join("ici/ici.toml").is_file() || home.join("ici/config.toml").is_file() {
            limitations.push("legacy global policy is not implicitly merged; inspect migration preview and move intentional team policy into the project".into());
        }
    }
    let _ = root;
    Ok(())
}

fn apply_overlay(
    parsed: &Parsed,
    effective: &mut Value,
    origins: &mut BTreeMap<String, Origin>,
    global: bool,
) -> Result<(), ConfigError> {
    fn visit(
        parsed: &Parsed,
        value: &Value,
        parts: &mut Vec<String>,
        effective: &mut Value,
        origins: &mut BTreeMap<String, Origin>,
        global: bool,
    ) -> Result<(), ConfigError> {
        if let Some(table) = value.as_object() {
            for (key, child) in table {
                parts.push(key.clone());
                visit(parsed, child, parts, effective, origins, global)?;
                parts.pop();
            }
            return Ok(());
        }
        let key = parts.join(".");
        let tool = parts.len() == 3 && parts[0] == "tools" && parts[2] == "path";
        let build = !global
            && parts.len() == 3
            && parts[0] == "builds"
            && ["project", "directory"].contains(&parts[2].as_str());
        let runtime = !global
            && parts.len() == 4
            && parts[0] == "components"
            && parts[2] == "python"
            && parts[3] == "executable";
        let display = global
            && parts.len() == 2
            && parts[0] == "display"
            && ["color", "verbosity", "max_findings", "group_by", "format"]
                .contains(&parts[1].as_str());
        if !(tool
            || build
            || runtime
            || display
            || (parts.len() == 1 && ["output", "cache"].contains(&parts[0].as_str())))
        {
            return Err(parsed.error(
                &key,
                "local preferences may change display/cache/tool paths, never inspection policy",
            ));
        }
        if build
            && effective
                .get("builds")
                .and_then(|v| v.get(&parts[1]))
                .is_none()
        {
            return Err(parsed.error(
                &key,
                "local overlay may relocate an existing build, not declare one",
            ));
        }
        if !display && value.as_str().is_none_or(str::is_empty) {
            return Err(parsed.error(&key, "local path must be a nonempty string"));
        }
        if display
            && ((parts[1] == "max_findings" && value.as_u64().is_none_or(|v| v > 100000))
                || (parts[1] != "max_findings" && value.as_str().is_none_or(str::is_empty)))
        {
            return Err(parsed.error(&key, "display preference has an invalid type or bound"));
        }
        if global && origins.get(&key).is_some_and(|o| o.layer != "defaults") {
            return Ok(());
        }
        let mut destination = &mut *effective;
        let mut write_parts = parts.clone();
        if runtime {
            write_parts[0] = "_local_components".into();
        }
        for part in &write_parts[..write_parts.len() - 1] {
            if destination.get(part).is_none() {
                destination[part] = json!({});
            }
            if !destination[part].is_object() {
                return Err(parsed.error(&key, "local overlay conflicts with declared value"));
            }
            destination = &mut destination[part];
        }
        destination[write_parts.last().unwrap()] = value.clone();
        if effective.get("_local_origins").is_none() {
            effective["_local_origins"] = json!({});
        }
        effective["_local_origins"][&key] =
            serde_json::to_value(parsed.origin(&key)).expect("origin serializes");
        origins.insert(key.clone(), parsed.origin(&key));
        Ok(())
    }
    visit(
        parsed,
        &parsed.value,
        &mut vec![],
        effective,
        origins,
        global,
    )
}

fn validate_legacy_paths(
    root: &Path,
    value: &Value,
    parsed: &Parsed,
    environment: &BTreeMap<String, String>,
) -> Result<(), ConfigError> {
    for pointer in [
        "/project/source_dirs",
        "/project/cpp_external_build_dirs",
        "/engines/line/gate_dirs",
        "/engines/line/include_dirs",
        "/engines/line/exclude_dirs",
    ] {
        if let Some(values) = value.pointer(pointer).and_then(Value::as_array) {
            for item in values {
                paths::anchor(
                    item.as_str().expect("validated path list"),
                    root,
                    root,
                    environment,
                    &parsed.origin(&pointer[1..].replace('/', ".")),
                    false,
                )?;
            }
        }
    }
    for pointer in [
        "/project/compile_database",
        "/engines/lint/clang_tidy_config",
        "/build/make/workdir",
        "/build/make/shadow_dir",
    ] {
        if let Some(path) = value.pointer(pointer).and_then(Value::as_str) {
            let resolved = paths::anchor(
                path,
                root,
                root,
                environment,
                &parsed.origin(&pointer[1..].replace('/', ".")),
                false,
            )?;
            if pointer.ends_with("shadow_dir") && resolved == "." {
                return Err(parsed.error(
                    "build.make.shadow_dir",
                    "shadow directory must not be the project root",
                ));
            }
            if pointer.ends_with("workdir")
                && resolved == "."
                && value
                    .pointer("/build/make/out_of_tree")
                    .and_then(Value::as_str)
                    == Some("required")
            {
                return Err(parsed.error(
                    "build.make.workdir",
                    "out_of_tree=required needs a non-root workdir",
                ));
            }
        }
    }
    if value
        .pointer("/build/make/enabled")
        .and_then(Value::as_bool)
        == Some(true)
        && value
            .pointer("/build/make/build_argv")
            .and_then(Value::as_array)
            .is_none_or(Vec::is_empty)
    {
        return Err(parsed.error(
            "build.make.build_argv",
            "enabled Make backend requires explicit build_argv",
        ));
    }
    Ok(())
}
