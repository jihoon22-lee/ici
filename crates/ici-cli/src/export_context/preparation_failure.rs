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
