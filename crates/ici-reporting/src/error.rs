#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
#[error("{0}")]
pub struct ReportError(pub String);
pub type Result<T> = std::result::Result<T, ReportError>;
impl From<serde_json::Error> for ReportError {
    fn from(error: serde_json::Error) -> Self {
        Self(format!("Invalid report JSON: {error}"))
    }
}
impl From<ici_domain::DomainError> for ReportError {
    fn from(error: ici_domain::DomainError) -> Self {
        Self(error.to_string())
    }
}
