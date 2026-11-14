use ici_domain::AnalysisOutput;

#[derive(Debug)]
pub enum PreparationFailure {
    Incomplete(String),
    Observed {
        phase: String,
        output: Box<AnalysisOutput>,
    },
}
impl PreparationFailure {
    pub fn observed_for(&self, task_id: &str) -> Option<AnalysisOutput> {
        let Self::Observed { output, .. } = self else {
            return None;
        };
        let mut output = (**output).clone();
        output.observation.task_id = task_id.into();
        for finding in &mut output.observation.findings {
            finding.task_id = Some(task_id.into());
        }
        Some(output)
    }
}
impl std::fmt::Display for PreparationFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Incomplete(reason) => formatter.write_str(reason),
            Self::Observed { phase, output } => write!(
                formatter,
                "{phase}: {}",
                if output.summary.is_empty() {
                    output
                        .targets
                        .first()
                        .map(|target| target.message.as_str())
                        .unwrap_or("native compiler observation did not complete")
                } else {
                    &output.summary
                }
            ),
        }
    }
}
impl std::error::Error for PreparationFailure {}
impl From<String> for PreparationFailure {
    fn from(reason: String) -> Self {
        Self::Incomplete(reason)
    }
}

/// Why no native preparation plan exists for a component variant.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PreparationUnavailable {
    /// No declared build and no selectable root descriptor: there is nothing to prepare, so a
    /// legacy direct-compile fallback (or a visible limitation) applies.
    NothingToPrepare(String),
    /// A declared or explicitly enabled build exists but is misconfigured or unavailable. The
    /// variant's dependent checks are blocked with this located reason instead of falling back.
    Misconfigured {
        reason: String,
        /// Project-relative native descriptor the reason refers to, when one was selected.
        descriptor: Option<String>,
    },
}
impl std::fmt::Display for PreparationUnavailable {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NothingToPrepare(reason) | Self::Misconfigured { reason, .. } => {
                formatter.write_str(reason)
            }
        }
    }
}
