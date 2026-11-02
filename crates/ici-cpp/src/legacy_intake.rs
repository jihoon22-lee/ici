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

/// One intake candidate: loads its bytes only after the frozen size bounds passed.
trait Candidate {
    fn path(&self) -> &str;
    /// Frozen `_read_bounded_regular`: open, type and size checks (`size_checked` applies the
    /// per-file and remaining-inventory bounds before any byte is read), then the payload.
    /// Returns the bounded size the inventory accounts and the payload of that size.
    fn load(&self, remaining: usize) -> Result<Loaded<'_>, LegacyIntakeError>;
}

type Loaded<'a> = (usize, std::borrow::Cow<'a, [u8]>);

/// Frozen per-file (`too-large`) then inventory (`inventory-too-large`) bound for a payload of
/// `size` bytes when `remaining` inventory bytes are left.
fn size_checked(path: &str, size: u64, remaining: usize) -> Result<usize, LegacyIntakeError> {
    if size > LEGACY_MAX_FILE_BYTES as u64 {
        return Err(LegacyIntakeError::new(
            path,
            "too-large",
            "Analysis source could not be read safely: The file exceeds the bounded input size.",
        ));
    }
    if size > remaining as u64 {
        return Err(LegacyIntakeError::new(
            path,
            "inventory-too-large",
            format!(
                "Analysis source inventory exceeds the bounded limit ({LEGACY_MAX_TOTAL_BYTES} bytes)"
            ),
        ));
    }
    Ok(size as usize)
}

impl Candidate for &ici_domain::SourceInput {
    fn path(&self) -> &str {
        &self.path
    }
    fn load(&self, remaining: usize) -> Result<Loaded<'_>, LegacyIntakeError> {
        let size = size_checked(&self.path, self.bytes.len() as u64, remaining)?;
        Ok((size, std::borrow::Cow::Borrowed(&self.bytes)))
    }
}

/// A project file read through a no-follow, non-blocking descriptor (frozen
/// `_read_bounded_regular`): the size bounds are decided from `fstat` before reading and at
/// most one bounded payload is held at a time.
struct FileCandidate<'a> {
    root: &'a std::path::Path,
    path: &'a str,
}

impl Candidate for FileCandidate<'_> {
    fn path(&self) -> &str {
        self.path
    }
    fn load(&self, remaining: usize) -> Result<Loaded<'_>, LegacyIntakeError> {
        use std::io::Read;
        use std::os::unix::fs::OpenOptionsExt;
        let unsafe_read = |code: &'static str, detail: &str| {
            LegacyIntakeError::new(
                self.path,
                code,
                format!("Analysis source could not be read safely: {detail}"),
            )
        };
        let file = match std::fs::OpenOptions::new()
            .read(true)
            .custom_flags(nix::libc::O_NOFOLLOW | nix::libc::O_NONBLOCK | nix::libc::O_CLOEXEC)
            .open(self.root.join(self.path))
        {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Err(LegacyIntakeError::new(
                    self.path,
                    "missing",
                    "Analysis source disappeared before it could be read",
                ));
            }
            Err(_) => {
                return Err(unsafe_read(
                    "unreadable",
                    "The file could not be opened safely.",
                ));
            }
        };
        let metadata = file
            .metadata()
            .map_err(|_| unsafe_read("unreadable", "The file could not be opened safely."))?;
        if !metadata.is_file() {
            return Err(unsafe_read(
                "not-file",
                "The selected path is not a regular file.",
            ));
        }
        let size = size_checked(self.path, metadata.len(), remaining)?;
        let mut bytes = Vec::with_capacity(size);
        file.take(size as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| unsafe_read("unreadable", "The file could not be opened safely."))?;
        if bytes.len() != size {
            return Err(unsafe_read(
                "changed",
                "The file changed while it was being read.",
            ));
        }
        Ok((size, std::borrow::Cow::Owned(bytes)))
    }
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
    intake(inputs.iter(), options)
}

/// [`legacy_source_intake`] over project files under `root` (project-relative `paths`), read
/// one at a time with every frozen bound applied before the bytes are read: candidates are
/// counted before any I/O, excluded generated/vendor paths are never opened, and only the
/// current bounded payload is held.
pub fn legacy_source_intake_files(
    root: &std::path::Path,
    paths: &[String],
    options: &serde_json::Value,
) -> Option<LegacyIntakeError> {
    intake(
        paths.iter().map(|path| FileCandidate { root, path }),
        options,
    )
}

fn intake<C: Candidate>(
    candidates: impl IntoIterator<Item = C>,
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
    let mut selected: Vec<C> = vec![];
    let mut seen = std::collections::BTreeSet::new();
    for candidate in candidates {
        if !seen.insert(candidate.path().to_owned()) {
            continue;
        }
        selected.push(candidate);
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
    selected.sort_by(|left, right| left.path().cmp(right.path()));
    let mut owned = 0usize;
    let mut total = 0usize;
    for candidate in selected {
        let path = candidate.path();
        if blocked(path) {
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
        if !supported(path) {
            let suffix = suffix(path);
            return Some(LegacyIntakeError::new(
                path,
                "unsupported-language",
                format!(
                    "Unsupported analysis source type: {}",
                    if suffix.is_empty() { "<none>" } else { suffix }
                ),
            ));
        }
        let (size, bytes) = match candidate.load(LEGACY_MAX_TOTAL_BYTES - total) {
            Ok(loaded) => loaded,
            Err(error) => return Some(error),
        };
        total += size;
        match std::str::from_utf8(&bytes) {
            Err(_) => {
                return Some(LegacyIntakeError::new(
                    path,
                    "invalid-utf8",
                    "Analysis source is not valid UTF-8",
                ));
            }
            Ok(text) if text.contains('\0') => {
                return Some(LegacyIntakeError::new(
                    path,
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

    /// A candidate sized by a sparse file's metadata that records every payload read.
    struct Sparse<'a> {
        path: String,
        file: std::path::PathBuf,
        reads: &'a std::cell::RefCell<Vec<String>>,
    }
    impl Candidate for Sparse<'_> {
        fn path(&self) -> &str {
            &self.path
        }
        fn load(&self, remaining: usize) -> Result<Loaded<'_>, LegacyIntakeError> {
            let size = std::fs::metadata(&self.file).unwrap().len();
            let size = size_checked(&self.path, size, remaining)?;
            self.reads.borrow_mut().push(self.path.clone());
            Ok((size, std::borrow::Cow::Borrowed(b"int x;\n")))
        }
    }

    #[test]
    fn legacy_file_intake_bounds_the_inventory_before_reading() {
        let work = tempfile::tempdir().unwrap();
        // Nine sparse 7.5 MiB files (no disk use): the ninth would pass 64 MiB and is never read.
        let reads = std::cell::RefCell::new(vec![]);
        let candidates: Vec<_> = (0..9)
            .map(|index| {
                let file = work.path().join(format!("part{index}.cpp"));
                std::fs::File::create(&file)
                    .unwrap()
                    .set_len(15 * 512 * 1024)
                    .unwrap();
                Sparse {
                    path: format!("src/part{index}.cpp"),
                    file,
                    reads: &reads,
                }
            })
            .collect();
        let error = intake(candidates, &serde_json::Value::Null).unwrap();
        assert_eq!(
            (error.file_path.as_str(), error.code),
            ("src/part8.cpp", "inventory-too-large")
        );
        assert_eq!(reads.borrow().len(), 8);
        assert!(!reads.borrow().contains(&"src/part8.cpp".to_owned()));
        // More than 8192 candidates fail before any file is opened (none of these exist).
        let names: Vec<String> = (0..=LEGACY_MAX_CANDIDATES)
            .map(|index| format!("src/missing{index:05}.cpp"))
            .collect();
        let error =
            legacy_source_intake_files(work.path(), &names, &serde_json::Value::Null).unwrap();
        assert_eq!(
            (error.file_path.as_str(), error.code),
            (".", "too-many-candidates")
        );
        let error =
            legacy_source_intake_files(work.path(), &names[..1], &serde_json::Value::Null).unwrap();
        assert_eq!(error.code, "missing");
    }

    #[test]
    fn legacy_file_intake_never_opens_excluded_paths_and_rejects_special_files() {
        let work = tempfile::tempdir().unwrap();
        let root = work.path();
        std::fs::create_dir_all(root.join("src/third_party")).unwrap();
        // A FIFO under an excluded vendor directory is never opened.
        nix::unistd::mkfifo(
            &root.join("src/third_party/pipe.cpp"),
            nix::sys::stat::Mode::S_IRUSR | nix::sys::stat::Mode::S_IWUSR,
        )
        .unwrap();
        std::fs::write(root.join("src/ok.cpp"), b"int ok();\n").unwrap();
        let paths = vec![
            "src/ok.cpp".to_owned(),
            "src/third_party/pipe.cpp".to_owned(),
        ];
        assert_eq!(
            legacy_source_intake_files(root, &paths, &serde_json::Value::Null),
            None
        );
        // Included, the same FIFO is refused as a non-regular file without blocking.
        let error =
            legacy_source_intake_files(root, &paths, &serde_json::json!({"include_vendor": true}))
                .unwrap();
        assert_eq!(
            (error.file_path.as_str(), error.code, error.message.as_str()),
            (
                "src/third_party/pipe.cpp",
                "not-file",
                "Analysis source could not be read safely: The selected path is not a regular file."
            )
        );
        // A sparse file over 8 MiB is refused from its size alone.
        std::fs::File::create(root.join("src/big.cpp"))
            .unwrap()
            .set_len(LEGACY_MAX_FILE_BYTES as u64 + 1)
            .unwrap();
        let error =
            legacy_source_intake_files(root, &["src/big.cpp".to_owned()], &serde_json::Value::Null)
                .unwrap();
        assert_eq!(
            (error.file_path.as_str(), error.code),
            ("src/big.cpp", "too-large")
        );
        // A symlink is not followed.
        std::os::unix::fs::symlink(root.join("src/ok.cpp"), root.join("src/link.cpp")).unwrap();
        let error = legacy_source_intake_files(
            root,
            &["src/link.cpp".to_owned()],
            &serde_json::Value::Null,
        )
        .unwrap();
        assert_eq!(error.code, "unreadable");
    }
}
