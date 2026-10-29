//! Rust-owned release packaging over immutable inputs. Planning never writes or probes.
use ici_application::snapshot::{InputLimits, InputSnapshot};
use ici_config::{Component, ResolvedProject};
use ici_domain::{AnalysisOutput, InspectionTarget, TaskKind, TaskSpec, TaskState, sha256};
use ici_execution::{
    Cancellation, TaskOutcome,
    storage::{self, WriteMode},
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{
    collections::{BTreeMap, BTreeSet},
    io::Write,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
};
mod declarations;
mod metadata;

/// Static metadata shared by native compilation and packaging; this never probes Git.
pub(crate) use metadata::source_metadata;

#[derive(Clone, Debug, Serialize)]
pub struct BuildMetadata {
    pub name: String,
    pub version: String,
    pub version_source: String,
}
/// Measured producer records for contained compiled outputs; aliases copy canonical bytes.
#[derive(Clone, Debug)]
pub struct ArtifactSource {
    pub path: String,
    pub kind: String,
    pub sha256: String,
    pub size: u64,
    pub mode: u32,
    pub destination_name: Option<String>,
}
#[derive(Clone, Debug)]
enum Content {
    Source(String),
    Generated(Vec<u8>),
}
#[derive(Clone, Debug)]
struct OutputFile {
    path: String,
    content: Content,
    kind: String,
    mode: u32,
    message: String,
}
#[derive(Clone, Debug)]
pub struct ArtifactBuildPlan {
    pub task: TaskSpec,
    pub root: PathBuf,
    pub component_id: String,
    pub metadata: BuildMetadata,
    pub output_root: String,
    pub output_paths: Vec<String>,
    pub version_probe: Option<TaskSpec>,
    pub limitations: Vec<String>,
    pub binary_inputs: BTreeSet<String>,
    version_probe_evidence: Option<serde_json::Value>,
    inputs: BTreeMap<String, String>,
    files: Vec<OutputFile>,
    manifest_path: String,
    declarations: declarations::Declarations,
    declared: BTreeMap<String, String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema_id: String,
    schema_version: u32,
    component_id: String,
    output_root: String,
    metadata: BuildMetadataRecord,
    files: BTreeMap<String, ManifestFile>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    retired_files: BTreeMap<String, ManifestFile>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct BuildMetadataRecord {
    name: String,
    version: String,
    version_source: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ManifestFile {
    sha256: String,
    kind: String,
    mode: u32,
    owned: bool,
}

pub fn describe(
    project: &ResolvedProject,
    component: &Component,
    task_id: &str,
) -> Result<ArtifactBuildPlan, String> {
    describe_with_environment(project, component, task_id, &BTreeMap::new())
}
pub fn describe_with_environment(
    project: &ResolvedProject,
    component: &Component,
    task_id: &str,
    environment: &BTreeMap<String, String>,
) -> Result<ArtifactBuildPlan, String> {
    describe_internal(project, component, task_id, environment, &[], 0)
}

pub fn describe_compiled(
    project: &ResolvedProject,
    component: &Component,
    task_id: &str,
    environment: &BTreeMap<String, String>,
    compiled: &[ArtifactSource],
) -> Result<ArtifactBuildPlan, String> {
    describe_internal(project, component, task_id, environment, compiled, 0)
}
/// Frozen adapter builds (CMake, qmake, Make) record their linked outputs in the shadow and
/// publish only the release tree and environment loaders; nothing is copied into `bin/`.
pub fn describe_adapter(
    project: &ResolvedProject,
    component: &Component,
    task_id: &str,
    environment: &BTreeMap<String, String>,
    measured: usize,
) -> Result<ArtifactBuildPlan, String> {
    describe_internal(project, component, task_id, environment, &[], measured)
}

fn artifact_limits() -> InputLimits {
    InputLimits {
        file_bytes: 256 * 1024 * 1024,
        total_bytes: 512 * 1024 * 1024,
    }
}

fn describe_internal(
    project: &ResolvedProject,
    component: &Component,
    task_id: &str,
    environment: &BTreeMap<String, String>,
    compiled: &[ArtifactSource],
    measured_adapter_artifacts: usize,
) -> Result<ArtifactBuildPlan, String> {
    let declarations = declarations::Declarations::collect(project, component)?;
    let base = if component.root == "." {
        project.root.clone()
    } else {
        project.root.join(&component.root)
    };
    let metadata::SourceMetadata {
        metadata_snapshot,
        name,
        version,
        mut version_source,
        mut scripts,
    } = source_metadata(project, &base)?;
    let mut limitations = vec![];
    let mut version_probe = None;
    let version = if let Some(version) = version {
        version
    } else {
        let explicit = project
            .tools
            .get("git")
            .and_then(|tool| tool.path.as_deref())
            .map(|path| {
                if Path::new(path).is_absolute() {
                    PathBuf::from(path)
                } else {
                    project.root.join(path)
                }
            });
        let explicitly_configured = explicit.is_some();
        match (ici_providers::resolver::ToolRequest {
            id: "git".into(),
            explicit,
            project_root: base.clone(),
            pack_root: None,
            path_env: Some(environment.get("PATH").cloned().unwrap_or_default()),
        })
        .resolve()
        {
            Ok(tool) => {
                let mut probe = TaskSpec::command(
                    format!("{task_id}.version"),
                    "git-describe",
                    vec![
                        tool.path.to_string_lossy().into_owned(),
                        "describe".into(),
                        "--tags".into(),
                        "--always".into(),
                    ],
                    base.to_string_lossy(),
                );
                probe.kind = TaskKind::Probe;
                probe.timeout_seconds = Some(2.0);
                probe.output_limit_bytes = Some(8192);
                probe.tool_digest = Some(tool.digest);
                probe.analysis_unit_ids = vec![format!("{}.python", component.id)];
                probe
                    .env_overlay
                    .insert("GIT_OPTIONAL_LOCKS".into(), "0".into());
                probe
                    .env_overlay
                    .insert("GIT_TERMINAL_PROMPT".into(), "0".into());
                version_probe = Some(probe);
                version_source = "planned:git-describe".into();
                limitations.push("release version and output paths will be resolved by the visible Git describe probe before preparation".into());
                "v__git_describe__".into()
            }
            Err(error) if explicitly_configured => {
                return Err(format!("configured Git executable is unavailable: {error}"));
            }
            Err(_) => {
                version_source = "fallback:git-unavailable".into();
                limitations.push("Git is unavailable; the historical project version default v1.0.0 is used without claiming Git evidence".into());
                "v1.0.0".into()
            }
        }
    };

    let metadata = BuildMetadata {
        name,
        version,
        version_source,
    };
    let local_output = Path::new(&metadata.version).join("x86_64");
    let output_root = relative(&project.root, &base.join(local_output))?;
    let source_roots = crate::inputs::source_roots(component, &project.root);
    let mut sources = component
        .files
        .iter()
        .filter(|path| {
            (path.ends_with(".py") || path.ends_with(".pyi")) && !under(path, &output_root)
        })
        .cloned()
        .collect::<Vec<_>>();
    if sources.len() > 2048 {
        return Err("Python artifact inventory exceeds 2048 sources".into());
    }
    let mut files = vec![];
    let mut destinations = BTreeSet::new();
    for source in &sources {
        let local = source_roots
            .iter()
            .find_map(|root| {
                if root == "." {
                    Some(source.as_str())
                } else {
                    source
                        .strip_prefix(root)
                        .and_then(|tail| tail.strip_prefix('/'))
                }
            })
            .filter(|relative| !relative.is_empty())
            .ok_or_else(|| format!("Python source {source} lies outside configured roots"))?;
        let path = format!("{output_root}/lib/{local}");
        if !destinations.insert(path.clone()) {
            return Err(format!("Python artifact collision at {path}"));
        }
        files.push(OutputFile {
            path,
            content: Content::Source(source.clone()),
            kind: "python-library".into(),
            mode: std::fs::symlink_metadata(project.root.join(source))
                .map_err(|error| error.to_string())?
                .permissions()
                .mode()
                & 0o777,
            message: format!("Packaged Python source {local}"),
        });
    }
    if let Some(entry) = project.effective.pointer("/build/python/entrypoint") {
        scripts.clear();
        scripts.insert(
            metadata.name.clone(),
            entry
                .as_str()
                .ok_or_else(|| "build.python.entrypoint must be a string".to_string())?
                .into(),
        );
    }
    if scripts.len() > 256 {
        return Err("Python artifact inventory exceeds 256 entrypoint launchers".into());
    }
    for (name, target) in scripts {
        validate_launcher_name(&name)?;
        let (module, callable) = target
            .split_once(':')
            .ok_or_else(|| format!("Invalid Python entrypoint: {target}"))?;
        if !module.split('.').all(identifier) || !identifier(callable) {
            return Err(format!("Invalid Python entrypoint: {target}"));
        }
        let module_path = module.replace('.', "/");
        if !sources.iter().any(|source| {
            source_roots.iter().any(|root| {
                let prefix = if root == "." {
                    String::new()
                } else {
                    format!("{root}/")
                };
                source == &format!("{prefix}{module_path}.py")
                    || source == &format!("{prefix}{module_path}/__init__.py")
            })
        }) {
            return Err(format!("Python entrypoint module does not exist: {module}"));
        }
        let path = format!("{output_root}/bin/{name}");
        if !destinations.insert(path.clone()) {
            return Err(format!("Python artifact collision at {path}"));
        }
        files.push(OutputFile {
            path,
            content: Content::Generated(launcher(module, callable).into_bytes()),
            kind: "python-launcher".into(),
            mode: 0o755,
            message: format!("Callable launcher for {target}"),
        });
    }
    if compiled.len() > 512 {
        return Err("compiled artifact inventory exceeds 512 records".into());
    }
    for record in compiled {
        ici_domain::validation::relative_path(&record.path, false)
            .map_err(|error| error.to_string())?;
        let directory = match record.kind.as_str() {
            "executable" => "bin",
            "shared-library" | "static-library" => "lib",
            _ => {
                return Err(format!(
                    "unsupported compiled artifact kind: {}",
                    record.kind
                ));
            }
        };
        let name = record
            .destination_name
            .as_deref()
            .unwrap_or_else(|| record.path.rsplit('/').next().unwrap_or_default());
        validate_launcher_name(name)?;
        if record.mode > 0o777 || record.size > 256 * 1024 * 1024 {
            return Err("compiled artifact mode or size exceeds contract".into());
        }
        let path = format!("{output_root}/{directory}/{name}");
        if !destinations.insert(path.clone()) {
            return Err(format!("compiled artifact collision at {path}"));
        }
        let metadata = std::fs::symlink_metadata(project.root.join(&record.path))
            .map_err(|error| error.to_string())?;
        if !metadata.is_file()
            || metadata.len() != record.size
            || metadata.permissions().mode() & 0o777 != record.mode
        {
            return Err(format!(
                "compiled artifact no longer matches its measured producer record: {}",
                record.path
            ));
        }
        sources.push(record.path.clone());
        files.push(OutputFile {
            path,
            content: Content::Source(record.path.clone()),
            kind: record.kind.clone(),
            mode: record.mode,
            message: format!("Packaged {} {}", record.kind, record.path),
        });
    }
    if files.is_empty() && declarations.is_empty() && measured_adapter_artifacts == 0 {
        return Err("No library, launcher, compiled or declared artifact was selected".into());
    }
    for (name, content) in [("env.sh", ENV_SH), ("env.csh", ENV_CSH)] {
        files.push(OutputFile {
            path: format!("{output_root}/{name}"),
            // Frozen _generate_env_scripts writes these exact loaders. Only a package that
            // publishes a shared library into lib/ (never produced by the frozen pyz) also
            // needs that directory on the dynamic loader path.
            content: Content::Generated(if !compiled.iter().any(|record| record.kind == "shared-library") { content.as_bytes().to_vec() } else if name == "env.sh" {
                content.replace("export PYTHONPATH=", "export LD_LIBRARY_PATH=\"${SCRIPT_DIR}/lib:${LD_LIBRARY_PATH:-}\"\nexport PYTHONPATH=").into_bytes()
            } else {
                content.replace("setenv PYTHONPATH", "if (! $?LD_LIBRARY_PATH) setenv LD_LIBRARY_PATH \"\"\nsetenv LD_LIBRARY_PATH \"${FULL_DIR}/lib:${LD_LIBRARY_PATH}\"\nsetenv PYTHONPATH").into_bytes()
            }),
            kind: "environment-script".into(),
            mode: 0o755,
            message: format!("Generated {name}"),
        });
    }
    let identity = sha256(format!("{}\0{output_root}", component.id));
    let manifest_path = format!(
        ".ici/rust-v1/python-artifacts/{}.json",
        identity.trim_start_matches("sha256:")
    );
    let mut inputs = metadata_snapshot
        .files
        .iter()
        .map(|(path, file)| (path.clone(), file.digest.clone()))
        .collect::<BTreeMap<_, _>>();
    let declared = declarations.select(&files, version_probe.is_some())?;
    let generated_paths = files
        .iter()
        .map(|file| file.path.as_str())
        .collect::<BTreeSet<_>>();
    let binary_inputs = compiled
        .iter()
        .map(|record| record.path.clone())
        .chain(
            declarations
                .input_paths()
                .into_iter()
                .filter(|path| !generated_paths.contains(path.as_str())),
        )
        .collect::<BTreeSet<_>>();
    sources.extend(binary_inputs.iter().cloned());
    sources.sort();
    sources.dedup();
    let source_snapshot = InputSnapshot::take(&project.root, &sources, &[], artifact_limits())
        .map_err(|error| error.to_string())?;
    for record in compiled {
        if source_snapshot
            .files
            .get(&record.path)
            .is_none_or(|file| file.digest != record.sha256)
        {
            return Err(format!("compiled artifact digest changed: {}", record.path));
        }
    }
    inputs.extend(
        source_snapshot
            .files
            .iter()
            .map(|(path, file)| (path.clone(), file.digest.clone())),
    );
    let mut output_paths = files
        .iter()
        .map(|file| file.path.clone())
        .collect::<Vec<_>>();
    output_paths.push(manifest_path.clone());
    if output_paths
        .iter()
        .any(|output| inputs.contains_key(output))
    {
        return Err("Python artifact output collides with a captured project input".into());
    }
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let mut task = TaskSpec::command(
        task_id,
        if compiled.is_empty() {
            "ici-python-artifact"
        } else {
            "ici-native-artifact"
        },
        vec![
            executable.to_string_lossy().into_owned(),
            "__artifact-build".into(),
            "--component".into(),
            component.id.clone(),
        ],
        base.to_string_lossy(),
    );
    task.kind = TaskKind::Prepare;
    if let Some(probe) = &version_probe {
        task.depends_on.push(probe.id.clone());
    }
    task.analysis_unit_ids = vec![format!(
        "{}.{}",
        component.id,
        if compiled.is_empty() { "python" } else { "cpp" }
    )];
    task.input_refs = inputs
        .keys()
        .map(|path| project.root.join(path).to_string_lossy().into_owned())
        .collect();
    task.output_specs = output_paths
        .iter()
        .map(|path| project.root.join(path).to_string_lossy().into_owned())
        .collect();
    task.work_dirs = vec![
        project
            .root
            .join(&output_root)
            .to_string_lossy()
            .into_owned(),
        project
            .root
            .join(".ici/rust-v1/python-artifacts")
            .to_string_lossy()
            .into_owned(),
    ];
    task.resource_keys = vec![
        format!("project-execution:{}", base.display()),
        format!(
            "python-artifact-root:{}",
            project.root.join(&output_root).display()
        ),
    ];
    // This is an in-process operation. The application records the core identity once;
    // no external executable identity is invented or rehashed for a displayed marker.
    Ok(ArtifactBuildPlan {
        task,
        root: project.root.clone(),
        component_id: component.id.clone(),
        metadata,
        output_root,
        output_paths,
        version_probe,
        limitations,
        binary_inputs,
        version_probe_evidence: None,
        inputs,
        files,
        manifest_path,
        declarations,
        declared,
    })
}

pub fn execute(
    plan: &ArtifactBuildPlan,
    snapshot: &InputSnapshot,
    cancellation: &Cancellation,
) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(&plan.task.id, &plan.task.provider);
    output.observation.limitations.clone_from(&plan.limitations);
    if let Some(probe) = &plan.version_probe_evidence {
        output.extra["version_probe"] = probe.clone();
    }
    match execute_inner(plan, snapshot, cancellation, &mut output) {
        Ok(()) => {
            output.summary = format!(
                "Build Completed: {} {} into {}",
                plan.metadata.name, plan.metadata.version, plan.output_root
            );
            output.extra["project_name"] = json!(plan.metadata.name);
            output.extra["project_version"] = json!(plan.metadata.version);
            output.extra["version_source"] = json!(plan.metadata.version_source);
            output.extra["target_path"] = json!(plan.root.join(&plan.output_root));
            output.extra["metrics_summary"] = json!(format!("Built into {}", plan.output_root));
            output.extra["artifact_manifest"] = json!(plan.manifest_path);
        }
        Err(reason) => {
            output.observation.state = if cancellation.is_cancelled() {
                TaskState::Cancelled
            } else {
                TaskState::Failed
            };
            output.status = "ERROR".into();
            output.summary = reason.clone();
            output.observation.limitations.push(reason.clone());
            let mut target = InspectionTarget::at("ici.toml", 1);
            target.status = "ERROR".into();
            target.target_name = "PythonArtifactBuild".into();
            target.message = reason;
            output.targets.push(target);
        }
    }
    output
}
fn execute_inner(
    plan: &ArtifactBuildPlan,
    snapshot: &InputSnapshot,
    cancellation: &Cancellation,
    output: &mut AnalysisOutput,
) -> Result<(), String> {
    if plan.metadata.version_source == "planned:git-describe" {
        return Err(
            "release version must be resolved by its visible Git probe before writing".into(),
        );
    }
    for (path, digest) in &plan.inputs {
        if snapshot
            .files
            .get(path)
            .is_none_or(|file| &file.digest != digest)
        {
            return Err(format!(
                "Python artifact input {path} changed after planning or was not captured"
            ));
        }
    }
    let previous = read_manifest(plan)?;
    let existing = read_existing(
        plan,
        &plan
            .files
            .iter()
            .map(|file| file.path.clone())
            .collect::<Vec<_>>(),
    )?;
    let mut manifest = Manifest {
        schema_id: "ici.python-artifacts".into(),
        schema_version: 1,
        component_id: plan.component_id.clone(),
        output_root: plan.output_root.clone(),
        metadata: BuildMetadataRecord {
            name: plan.metadata.name.clone(),
            version: plan.metadata.version.clone(),
            version_source: plan.metadata.version_source.clone(),
        },
        files: BTreeMap::new(),
        retired_files: BTreeMap::new(),
    };
    let mut writes = vec![];
    // Complete collision/ownership validation precedes any output creation.
    for file in &plan.files {
        let bytes = match &file.content {
            Content::Source(path) => &snapshot.files[path].input.bytes,
            Content::Generated(bytes) => bytes,
        };
        let digest = sha256(bytes);
        if existing.contains_key(&file.path) {
            let metadata = std::fs::symlink_metadata(plan.root.join(&file.path))
                .map_err(|error| error.to_string())?;
            if !metadata.is_file() || metadata.permissions().mode() & 0o777 != file.mode {
                return Err(format!(
                    "refusing to replace existing user data or modified artifact permissions: {}",
                    file.path
                ));
            }
        }
        let (write, owned) = if let Some(current) = existing.get(&file.path) {
            let prior = previous.as_ref().and_then(|manifest| {
                manifest
                    .files
                    .get(&file.path)
                    .or_else(|| manifest.retired_files.get(&file.path))
            });
            if current == &digest {
                (
                    false,
                    prior.is_some_and(|prior| prior.owned && prior.sha256 == *current),
                )
            } else if prior.is_some_and(|prior| prior.owned && prior.sha256 == *current) {
                (true, true)
            } else {
                return Err(format!(
                    "refusing to replace existing user data or modified artifact: {}",
                    file.path
                ));
            }
        } else {
            (true, true)
        };
        manifest.files.insert(
            file.path.clone(),
            ManifestFile {
                sha256: digest,
                kind: file.kind.clone(),
                mode: file.mode,
                owned,
            },
        );
        writes.push((file, bytes, write, existing.contains_key(&file.path)));
    }
    if let Some(previous) = &previous {
        let candidates = previous
            .files
            .iter()
            .chain(&previous.retired_files)
            .filter(|(path, record)| record.owned && !manifest.files.contains_key(*path))
            .collect::<Vec<_>>();
        let existing = read_existing(
            plan,
            &candidates
                .iter()
                .map(|(path, _)| (*path).clone())
                .collect::<Vec<_>>(),
        )?;
        for (path, record) in candidates {
            if existing.get(path) == Some(&record.sha256)
                && std::fs::symlink_metadata(plan.root.join(path)).is_ok_and(|metadata| {
                    metadata.is_file() && metadata.permissions().mode() & 0o777 == record.mode
                })
            {
                manifest.retired_files.insert(path.clone(), record.clone());
            }
        }
    }
    release_directories(plan)?;
    for (file, bytes, write, existed) in writes {
        if cancellation.is_cancelled() {
            return Err("Python artifact preparation cancelled".into());
        }
        if write {
            storage::atomic_write_with(
                &plan.root.join(&file.path),
                if existed {
                    WriteMode::ReplaceRegular
                } else {
                    WriteMode::CreateNew
                },
                |destination| {
                    destination.write_all(bytes)?;
                    destination.set_permissions(std::fs::Permissions::from_mode(file.mode))
                },
            )
            .map_err(|error| format!("Python artifact {}: {error}", file.path))?;
        }
        let mut target = InspectionTarget::at(&file.path, 1);
        target.target_name = match file.kind.as_str() {
            "python-library" => "PythonLibrary",
            "python-launcher" => "PythonLauncher",
            "executable" | "shared-library" | "static-library" => "CompiledArtifact",
            _ => "EnvironmentScript",
        }
        .into();
        target.message = file.message.clone();
        output.targets.push(target);
    }
    if cancellation.is_cancelled() {
        return Err("Python artifact preparation cancelled before manifest publication".into());
    }
    let bytes = serde_json::to_vec_pretty(&manifest).map_err(|error| error.to_string())?;
    storage::atomic_write(
        &plan.root.join(&plan.manifest_path),
        &bytes,
        if previous.is_some() {
            WriteMode::ReplaceRegular
        } else {
            WriteMode::CreateNew
        },
    )
    .map_err(|error| format!("artifact ownership manifest: {error}"))?;
    let verified = verify_output(plan)?;
    let records = plan.files.iter().map(|file| {
        let record = &manifest.files[&file.path];
        let size = match &file.content { Content::Source(source) => snapshot.files[source].input.bytes.len(), Content::Generated(bytes) => bytes.len() };
        json!({"path":file.path,"kind":record.kind,"sha256":record.sha256,"artifact_id":format!("release:project:{}",file.path),"owned":record.owned,"size":size,"mode":record.mode})
    }).collect::<Vec<_>>();
    output.extra["artifact_manifests"] = json!([{"producer":if plan.task.provider == "ici-native-artifact" { "ici.native-artifact" } else { "ici.python-artifact" },"variant":"release","scope":"project","metadata":plan.metadata,"artifacts":records}]);
    declarations::publish(plan, snapshot, output)?;
    output.extra["verified_output_paths"] = json!(verified);
    output.extra["retired_artifacts"] = json!(manifest.retired_files.keys().collect::<Vec<_>>());
    Ok(())
}

/// Frozen `_prepare_output_tree`: `<version>/x86_64` with `bin/` and `lib/`, created like
/// Python `mkdir` (mode 0777 under the process umask). Existing directories are kept as they are;
/// a symlinked or non-directory component is refused.
fn release_directories(plan: &ArtifactBuildPlan) -> Result<(), String> {
    use std::os::unix::fs::DirBuilderExt;
    let mut targets = vec![];
    let mut current = PathBuf::new();
    for part in Path::new(&plan.output_root).components() {
        current.push(part);
        targets.push(current.clone());
    }
    targets.push(current.join("bin"));
    targets.push(current.join("lib"));
    for relative in targets {
        let path = plan.root.join(&relative);
        match std::fs::symlink_metadata(&path) {
            Ok(metadata) if metadata.is_dir() => {}
            Ok(_) => {
                return Err(format!(
                    "release output directory is not a plain directory: {}",
                    relative.display()
                ));
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                std::fs::DirBuilder::new()
                    .mode(0o777)
                    .create(&path)
                    .map_err(|error| format!("release output {}: {error}", relative.display()))?;
            }
            Err(error) => return Err(error.to_string()),
        }
    }
    Ok(())
}

/// Exact no-follow digest proof used before classifying new release outputs as authorized effects.
pub fn verify_output(plan: &ArtifactBuildPlan) -> Result<BTreeMap<String, String>, String> {
    let manifest = read_manifest(plan)?
        .ok_or_else(|| "Python artifact ownership manifest is missing".to_string())?;
    let paths = plan
        .files
        .iter()
        .map(|file| file.path.clone())
        .chain(std::iter::once(plan.manifest_path.clone()))
        .collect::<Vec<_>>();
    let snapshot = InputSnapshot::take(&plan.root, &paths, &[], artifact_limits())
        .map_err(|error| error.to_string())?;
    let mut output = BTreeMap::new();
    if manifest.files.len() != plan.files.len() {
        return Err("Python artifact ownership manifest has unexpected file membership".into());
    }
    for file in &plan.files {
        let record = manifest
            .files
            .get(&file.path)
            .ok_or_else(|| format!("artifact manifest omits {}", file.path))?;
        let captured = &snapshot.files[&file.path];
        let metadata = std::fs::symlink_metadata(plan.root.join(&file.path))
            .map_err(|error| error.to_string())?;
        if captured.digest != record.sha256
            || record.kind != file.kind
            || record.mode != file.mode
            || !metadata.is_file()
            || metadata.permissions().mode() & 0o777 != record.mode
        {
            return Err(format!(
                "Python artifact {} no longer matches the published manifest",
                file.path
            ));
        }
        output.insert(file.path.clone(), captured.digest.clone());
    }
    output.insert(
        plan.manifest_path.clone(),
        snapshot.files[&plan.manifest_path].digest.clone(),
    );
    Ok(output)
}
fn read_manifest(plan: &ArtifactBuildPlan) -> Result<Option<Manifest>, String> {
    if std::fs::symlink_metadata(plan.root.join(&plan.manifest_path)).is_err() {
        return Ok(None);
    }
    let snapshot = InputSnapshot::take(
        &plan.root,
        std::slice::from_ref(&plan.manifest_path),
        &[],
        InputLimits {
            file_bytes: 2 * 1024 * 1024,
            total_bytes: 2 * 1024 * 1024,
        },
    )
    .map_err(|error| format!("artifact ownership manifest: {error}"))?;
    let manifest: Manifest = serde_json::from_slice(
        &snapshot.files[&plan.manifest_path].input.bytes,
    )
    .map_err(|error| format!("artifact ownership manifest is not a valid ici record: {error}"))?;
    validate_records(&manifest)?;
    if manifest.schema_id != "ici.python-artifacts"
        || manifest.schema_version != 1
        || manifest.component_id != plan.component_id
        || manifest.output_root != plan.output_root
    {
        return Err("artifact ownership manifest belongs to a different contract".into());
    }
    Ok(Some(manifest))
}
fn read_existing(
    plan: &ArtifactBuildPlan,
    paths: &[String],
) -> Result<BTreeMap<String, String>, String> {
    let paths = paths
        .iter()
        .filter(|path| std::fs::symlink_metadata(plan.root.join(path)).is_ok())
        .cloned()
        .collect::<Vec<_>>();
    if paths.is_empty() {
        return Ok(BTreeMap::new());
    }
    let snapshot = InputSnapshot::take(&plan.root, &paths, &[], artifact_limits())
        .map_err(|error| format!("existing artifact cannot be inspected safely: {error}"))?;
    Ok(snapshot
        .files
        .into_iter()
        .map(|(path, file)| (path, file.digest))
        .collect())
}
fn relative(root: &Path, path: &Path) -> Result<String, String> {
    let relative = path
        .strip_prefix(root)
        .map_err(|_| "artifact path escapes workspace".to_string())?
        .to_string_lossy()
        .into_owned();
    ici_domain::validation::relative_path(&relative, false).map_err(|error| error.to_string())?;
    Ok(relative)
}
fn under(path: &str, parent: &str) -> bool {
    path == parent
        || path
            .strip_prefix(parent)
            .is_some_and(|tail| tail.starts_with('/'))
}
fn validate_name(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value == "."
        || value == ".."
        || value.len() > 255
        || !value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "._-".contains(c))
    {
        Err(format!("invalid project name: {value:?}"))
    } else {
        Ok(())
    }
}
fn normalize_version(value: &str) -> Result<String, String> {
    let value = value.strip_prefix('v').unwrap_or(value);
    if value.is_empty()
        || value.len() > 255
        || !value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "._-".contains(c))
    {
        Err(format!("invalid project version: {value:?}"))
    } else {
        Ok(format!("v{value}"))
    }
}
fn validate_launcher_name(value: &str) -> Result<(), String> {
    if value.len() > 255
        || !value
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_alphanumeric())
        || !value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "._-".contains(c))
    {
        Err(format!("unsafe Python launcher name: {value:?}"))
    } else {
        Ok(())
    }
}
fn identifier(value: &str) -> bool {
    let mut chars = value.chars();
    chars
        .next()
        .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}
fn launcher(module: &str, callable: &str) -> String {
    format!(
        r#"#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export PYTHONPATH="$SCRIPT_DIR/../lib:$ROOT_DIR/src:${{PYTHONPATH:-}}"

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON="$ROOT_DIR/.venv/bin/python"
elif [ -x "$ROOT_DIR/.venv/Scripts/python" ]; then
    PYTHON="$ROOT_DIR/.venv/Scripts/python"
else
    PYTHON="python3"
fi
exec "$PYTHON" -c 'import importlib, sys; sys.exit(importlib.import_module("{module}").{callable}())' "$@"
"#
    )
}
const ENV_SH: &str = r#"#!/usr/bin/env bash
# Portable Environment Loader for BASH / ZSH / SH
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="${SCRIPT_DIR}/bin:${PATH}"
export PYTHONPATH="${SCRIPT_DIR}/lib:${PYTHONPATH:-}"
echo "[ici Env] Loaded release environment from ${SCRIPT_DIR}"
"#;
const ENV_CSH: &str = r#"#!/bin/csh
# Portable Environment Loader for CSH / TCSH
set SCRIPT_PATH = ($_)
if ("$SCRIPT_PATH" == "") then
    set SCRIPT_DIR = `dirname $0`
else
    set SCRIPT_DIR = `dirname $SCRIPT_PATH[2]`
endif
set FULL_DIR = `cd "$SCRIPT_DIR" && pwd`
setenv PATH "${FULL_DIR}/bin:${PATH}"
setenv PYTHONPATH "${FULL_DIR}/lib:${PYTHONPATH}"
echo "[ici Env] Loaded release environment from ${FULL_DIR}"
"#;

/// Exclude only complete, unchanged native package records from future broad source discovery.
/// This neither deletes stale artifacts nor trusts a version-shaped directory by its name.
pub fn owned_outputs(root: &Path) -> Result<BTreeSet<String>, String> {
    Ok(verified_publication(root)?.owned)
}

/// Active published members, including unchanged user files, after complete manifest verification.
/// Retired files and ownership records are not currently published artifacts.
pub fn published_outputs(root: &Path) -> Result<BTreeSet<String>, String> {
    Ok(verified_publication(root)?.published.into_keys().collect())
}

/// Active artifact kinds accompany the same complete manifest verification as source exclusion.
pub fn published_artifacts(root: &Path) -> Result<BTreeMap<String, String>, String> {
    Ok(verified_publication(root)?.published)
}
/// Typed declarations retain their producer kind; membership never comes from filename guesses.
pub fn declared_artifacts(
    project: &ResolvedProject,
    component: &Component,
) -> Result<BTreeMap<String, String>, String> {
    declarations::Declarations::collect(project, component)?.select(&[], true)
}
/// Existing files selected by typed configuration declarations, using their canonical identities.
pub fn declared_outputs(
    project: &ResolvedProject,
    component: &Component,
) -> Result<BTreeSet<String>, String> {
    Ok(declared_artifacts(project, component)?
        .into_keys()
        .collect())
}

#[derive(Default)]
struct VerifiedPublication {
    owned: BTreeSet<String>,
    published: BTreeMap<String, String>,
}
fn verified_publication(root: &Path) -> Result<VerifiedPublication, String> {
    let directory = root.join(".ici/rust-v1/python-artifacts");
    let metadata = match std::fs::symlink_metadata(&directory) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(VerifiedPublication::default());
        }
        Err(error) => return Err(error.to_string()),
    };
    if !metadata.is_dir()
        || directory
            .canonicalize()
            .map_err(|error| error.to_string())?
            != directory
    {
        return Err("Python artifact manifest directory traverses a symlink".into());
    }
    let mut entries = std::fs::read_dir(&directory)
        .map_err(|error| error.to_string())?
        .take(129)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?;
    if entries.len() > 128 {
        return Err("Python artifact ownership inventory exceeds 128 manifests".into());
    }
    entries.sort_by_key(|entry| entry.file_name());
    let mut outputs = VerifiedPublication::default();
    let mut total_bytes = 0usize;
    let mut total_records = 0usize;
    for entry in entries {
        if entry
            .path()
            .extension()
            .is_none_or(|extension| extension != "json")
        {
            continue;
        }
        let manifest_path = relative(root, &entry.path())?;
        let snapshot = InputSnapshot::take(
            root,
            std::slice::from_ref(&manifest_path),
            &[],
            InputLimits {
                file_bytes: 2 * 1024 * 1024,
                total_bytes: 2 * 1024 * 1024,
            },
        )
        .map_err(|error| error.to_string())?;
        let manifest: Manifest =
            serde_json::from_slice(&snapshot.files[&manifest_path].input.bytes)
                .map_err(|error| format!("invalid Python artifact ownership manifest: {error}"))?;
        validate_name(&manifest.metadata.name)?;
        if manifest.schema_id != "ici.python-artifacts"
            || manifest.schema_version != 1
            || normalize_version(&manifest.metadata.version)? != manifest.metadata.version
            || manifest.files.is_empty()
            || manifest.files.len() > 4096
        {
            return Err("unrecognized Python artifact ownership record".into());
        }
        ici_domain::validation::relative_path(&manifest.output_root, false)
            .map_err(|error| error.to_string())?;
        let suffix = format!("{}/x86_64", manifest.metadata.version);
        if manifest.output_root != suffix && !manifest.output_root.ends_with(&format!("/{suffix}"))
        {
            return Err("Python artifact output root disagrees with its version".into());
        }
        let expected = sha256(format!(
            "{}\0{}",
            manifest.component_id, manifest.output_root
        ));
        if entry.file_name().to_string_lossy()
            != format!("{}.json", expected.trim_start_matches("sha256:"))
        {
            return Err("Python artifact manifest identity does not match its filename".into());
        }
        validate_records(&manifest)?;
        let paths = manifest
            .files
            .keys()
            .chain(manifest.retired_files.keys())
            .cloned()
            .collect::<Vec<_>>();
        let snapshot = InputSnapshot::take(root, &paths, &[], artifact_limits())
            .map_err(|error| error.to_string())?;
        total_bytes = total_bytes
            .checked_add(snapshot.total_bytes)
            .ok_or_else(|| "Python artifact ownership inventory overflow".to_string())?;
        total_records = total_records
            .checked_add(paths.len())
            .ok_or("artifact inventory count overflow")?;
        if total_bytes > 512 * 1024 * 1024 || total_records > 8192 {
            return Err("Python artifact ownership inventory exceeds 512 MiB or 8192 files".into());
        }
        for (path, record) in manifest.files.iter().chain(&manifest.retired_files) {
            let metadata =
                std::fs::symlink_metadata(root.join(path)).map_err(|error| error.to_string())?;
            if snapshot.files[path].digest != record.sha256
                || !metadata.is_file()
                || metadata.permissions().mode() & 0o777 != record.mode
            {
                return Err(format!(
                    "previous Python artifact {path} changed; it remains part of source discovery"
                ));
            }
        }
        for (path, record) in &manifest.files {
            if let Some(prior) = outputs.published.insert(path.clone(), record.kind.clone())
                && prior != record.kind
            {
                return Err("published artifact kinds disagree across ownership manifests".into());
            }
        }
        outputs.owned.extend(
            manifest
                .files
                .into_iter()
                .chain(manifest.retired_files)
                .filter_map(|(path, record)| record.owned.then_some(path)),
        );
        outputs.owned.insert(manifest_path);
    }
    Ok(outputs)
}

fn validate_records(manifest: &Manifest) -> Result<(), String> {
    if manifest
        .files
        .len()
        .saturating_add(manifest.retired_files.len())
        > 4096
        || manifest
            .files
            .keys()
            .any(|path| manifest.retired_files.contains_key(path))
    {
        return Err(
            "Python artifact ownership membership is ambiguous or exceeds 4096 files".into(),
        );
    }
    for (path, record) in manifest.files.iter().chain(&manifest.retired_files) {
        ici_domain::validation::relative_path(path, false).map_err(|error| error.to_string())?;
        let local = path
            .strip_prefix(&format!("{}/", manifest.output_root))
            .ok_or_else(|| "Python artifact manifest escapes its output root".to_string())?;
        let valid = match record.kind.as_str() {
            "python-library" => {
                record.mode <= 0o777
                    && local.starts_with("lib/")
                    && (local.ends_with(".py") || local.ends_with(".pyi"))
            }
            "python-launcher" => {
                record.mode == 0o755
                    && local
                        .strip_prefix("bin/")
                        .is_some_and(|name| validate_launcher_name(name).is_ok())
            }
            "executable" => {
                record.mode <= 0o777
                    && local
                        .strip_prefix("bin/")
                        .is_some_and(|name| validate_launcher_name(name).is_ok())
            }
            "shared-library" | "static-library" => {
                record.mode <= 0o777
                    && local
                        .strip_prefix("lib/")
                        .is_some_and(|name| validate_launcher_name(name).is_ok())
            }
            "environment-script" => record.mode == 0o755 && matches!(local, "env.sh" | "env.csh"),
            _ => false,
        };
        if !valid {
            return Err("unrecognized Python artifact file contract".into());
        }
    }
    Ok(())
}

/// Resolve only the visible probe outcome. This function performs no reads, writes or processes.
pub fn apply_version_probe(
    plan: &ArtifactBuildPlan,
    outcome: &TaskOutcome,
) -> Result<ArtifactBuildPlan, String> {
    let probe = plan
        .version_probe
        .as_ref()
        .ok_or_else(|| "this artifact plan has no Git version probe".to_string())?;
    if outcome.task_id != probe.id {
        return Err("Git version outcome belongs to a different task".into());
    }
    if outcome.cancelled || outcome.state == TaskState::Cancelled {
        return Err("Git version probe was cancelled".into());
    }
    let started = outcome.cleanup.group.is_some()
        || (outcome.state == TaskState::Succeeded && outcome.exit_code.is_some());
    if outcome.state == TaskState::Blocked || !started {
        return Err("Git version probe did not start under its selected executable identity; no fallback was selected".into());
    }
    let mut result = plan.clone();
    result.limitations.retain(|message| {
        !message.starts_with("release version and output paths will be resolved")
    });
    let completed = outcome.state == TaskState::Succeeded
        && !outcome.timed_out
        && !outcome.truncated
        && outcome.signal.is_none()
        && outcome.exit_code == Some(0);
    let (version, source) = if completed {
        let text = std::str::from_utf8(&outcome.stdout)
            .map_err(|_| "Git version output is not UTF-8".to_string())?
            .trim();
        if text.is_empty() {
            result.limitations.push(
                "Git describe returned no version; the historical default v1.0.0 is used".into(),
            );
            ("v1.0.0".into(), "fallback:git-empty".to_string())
        } else {
            (normalize_version(text)?, "git-describe".to_string())
        }
    } else {
        result.limitations.push(format!("Git describe did not complete successfully (state {:?}, exit {:?}); the historical default v1.0.0 is used",outcome.state,outcome.exit_code));
        ("v1.0.0".into(), "fallback:git-failed".to_string())
    };
    let output_root = relative(
        &plan.root,
        &Path::new(&plan.task.cwd).join(&version).join("x86_64"),
    )?;
    let mut excluded = vec![];
    result.files.retain(|file| match &file.content {
        Content::Source(path) if under(path, &output_root) => {
            excluded.push(path.clone());
            false
        }
        _ => true,
    });
    for path in excluded {
        result.inputs.remove(&path);
    }
    if result.declarations.is_empty()
        && !result.files.iter().any(|file| {
            matches!(file.content, Content::Source(_)) || file.kind == "python-launcher"
        })
    {
        return Err(
            "no Python library or launcher remains outside the resolved output tree".into(),
        );
    }
    for file in &mut result.files {
        let local = file
            .path
            .strip_prefix(&format!("{}/", plan.output_root))
            .ok_or_else(|| "planned artifact output is outside its version root".to_string())?;
        file.path = format!("{output_root}/{local}");
    }
    result.declared = result.declarations.select(&result.files, false)?;
    result.output_root = output_root;
    result.metadata.version = version;
    result.metadata.version_source = source;
    let identity = sha256(format!("{}\0{}", result.component_id, result.output_root));
    result.manifest_path = format!(
        ".ici/rust-v1/python-artifacts/{}.json",
        identity.trim_start_matches("sha256:")
    );
    result.output_paths = result
        .files
        .iter()
        .map(|file| file.path.clone())
        .chain(std::iter::once(result.manifest_path.clone()))
        .collect();
    if result
        .output_paths
        .iter()
        .any(|path| result.inputs.contains_key(path))
    {
        return Err("resolved Python artifact output collides with a captured input".into());
    }
    result.task.input_refs = result
        .inputs
        .keys()
        .map(|path| result.root.join(path).to_string_lossy().into_owned())
        .collect();
    result.task.output_specs = result
        .output_paths
        .iter()
        .map(|path| result.root.join(path).to_string_lossy().into_owned())
        .collect();
    result.task.work_dirs = vec![
        result
            .root
            .join(&result.output_root)
            .to_string_lossy()
            .into_owned(),
        result
            .root
            .join(".ici/rust-v1/python-artifacts")
            .to_string_lossy()
            .into_owned(),
    ];
    result.task.resource_keys = vec![
        format!("project-execution:{}", result.task.cwd),
        format!(
            "python-artifact-root:{}",
            result.root.join(&result.output_root).display()
        ),
    ];
    result.task.argv.extend([
        "--project-version".into(),
        result.metadata.version.clone(),
        "--version-source".into(),
        result.metadata.version_source.clone(),
    ]);
    result.version_probe_evidence = Some(
        json!({"task_id":outcome.task_id,"state":outcome.state,"exit_code":outcome.exit_code,"timed_out":outcome.timed_out,"truncated":outcome.truncated,"signal":outcome.signal,"source":result.metadata.version_source,"version":result.metadata.version,"tool_digest":probe.tool_digest}),
    );
    Ok(result)
}
