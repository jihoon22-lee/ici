//! Read-only declared wheel/report publication, independent of artifact ownership.
use super::*;

#[derive(Clone, Debug)]
pub(super) struct Declarations {
    component: Component,
    patterns: Vec<(String, String)>,
    candidates: BTreeMap<String, String>,
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
        };
        result.component.root = ".".into();
        if let Some(table) = project.effective.pointer("/build/artifacts") {
            let table = table.as_object().ok_or("build.artifacts must be a table")?;
            for (key, values) in table {
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
                    result.patterns.push((kind.into(), pattern.into()));
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
                                _ => continue,
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
        let mut candidates = self.candidates.clone();
        candidates.extend(
            generated
                .iter()
                .map(|file| (file.path.clone(), file.path.clone())),
        );
        let aliases = candidates.keys().cloned().collect::<Vec<_>>();
        let mut selected = BTreeMap::new();
        for (kind, pattern) in &self.patterns {
            let matches = ici_config::matching_source_paths(
                &self.component,
                &aliases,
                std::slice::from_ref(pattern),
            )
            .map_err(|error| error.to_string())?;
            if matches.is_empty() && !allow_unmatched {
                return Err(format!(
                    "declared artifact glob matched nothing: {kind}: {pattern}"
                ));
            }
            for alias in matches {
                let path = &candidates[&alias];
                if selected.get(path).is_some_and(|prior| prior != kind) {
                    return Err(format!("declared artifact has conflicting kinds: {path}"));
                }
                selected.insert(path.clone(), kind.clone());
            }
        }
        if selected.len() > 512 {
            return Err("declared artifacts exceed 512 records".into());
        }
        Ok(selected)
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
        artifacts.push(json!({"path":path,"kind":kind,"sha256":file.digest,"size":file.input.bytes.len(),"mode":mode,"artifact_id":format!("release:project:{path}"),"target":Path::new(path).file_name().unwrap_or_default(),"scope":"project","owned":false}));
    }
    output.extra["artifact_manifests"].as_array_mut().expect("generated manifest array").push(json!({"producer":"config.artifacts","variant":"release","scope":"project","artifacts":artifacts}));
    let mut target = InspectionTarget::at(".", 1);
    target.target_name = "DeclaredArtifacts".into();
    target.message = format!("Recorded {} declared artifact(s)", plan.declared.len());
    output.targets.push(target);
    Ok(())
}
