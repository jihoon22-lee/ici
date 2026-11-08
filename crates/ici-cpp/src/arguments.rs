use crate::input::{read_contained, scoped};
use crate::{CppError, InputIdentity};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

pub const MAX_ARGUMENTS: usize = 32_768;
pub const MAX_ARGUMENT_CHARS: usize = 1024 * 1024;
pub const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;

/// Python shlex.split(posix=True, comments=False) semantics; parsing never executes shell syntax.
pub fn split_posix(command: &str) -> Result<Vec<String>, CppError> {
    if command.len() > 4 * 1024 * 1024 || command.contains('\0') {
        return Err(CppError::new(
            "invalid-command",
            "command exceeds bounded text contract",
        ));
    }
    let mut chars = command.chars().peekable();
    let mut quote = None;
    let mut words = vec![];
    let mut word = String::new();
    let mut started = false;
    while let Some(c) = chars.next() {
        match quote {
            Some('\'') => {
                if c == '\'' {
                    quote = None;
                } else {
                    word.push(c);
                }
            }
            Some('"') => {
                if c == '"' {
                    quote = None;
                } else if c == '\\' {
                    let next = chars
                        .next()
                        .ok_or_else(|| CppError::new("invalid-command", "unterminated escape"))?;
                    if next != '"' && next != '\\' {
                        word.push('\\');
                    }
                    word.push(next);
                } else {
                    word.push(c);
                }
            }
            _ => {
                if matches!(c, ' ' | '\t' | '\r' | '\n') {
                    if started {
                        words.push(std::mem::take(&mut word));
                        started = false;
                    }
                } else if c == '\'' || c == '"' {
                    quote = Some(c);
                    started = true;
                } else if c == '\\' {
                    word.push(
                        chars.next().ok_or_else(|| {
                            CppError::new("invalid-command", "unterminated escape")
                        })?,
                    );
                    started = true;
                } else {
                    word.push(c);
                    started = true;
                }
            }
        }
    }
    if quote.is_some() {
        return Err(CppError::new("invalid-command", "unclosed quote"));
    }
    if started {
        words.push(word);
    }
    Ok(words)
}

pub fn split_windows(command: &str) -> Result<Vec<String>, CppError> {
    if command.len() > 4 * 1024 * 1024 || command.contains('\0') {
        return Err(CppError::new(
            "invalid-command",
            "command exceeds bounded text contract",
        ));
    }
    let chars: Vec<_> = command.chars().collect();
    let mut i = 0;
    let mut result = vec![];
    while i < chars.len() {
        while i < chars.len() && matches!(chars[i], ' ' | '\t') {
            i += 1;
        }
        if i == chars.len() {
            break;
        }
        let mut word = String::new();
        let mut quoted = false;
        while i < chars.len() && (quoted || !matches!(chars[i], ' ' | '\t')) {
            let mut slashes = 0;
            while i < chars.len() && chars[i] == '\\' {
                slashes += 1;
                i += 1;
            }
            if i < chars.len() && chars[i] == '"' {
                for _ in 0..slashes / 2 {
                    word.push('\\');
                }
                if slashes % 2 == 1 {
                    word.push('"');
                } else if quoted && chars.get(i + 1) == Some(&'"') {
                    word.push('"');
                    i += 1;
                } else {
                    quoted = !quoted;
                }
                i += 1;
            } else {
                for _ in 0..slashes {
                    word.push('\\');
                }
                if i < chars.len() && (quoted || !matches!(chars[i], ' ' | '\t')) {
                    word.push(chars[i]);
                    i += 1;
                }
            }
        }
        if quoted {
            return Err(CppError::new("invalid-command", "unclosed quote"));
        }
        result.push(word);
    }
    Ok(result)
}

pub(crate) fn validate(argv: &[String]) -> Result<(), CppError> {
    if argv.is_empty()
        || argv.len() > MAX_ARGUMENTS
        || argv.iter().any(|v| v.is_empty() || v.contains('\0'))
        || argv.iter().map(|v| v.chars().count()).sum::<usize>() > MAX_ARGUMENT_CHARS
    {
        Err(CppError::new(
            "invalid-arguments",
            "compiler arguments must be bounded nonempty strings",
        ))
    } else {
        Ok(())
    }
}
/// One row's expanded argv, the response files it read and those it could not use.
#[derive(Default)]
pub(crate) struct Expansion {
    pub argv: Vec<String>,
    pub response_files: Vec<InputIdentity>,
    pub diagnostics: Vec<CppError>,
}
#[derive(Default)]
pub(crate) struct ResponseCache {
    files: BTreeMap<PathBuf, (Vec<String>, InputIdentity)>,
    failures: BTreeMap<PathBuf, CppError>,
    bytes: usize,
    current_chars: usize,
}
impl ResponseCache {
    /// Frozen `_expand_response_files`: a response file that cannot be used drops its own token
    /// and leaves a unit diagnostic; only the expansion bounds refuse the row.
    pub fn expand(
        &mut self,
        root: &Path,
        cwd: &Path,
        argv: &[String],
    ) -> Result<Expansion, CppError> {
        self.current_chars = 0;
        let mut active = vec![];
        let mut expansion = Expansion::default();
        let mut used = BTreeMap::new();
        self.walk(root, cwd, argv, &mut active, &mut expansion, &mut used)?;
        expansion.response_files = used.into_values().collect();
        Ok(expansion)
    }
    fn walk(
        &mut self,
        root: &Path,
        directory: &Path,
        argv: &[String],
        active: &mut Vec<PathBuf>,
        expansion: &mut Expansion,
        used: &mut BTreeMap<String, InputIdentity>,
    ) -> Result<(), CppError> {
        for token in argv {
            if let Some(raw) = token.strip_prefix('@') {
                match self.response_tokens(root, directory, raw, active) {
                    Ok((path, words, identity)) => {
                        used.insert(identity.path.clone(), identity);
                        let parent = path.parent().unwrap_or(directory).to_path_buf();
                        active.push(path);
                        self.walk(root, &parent, &words, active, expansion, used)?;
                        active.pop();
                    }
                    Err(error) => expansion.diagnostics.push(error),
                }
            } else {
                self.current_chars += token.chars().count();
                expansion.argv.push(token.clone());
                if self.current_chars > MAX_ARGUMENT_CHARS {
                    return Err(CppError::new(
                        "invalid-arguments",
                        "Expanded compilation arguments exceed the bounded character count.",
                    ));
                }
            }
            if expansion.argv.len() > MAX_ARGUMENTS {
                return Err(CppError::new(
                    "invalid-arguments",
                    "Expanded compilation arguments exceed the bounded count.",
                ));
            }
        }
        Ok(())
    }
    /// Frozen `_expand_response_token` up to the nested expansion, in its order of refusals.
    fn response_tokens(
        &mut self,
        root: &Path,
        directory: &Path,
        raw: &str,
        active: &[PathBuf],
    ) -> Result<(PathBuf, Vec<String>, InputIdentity), CppError> {
        if raw.is_empty() {
            return Err(CppError::new(
                "response-file-invalid",
                "A compiler response-file token has no path.",
            ));
        }
        if active.len() >= 4 {
            return Err(CppError::new(
                "response-file-depth",
                "Compiler response-file nesting exceeds the bounded depth.",
            ));
        }
        let (relative, scope, path) = scoped(root, directory, raw)?;
        if scope != "project" {
            return Err(CppError::new(
                "response-file-outside-project",
                "A compiler response file resolves outside the project.",
            ));
        }
        if active.contains(&path) {
            return Err(CppError::new(
                "response-file-cycle",
                "Compiler response files contain a recursive cycle.",
            ));
        }
        if let Some(failure) = self.failures.get(&path) {
            return Err(failure.clone());
        }
        if !self.files.contains_key(&path) {
            match self.load(root, &path, relative) {
                Ok(loaded) => {
                    self.files.insert(path.clone(), loaded);
                }
                Err(failure) => {
                    self.failures.insert(path, failure.clone());
                    return Err(failure);
                }
            }
        }
        let (words, identity) = self.files[&path].clone();
        Ok((path, words, identity))
    }
    /// Frozen `_load_response_tokens`.
    fn load(
        &mut self,
        root: &Path,
        path: &Path,
        relative: String,
    ) -> Result<(Vec<String>, InputIdentity), CppError> {
        let bytes = read_contained(root, path, MAX_RESPONSE_BYTES).map_err(|error| {
            if error.code == "missing" {
                CppError::new(
                    "response-file-missing",
                    "A compiler response file does not exist.",
                )
            } else {
                CppError::new(
                    format!("response-file-{}", error.code),
                    "A compiler response file could not be read safely.",
                )
            }
        })?;
        self.bytes += bytes.len();
        if self.bytes > MAX_RESPONSE_BYTES {
            return Err(CppError::new(
                "response-file-too-large",
                "The aggregate compiler response-file input is too large.",
            ));
        }
        let text = std::str::from_utf8(&bytes).map_err(|_| {
            CppError::new(
                "response-file-malformed",
                "A compiler response file could not be decoded safely.",
            )
        })?;
        let words = split_posix(text).map_err(|_| {
            CppError::new(
                "response-file-malformed",
                "A compiler response file could not be decoded safely.",
            )
        })?;
        if words.len() > MAX_ARGUMENTS
            || words.iter().any(|v| v.is_empty() || v.contains('\0'))
            || words.iter().map(|v| v.chars().count()).sum::<usize>() > MAX_ARGUMENT_CHARS
        {
            return Err(CppError::new(
                "response-file-malformed",
                "A compiler response file contains invalid or excessive arguments.",
            ));
        }
        let identity = InputIdentity {
            path: relative,
            sha256: ici_domain::sha256(&bytes),
            size: bytes.len() as u64,
        };
        Ok((words, identity))
    }
}
