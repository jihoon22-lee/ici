//! Cache computation outputs, retaining real inspected locations. Policy is re-evaluated per run.
use ici_application::{
    cache::{CacheIdentity, ObservationCache},
    identity,
    snapshot::InputSnapshot,
};
use ici_domain::{AnalysisRequest, TaskSpec, sha256};
use ici_execution::Runner;
use ici_providers::ProviderPlan;
use serde_json::json;
use std::path::Path;

pub struct RunCache {
    pub store: ObservationCache,
    policy: String,
}
impl RunCache {
    pub fn new(root: &Path, core: Option<&str>, policy: &str, disabled: bool) -> Option<Self> {
        if disabled {
            return None;
        }
        Some(Self {
            store: ObservationCache {
                directory: root.join(".ici/rust-v1/observations"),
                core_digest: core?.into(),
            },
            policy: policy.into(),
        })
    }
    pub fn internal_key(
        &self,
        request: &AnalysisRequest,
        root: &Path,
        snapshot: &InputSnapshot,
    ) -> ici_application::Result<Option<String>> {
        // Compiler-derived facets need their explicit context/fact identity before becoming reusable.
        if request.language != "python" {
            return Ok(None);
        }
        let mut task = TaskSpec::command(
            &request.task_id,
            "ici",
            vec!["ici.native.analysis".into(), request.check_id.clone()],
            root.to_string_lossy(),
        );
        task.cacheable = true;
        task.tool_digest = Some(self.store.core_digest.clone());
        task.input_refs = request
            .inputs
            .iter()
            .map(|input| input.path.clone())
            .collect();
        CacheIdentity{core_digest:self.store.core_digest.clone(),policy_digest:self.policy.clone(),environment_digest:sha256("ici.pure-native-analysis.v1"),interpretation_digest:identity::digest(&json!({"check":request.check_id,"component":request.component_id,"language":request.language,"target":request.target_version,"options":request.options}))?,input_digests:snapshot.files.iter().map(|(path,file)|(path.clone(),file.digest.clone())).collect(),inputs_complete:true}.key(&task)
    }
    pub fn provider_key(
        &self,
        provider: &ProviderPlan,
        root: &Path,
        snapshot: &InputSnapshot,
        runner: &Runner,
    ) -> ici_application::Result<Option<String>> {
        if !provider.scope_complete {
            return Ok(None);
        }
        runner.verify_tool(&provider.task).map_err(|error| {
            ici_application::ApplicationError(format!("tool identity before reuse: {error}"))
        })?;
        CacheIdentity {
            core_digest: self.store.core_digest.clone(),
            policy_digest: self.policy.clone(),
            environment_digest: runner.environment_digest(&provider.task),
            interpretation_digest: identity::digest(provider)?,
            input_digests: snapshot
                .files
                .iter()
                .map(|(path, file)| {
                    (
                        root.join(path).to_string_lossy().into_owned(),
                        file.digest.clone(),
                    )
                })
                .collect(),
            inputs_complete: true,
        }
        .key(&provider.task)
    }
}
