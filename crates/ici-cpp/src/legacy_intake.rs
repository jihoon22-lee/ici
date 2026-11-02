//! Frozen v0.13.0 `read_analysis_sources` (`analysis/_source_inputs.py`) for the legacy C++
//! dead-code and duplicate-code projections: one fail-closed pass over the request bytes in
//! project-relative path order that stops at the first intake fault with the frozen
//! `AnalysisSourceError` code, location and message. Modern requests never use it.
use ici_domain::{AnalysisOutput, AnalysisRequest, InspectionTarget, TaskState};

/// Frozen `MAX_ANALYSIS_SOURCE_CANDIDATES`.
pub const LEGACY_MAX_CANDIDATES: usize = 8_192;
/// Frozen `MAX_ANALYSIS_SOURCE_FILES`.
pub const LEGACY_MAX_FILES: usize = 2_048;
/// Frozen `MAX_ANALYSIS_SOURCE_BYTES` (per file).
pub const LEGACY_MAX_FILE_BYTES: usize = 8 * 1024 * 1024;
/// Frozen `MAX_ANALYSIS_INVENTORY_BYTES` (all owned files).
pub const LEGACY_MAX_TOTAL_BYTES: usize = 64 * 1024 * 1024;

/// One frozen `AnalysisSourceError`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LegacyIntakeError {
    pub file_path: String,
    pub code: &'static str,
    pub message: String,
}

impl LegacyIntakeError {
    fn new(file_path: &str, code: &'static str, message: impl Into<String>) -> Self {
        Self {
            file_path: file_path.into(),
            code,
            message: message.into(),
        }
    }
}

/// `PurePath.suffix` of the final component (empty for a dotfile or a name without a dot).
fn suffix(path: &str) -> &str {
    let name = path.rsplit('/').next().unwrap_or(path);
    name.rfind('.')
        .filter(|index| *index > 0 && *index + 1 < name.len())
        .map_or("", |index| &name[index..])
}

/// Frozen `_LANGUAGES` suffixes a C/C++ intake accepts (`.py` belongs to the Python scope).
fn supported(path: &str) -> bool {
    matches!(
        suffix(path).to_ascii_lowercase().as_str(),
        ".c" | ".cc" | ".cpp" | ".cxx" | ".h" | ".hh" | ".hpp" | ".hxx" | ".moc" | ".py"
    )
}

/// The first frozen intake fault over `request.inputs`, or `None` when every owned source is a
/// bounded UTF-8 text without NUL. Generated/vendor sources excluded by the request's
/// `include_generated` / `include_vendor` policy are never read, like frozen.
pub fn legacy_source_intake(request: &AnalysisRequest) -> Option<LegacyIntakeError> {
    legacy_source_intake_inputs(&request.inputs, &request.options)
}

/// [`legacy_source_intake`] over explicit inputs and request-shaped options
/// (`include_generated`, `include_vendor`).
pub fn legacy_source_intake_inputs(
    inputs: &[ici_domain::SourceInput],
    options: &serde_json::Value,
) -> Option<LegacyIntakeError> {
    let blocked = |path: &str| {
        ici_analysis::source_exclusion_reasons(path)
            .into_iter()
            .any(|reason| {
                !options
                    .get(format!("include_{reason}"))
                    .and_then(serde_json::Value::as_bool)
                    .unwrap_or(false)
            })
    };
    let mut selected: Vec<&ici_domain::SourceInput> = vec![];
    let mut seen = std::collections::BTreeSet::new();
    for input in inputs {
        if !seen.insert(input.path.as_str()) {
            continue;
        }
        selected.push(input);
        if selected.len() > LEGACY_MAX_CANDIDATES {
            return Some(LegacyIntakeError::new(
                ".",
                "too-many-candidates",
                format!(
                    "Analysis source candidate count exceeds the bounded limit ({LEGACY_MAX_CANDIDATES})"
                ),
            ));
        }
    }
    selected.sort_by(|left, right| left.path.cmp(&right.path));
    let mut owned = 0usize;
    let mut total = 0usize;
    for input in selected {
        if blocked(&input.path) {
            continue;
        }
        if owned >= LEGACY_MAX_FILES {
            return Some(LegacyIntakeError::new(
                ".",
                "too-many-files",
                format!(
                    "Owned analysis source count exceeds the bounded limit ({LEGACY_MAX_FILES})"
                ),
            ));
        }
        if !supported(&input.path) {
            let suffix = suffix(&input.path);
            return Some(LegacyIntakeError::new(
                &input.path,
                "unsupported-language",
                format!(
                    "Unsupported analysis source type: {}",
                    if suffix.is_empty() { "<none>" } else { suffix }
                ),
            ));
        }
        if input.bytes.len() > LEGACY_MAX_FILE_BYTES {
            return Some(LegacyIntakeError::new(
                &input.path,
                "too-large",
                "Analysis source could not be read safely: The file exceeds the bounded input size.",
            ));
        }
        total += input.bytes.len();
        if total > LEGACY_MAX_TOTAL_BYTES {
            return Some(LegacyIntakeError::new(
                &input.path,
                "inventory-too-large",
                format!(
                    "Analysis source inventory exceeds the bounded limit ({LEGACY_MAX_TOTAL_BYTES} bytes)"
                ),
            ));
        }
        match std::str::from_utf8(&input.bytes) {
            Err(_) => {
                return Some(LegacyIntakeError::new(
                    &input.path,
                    "invalid-utf8",
                    "Analysis source is not valid UTF-8",
                ));
            }
            Ok(text) if text.contains('\0') => {
                return Some(LegacyIntakeError::new(
                    &input.path,
                    "invalid-text",
                    "Analysis source contains a NUL character",
                ));
            }
            Ok(_) => {}
        }
        owned += 1;
    }
    None
}

/// Frozen engine result for an intake fault (`DeadCodeEngine._collect_sources`,
/// `DuplicateEngine`): only the `SourceInputError` target, ERROR NOT_RUN, the error message as
/// the summary and no finding.
pub fn legacy_intake_output(
    mut output: AnalysisOutput,
    error: &LegacyIntakeError,
) -> AnalysisOutput {
    let mut target = InspectionTarget::at(&error.file_path, 1);
    target.target_name = "SourceInputError".into();
    target.status = "ERROR".into();
    target.message = format!("{}: {}", error.code, error.message);
    output.targets = vec![target];
    output.observation.findings.clear();
    output.summary = error.message.clone();
    output.status = "ERROR".into();
    output.observation.state = TaskState::Failed;
    output.observation.limitations.push(error.message.clone());
    output
}

#[cfg(test)]
mod tests {
    use super::*;
    use ici_domain::SourceInput;

    fn request(inputs: Vec<(&str, Vec<u8>)>) -> AnalysisRequest {
        let mut request: AnalysisRequest = serde_json::from_value(serde_json::json!({
            "task_id": "t", "check_id": "cpp.dead", "component_id": "c", "language": "cpp",
            "inputs": [], "options": {}, "target_version": ""
        }))
        .unwrap();
        request.inputs = inputs
            .into_iter()
            .map(|(path, bytes)| SourceInput {
                path: path.into(),
                bytes,
            })
            .collect();
        request
    }

    #[test]
    fn legacy_intake_stops_at_the_first_fault_in_path_order() {
        let ok = b"int f() { return 0; }\n".to_vec();
        assert_eq!(
            legacy_source_intake(&request(vec![("src/a.cpp", ok.clone())])),
            None
        );
        // Path order, not request order; excluded generated sources are never read.
        let error = legacy_source_intake(&request(vec![
            ("src/z.cpp", b"\xe9\n".to_vec()),
            ("src/b.hpp", b"a\0b".to_vec()),
            ("src/moc_a.cpp", b"\xff".to_vec()),
            ("src/a.cpp", ok.clone()),
        ]))
        .unwrap();
        assert_eq!(
            (error.file_path.as_str(), error.code, error.message.as_str()),
            (
                "src/b.hpp",
                "invalid-text",
                "Analysis source contains a NUL character"
            )
        );
        let error =
            legacy_source_intake(&request(vec![("src/z.cpp", b"\xe9\n".to_vec())])).unwrap();
        assert_eq!(
            (error.code, error.message.as_str()),
            ("invalid-utf8", "Analysis source is not valid UTF-8")
        );
    }

    #[test]
    fn legacy_intake_applies_the_frozen_bounds() {
        let big = vec![b'a'; LEGACY_MAX_FILE_BYTES + 1];
        let error = legacy_source_intake(&request(vec![("src/a.cpp", big)])).unwrap();
        assert_eq!(
            (error.file_path.as_str(), error.code),
            ("src/a.cpp", "too-large")
        );
        let edge = vec![b'a'; LEGACY_MAX_FILE_BYTES];
        assert_eq!(
            legacy_source_intake(&request(vec![("src/a.cpp", edge.clone())])),
            None
        );
        let files: Vec<_> = (0..9)
            .map(|index| (format!("src/{index}.cpp"), edge.clone()))
            .collect();
        let error = legacy_source_intake(&request(
            files
                .iter()
                .map(|(path, bytes)| (path.as_str(), bytes.clone()))
                .collect(),
        ))
        .unwrap();
        assert_eq!(
            (error.file_path.as_str(), error.code, error.message.as_str()),
            (
                "src/8.cpp",
                "inventory-too-large",
                "Analysis source inventory exceeds the bounded limit (67108864 bytes)"
            )
        );
        let names: Vec<_> = (0..=LEGACY_MAX_FILES)
            .map(|index| format!("src/{index:05}.cpp"))
            .collect();
        let error = legacy_source_intake(&request(
            names.iter().map(|path| (path.as_str(), vec![])).collect(),
        ))
        .unwrap();
        assert_eq!(
            (error.file_path.as_str(), error.code),
            (".", "too-many-files")
        );
        let names: Vec<_> = (0..=LEGACY_MAX_CANDIDATES)
            .map(|index| format!("third_party/{index:05}.cpp"))
            .collect();
        let error = legacy_source_intake(&request(
            names.iter().map(|path| (path.as_str(), vec![])).collect(),
        ))
        .unwrap();
        assert_eq!(
            (error.file_path.as_str(), error.code),
            (".", "too-many-candidates")
        );
        let error = legacy_source_intake(&request(vec![("src/a.txt", vec![])])).unwrap();
        assert_eq!(
            (error.code, error.message.as_str()),
            (
                "unsupported-language",
                "Unsupported analysis source type: .txt"
            )
        );
    }
}
