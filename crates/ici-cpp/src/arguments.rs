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
#[derive(Default)]
pub(crate) struct ResponseCache {
    files: BTreeMap<PathBuf, (Vec<String>, InputIdentity)>,
    bytes: usize,
    current_chars: usize,
}
impl ResponseCache {
    pub fn expand(
        &mut self,
        root: &Path,
        cwd: &Path,
        argv: &[String],
    ) -> Result<(Vec<String>, Vec<InputIdentity>), CppError> {
        self.current_chars = 0;
        let mut active = vec![];
        let mut output = vec![];
        let mut used = BTreeMap::new();
        self.walk(root, cwd, argv, &mut active, &mut output, &mut used)?;
        validate(&output)?;
        Ok((output, used.into_values().collect()))
    }
    fn walk(
        &mut self,
        root: &Path,
        cwd: &Path,
        argv: &[String],
        active: &mut Vec<PathBuf>,
        output: &mut Vec<String>,
        used: &mut BTreeMap<String, InputIdentity>,
    ) -> Result<(), CppError> {
        for token in argv {
            if let Some(raw) = token.strip_prefix('@') {
                if active.len() >= 4 {
                    return Err(CppError::new(
                        "response-file-depth",
                        "response nesting exceeds four files",
                    ));
                }
                // GCC resolves every @file relative to the compiler cwd, including nested @ tokens.
                let (relative, scope, path) = scoped(root, cwd, raw)?;
                if scope != "project" {
                    return Err(CppError::new(
                        "response-file-outside-project",
                        "response file leaves project",
                    ));
                }
                if active.contains(&path) {
                    return Err(CppError::new(
                        "response-file-cycle",
                        "recursive response file",
                    ));
                }
                if !self.files.contains_key(&path) {
                    let bytes = read_contained(root, &path, MAX_RESPONSE_BYTES)?;
                    self.bytes += bytes.len();
                    if self.bytes > MAX_RESPONSE_BYTES {
                        return Err(CppError::new(
                            "response-file-too-large",
                            "aggregate response input exceeds 4MiB",
                        ));
                    }
                    let text = std::str::from_utf8(&bytes).map_err(|_| {
                        CppError::new("response-file-malformed", "response is not UTF-8")
                    })?;
                    let words = split_posix(text)?;
                    let identity = InputIdentity {
                        path: relative.clone(),
                        sha256: ici_domain::sha256(&bytes),
                        size: bytes.len() as u64,
                    };
                    self.files.insert(path.clone(), (words, identity));
                }
                let (words, identity) = self.files[&path].clone();
                used.insert(identity.path.clone(), identity);
                active.push(path);
                self.walk(root, cwd, &words, active, output, used)?;
                active.pop();
            } else {
                self.current_chars += token.chars().count();
                output.push(token.clone());
            }
            if output.len() > MAX_ARGUMENTS || self.current_chars > MAX_ARGUMENT_CHARS {
                return Err(CppError::new(
                    "invalid-arguments",
                    "expanded response arguments exceed limit",
                ));
            }
        }
        Ok(())
    }
}
