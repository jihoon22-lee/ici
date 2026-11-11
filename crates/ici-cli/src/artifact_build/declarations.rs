//! Read-only declared wheel/report publication, independent of artifact ownership.
use super::*;

#[derive(Clone, Debug)]
pub(super) struct Declarations {
    component: Component,
    /// (TOML key, kind, pattern), keys in sorted order, patterns in declared order.
    patterns: Vec<(String, String, String)>,
    candidates: BTreeMap<String, String>,
    /// Where each key was declared: project-relative file and line.
    locations: BTreeMap<String, (String, u32)>,
    /// (key, pattern) whose pattern named a link leaving the workspace or not ending at a file:
    /// refused before selection, never a measured no-match.
    refused: BTreeSet<(String, String)>,
    /// The legacy schema keeps frozen `_declared_artifact_paths`: a path an earlier pattern
    /// already recorded is skipped and does not count as a match.
    legacy: bool,
}
/// The declared selection: kept paths and their kinds, and every (key, pattern) that matched
/// nothing (frozen `unmatched`).
#[derive(Clone, Debug, Default)]
pub(super) struct Selection {
    pub(super) selected: BTreeMap<String, String>,
    pub(super) unmatched: Vec<(String, String)>,
}
/// Frozen `ValueError(f"declared artifact glob matched nothing: {'; '.join(unmatched)}")`, each
/// entry `f"{key}: {pattern}"` with the configuration key (`python_wheel: dist/*.whl`).
pub(super) fn no_match(unmatched: &[(String, String)]) -> Option<String> {
    (!unmatched.is_empty()).then(|| {
        format!(
            "declared artifact glob matched nothing: {}",
            unmatched
                .iter()
                .map(|(key, pattern)| format!("{key}: {pattern}"))
                .collect::<Vec<_>>()
                .join("; ")
        )
    })
}
impl Declarations {
    pub(super) fn collect(
        project: &ResolvedProject,
        component: &Component,
    ) -> Result<Self, String> {
        let mut result = Self {
            component: component.clone(),
            patterns: vec![],
            candidates: BTreeMap::new(),
            locations: BTreeMap::new(),
            refused: BTreeSet::new(),
            legacy: project.schema_family == ici_config::SchemaFamily::Legacy,
        };
        result.component.root = ".".into();
        if let Some(table) = project.effective.pointer("/build/artifacts") {
            let table = table.as_object().ok_or("build.artifacts must be a table")?;
            for (key, values) in table {
                let origin = project
                    .origins
                    .get(&format!("build.artifacts.{key}"))
                    .or_else(|| project.origins.get("build.artifacts"));
                let file = origin.map_or_else(
                    || "ici.toml".to_owned(),
                    |origin| {
                        Path::new(&origin.file)
                            .strip_prefix(&project.root)
                            .map_or_else(
                                |_| origin.file.clone(),
                                |path| path.to_string_lossy().into_owned(),
                            )
                    },
                );
                let line =
                    origin.map_or(1, |origin| u32::try_from(origin.line.max(1)).unwrap_or(1));
                result.locations.insert(key.clone(), (file, line));
                let kind = match key.as_str() {
                    "python_wheel" => "python-wheel",
                    "report" => "report",
                    _ => return Err(format!("unknown declared artifact kind: {key}")),
                };
                let values = values
                    .as_array()
                    .filter(|values| values.len() <= 32)
                    .ok_or("declared artifact patterns must contain at most 32 values")?;
                for value in values {
                    let pattern = value
                        .as_str()
                        .ok_or("declared artifact pattern must be a string")?;
                    ici_config::matching_source_paths(&result.component, &[], &[pattern.into()])
                        .map_err(|error| error.to_string())?;
                    result
                        .patterns
                        .push((key.clone(), kind.into(), pattern.into()));
                    let prefix = pattern
                        .split('/')
                        .take_while(|part| !part.chars().any(|c| "*?[{".contains(c)))
                        .collect::<Vec<_>>()
                        .join("/");
                    let start = project.root.join(&prefix);
                    let mut pending = vec![(start, 0usize)];
                    let mut inspected = 0usize;
                    while let Some((path, depth)) = pending.pop() {
                        inspected += 1;
                        if inspected > 32768 || depth > 64 {
                            return Err(
                                "declared artifact inventory exceeds 32768 entries or depth 64"
                                    .into(),
                            );
                        }
                        let metadata = match std::fs::symlink_metadata(&path) {
                            Ok(value) => value,
                            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                            Err(error) => return Err(error.to_string()),
                        };
                        if metadata.is_dir() {
                            let mut entries = std::fs::read_dir(&path)
                                .map_err(|error| error.to_string())?
                                .take(32769)
                                .collect::<Result<Vec<_>, _>>()
                                .map_err(|error| error.to_string())?;
                            if entries.len() > 32768 {
                                return Err(
                                    "declared artifact directory exceeds 32768 entries".into()
                                );
                            }
                            entries.sort_by_key(|entry| entry.file_name());
                            for entry in entries.into_iter().rev() {
                                pending.push((entry.path(), depth + 1));
                            }
                        } else if metadata.is_file() || metadata.file_type().is_symlink() {
                            let alias = relative(&project.root, &path)?;
                            if ici_config::matching_source_paths(
                                &result.component,
                                std::slice::from_ref(&alias),
                                &[pattern.into()],
                            )
                            .map_err(|error| error.to_string())?
                            .is_empty()
                            {
                                continue;
                            }
                            let canonical = match path.canonicalize() {
                                Ok(path) if path.starts_with(&project.root) && path.is_file() => {
                                    path
                                }
                                _ => {
                                    result.refused.insert((key.clone(), pattern.into()));
                                    continue;
                                }
                            };
                            result
                                .candidates
                                .insert(alias, relative(&project.root, &canonical)?);
                            if result.candidates.len() > 512 {
                                return Err("declared artifacts exceed 512 records".into());
                            }
                        }
                    }
                }
            }
        }
        Ok(result)
    }
    /// Native policy (schema 1/2): an unmatched pattern that named a refused link stays a
    /// refusal (`Err`, INCOMPLETE), not the measured no-match; frozen legacy reports both as
    /// `matched nothing`.
    pub(super) fn refusal(&self, unmatched: &[(String, String)]) -> Result<(), String> {
        match no_match(unmatched) {
            Some(reason)
                if !self.legacy && unmatched.iter().any(|entry| self.refused.contains(entry)) =>
            {
                Err(reason)
            }
            _ => Ok(()),
        }
    }
    pub(super) fn is_empty(&self) -> bool {
        self.patterns.is_empty()
    }
    pub(super) fn input_paths(&self) -> Vec<String> {
        self.candidates
            .values()
            .cloned()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    }
    /// Only frozen aliases and planned generated paths participate; no filesystem lookup here.
    pub(super) fn select(
        &self,
        generated: &[OutputFile],
        allow_unmatched: bool,
    ) -> Result<BTreeMap<String, String>, String> {
        let selection = self.selection(generated)?;
        match no_match(&selection.unmatched) {
            Some(reason) if !allow_unmatched => Err(reason),
            _ => Ok(selection.selected),
        }
    }
    /// Every declared pattern over the project's own files and this run's planned outputs.
    pub(super) fn selection(&self, generated: &[OutputFile]) -> Result<Selection, String> {
        let mut candidates = self.candidates.clone();
        candidates.extend(
            generated
                .iter()
                .map(|file| (file.path.clone(), file.path.clone())),
        );
        let aliases = candidates.keys().cloned().collect::<Vec<_>>();
        let mut result = Selection::default();
        for (key, kind, pattern) in &self.patterns {
            let matches = ici_config::matching_source_paths(
                &self.component,
                &aliases,
                std::slice::from_ref(pattern),
            )
            .map_err(|error| error.to_string())?;
            let mut matched = false;
            for alias in matches {
                let path = &candidates[&alias];
                match result.selected.get(path) {
                    Some(_) if self.legacy => continue,
                    Some(prior) if prior != kind => {
                        return Err(format!("declared artifact has conflicting kinds: {path}"));
                    }
                    _ => {}
                }
                matched = true;
                result.selected.insert(path.clone(), kind.clone());
            }
            if !matched {
                // Frozen names the configuration key, not the kind (`python_wheel: dist/*.whl`).
                result.unmatched.push((key.clone(), pattern.clone()));
            }
        }
        if result.selected.len() > 512 {
            return Err("declared artifacts exceed 512 records".into());
        }
        Ok(result)
    }
}

pub(super) fn publish(
    plan: &ArtifactBuildPlan,
    snapshot: &InputSnapshot,
    output: &mut AnalysisOutput,
) -> Result<(), String> {
    if plan.declared.is_empty() {
        return Ok(());
    }
    let paths = plan.declared.keys().cloned().collect::<Vec<_>>();
    let current = InputSnapshot::take(&plan.root, &paths, &[], artifact_limits())
        .map_err(|error| error.to_string())?;
    let mut artifacts = vec![];
    for (path, kind) in &plan.declared {
        let file = &current.files[path];
        let generated = plan.files.iter().find(|candidate| &candidate.path == path);
        let expected = match generated.map(|file| &file.content) {
            Some(Content::Generated(bytes)) => sha256(bytes),
            Some(Content::Source(source)) => snapshot.files[source].digest.clone(),
            None => plan
                .inputs
                .get(path)
                .cloned()
                .ok_or_else(|| format!("declared artifact was not captured: {path}"))?,
        };
        if file.digest != expected {
            return Err(format!("declared artifact changed after capture: {path}"));
        }
        let mode = std::fs::symlink_metadata(plan.root.join(path))
            .map_err(|error| error.to_string())?
            .permissions()
            .mode()
            & 0o777;
        artifacts.push(json!({"path":path,"kind":kind,"sha256":file.digest,"size":file.input.bytes.len(),"mode":mode,"artifact_id":format!("release:project:{path}"),"target":Path::new(path).file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default(),"scope":"project","owned":false}));
    }
    if plan.legacy {
        // Frozen `ArtifactManifest.create(producer="config.artifacts")` with the adapters'
        // `variant:scope:path` id and file-name target (`ici.artifacts/v2` records); the
        // projection binds it to the run identity after any adapter manifest.
        let records: Vec<_> = artifacts
            .iter()
            .map(|record| {
                json!({"path":record["path"],"scope":"project","kind":record["kind"],
                    "sha256":record["sha256"],"size":record["size"],"mode":record["mode"],
                    "producer":"config.artifacts","id":record["artifact_id"],
                    "target":record["target"],"command":[]})
            })
            .collect();
        let manifest = json!({"shadow_root":null,"variant":"release","artifacts":records});
        match output.extra.get_mut("legacy_artifact_manifests") {
            Some(serde_json::Value::Array(manifests)) => manifests.push(manifest),
            _ => output.extra["legacy_artifact_manifests"] = json!([manifest]),
        }
    }
    output.extra["artifact_manifests"].as_array_mut().expect("generated manifest array").push(json!({"producer":"config.artifacts","variant":"release","scope":"project","artifacts":artifacts}));
    let mut target = InspectionTarget::at(".", 1);
    target.target_name = "DeclaredArtifacts".into();
    target.message = format!("Recorded {} declared artifact(s)", plan.declared.len());
    output.targets.push(target);
    Ok(())
}

/// Native policy for schema 1 and 2 (root ruling R-3, 2026-11-11; no frozen counterpart): a
/// declaration evaluated completely over the project's files and this run's planned outputs
/// whose pattern matched nothing is a measured contract violation. The check ran to completion
/// (SUCCEEDED, complete evidence) with one blocking finding per unmatched pattern at the line
/// that declares it; nothing is written.
pub(super) fn unmatched_contract(plan: &ArtifactBuildPlan, output: &mut AnalysisOutput) {
    let reason = no_match(&plan.declared_unmatched).unwrap_or_default();
    output.observation.state = TaskState::Succeeded;
    output
        .observation
        .measurements
        .push(ici_domain::Measurement {
            name: "artifacts.declared_unmatched".into(),
            value: plan.declared_unmatched.len() as f64,
            evidence: ici_domain::EvidenceLevel::Measured,
            unit: "globs".into(),
            numerator: None,
            denominator: None,
        });
    for (key, pattern) in &plan.declared_unmatched {
        let (file, line) = plan
            .declarations
            .locations
            .get(key)
            .cloned()
            .unwrap_or_else(|| ("ici.toml".into(), 1));
        let message = format!("declared artifact glob matched nothing: {key}: {pattern}");
        let mut target = InspectionTarget::at(&file, line);
        target.target_name = "DeclaredArtifacts".into();
        target.status = "FAIL".into();
        target.message = message.clone();
        output.targets.push(target);
        let mut finding = ici_domain::Finding::new(
            "artifact.declared-unmatched",
            &message,
            ici_domain::SourceSpan::line(&file, line),
            &output.observation.provider,
        );
        finding.fingerprint = sha256(format!("artifact.declared-unmatched:{key}:{pattern}"));
        finding.severity = "high".into();
        finding.confidence = "high".into();
        finding.evidence = ici_domain::EvidenceLevel::Measured;
        output.observation.findings.push(finding);
    }
    output.status = "FAIL".into();
    output.summary = format!("Declared artifacts could not be recorded: {reason}");
}
