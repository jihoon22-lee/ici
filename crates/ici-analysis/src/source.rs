//! Strict, immutable Python source decoding and checked Unicode locations.
use ici_domain::{InspectionTarget, SourceInput, SourceSpan};
use regex::Regex;
use ruff_text_size::TextRange;
use std::sync::LazyLock;

pub const MAX_SOURCE_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_TOTAL_BYTES: usize = 64 * 1024 * 1024;
pub const MAX_SOURCES: usize = 2048;

#[derive(Clone, Debug)]
pub struct SourceError {
    pub code: &'static str,
    pub message: String,
    pub line: u32,
    pub column: u32,
}
impl SourceError {
    pub fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            line: 1,
            column: 1,
        }
    }
}
#[derive(Clone, Debug)]
pub struct SourceDocument {
    pub path: String,
    pub text: String,
    pub encoding: String,
    pub bom_bytes: usize,
    starts: Vec<usize>,
    raw_offsets: Option<Vec<(usize, usize)>>,
}
static COOKIE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^[ \t\x0c]*#.*?coding[:=][ \t]*([-\w.]+)").expect("constant encoding regex")
});

impl SourceDocument {
    pub fn decode(input: &SourceInput) -> Result<Self, SourceError> {
        if input.path.is_empty()
            || input.path.starts_with('/')
            || input.path.contains(['\\', '\0'])
            || input
                .path
                .split('/')
                .any(|part| part == ".." || part.is_empty())
        {
            return Err(SourceError::new(
                "invalid-path",
                "Source path must be project-relative",
            ));
        }
        if input.bytes.len() > MAX_SOURCE_BYTES {
            return Err(SourceError::new(
                "source-limit",
                "Source exceeds the 8 MiB analysis bound",
            ));
        }
        let bom_bytes = usize::from(input.bytes.starts_with(&[0xef, 0xbb, 0xbf])) * 3;
        let bytes = &input.bytes[bom_bytes..];
        // Headers are bytes in the Python source encoding; ASCII cookie spelling is invariant.
        let mut encoding = "utf-8".to_owned();
        let header_text: String = bytes.iter().map(|byte| char::from(*byte)).collect();
        let header_text = header_text.replace("\r\n", "\n").replace('\r', "\n");
        for line in header_text.lines().take(
            if input.path.ends_with(".py") || input.path.ends_with(".pyi") {
                2
            } else {
                0
            },
        ) {
            let header = line;
            if let Some(capture) = COOKIE.captures(&header) {
                encoding = capture[1].to_ascii_lowercase().replace('_', "-");
                break;
            }
            if !header.trim_start().starts_with('#') && !header.trim().is_empty() {
                break;
            }
        }
        let utf8 = matches!(encoding.as_str(), "utf-8" | "utf8" | "utf-8-sig");
        if bom_bytes != 0 && !utf8 {
            return Err(SourceError::new(
                "encoding-conflict",
                "UTF-8 BOM conflicts with the source encoding cookie",
            ));
        }
        let (text, raw_offsets) = if utf8 {
            (
                String::from_utf8(bytes.to_vec()).map_err(|_| {
                    SourceError::new("invalid-encoding", "Source is not valid UTF-8")
                })?,
                None,
            )
        } else if matches!(encoding.as_str(), "ascii" | "us-ascii") {
            if !bytes.is_ascii() {
                return Err(SourceError::new(
                    "invalid-encoding",
                    "Source is not valid ASCII",
                ));
            }
            (
                String::from_utf8(bytes.to_vec()).expect("ASCII is UTF-8"),
                None,
            )
        } else if matches!(
            encoding.as_str(),
            "latin-1"
                | "latin1"
                | "iso-8859-1"
                | "iso8859-1"
                | "iso-latin-1"
                | "l1"
                | "cp1252"
                | "windows-1252"
        ) {
            let windows = matches!(encoding.as_str(), "cp1252" | "windows-1252");
            let mut text = String::new();
            let mut offsets = Vec::with_capacity(bytes.len() + 1);
            const CP1252: [u32; 32] = [
                0x20ac, 0, 0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021, 0x02c6, 0x2030, 0x0160,
                0x2039, 0x0152, 0, 0x017d, 0, 0, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013,
                0x2014, 0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, 0, 0x017e, 0x0178,
            ];
            for (index, byte) in bytes.iter().enumerate() {
                let value = if windows && (0x80..=0x9f).contains(byte) {
                    CP1252[usize::from(*byte - 0x80)]
                } else {
                    u32::from(*byte)
                };
                if value == 0 && *byte != 0 {
                    return Err(SourceError::new(
                        "invalid-encoding",
                        "Source contains an undefined CP1252 byte",
                    ));
                }
                offsets.push((text.len(), index + bom_bytes));
                text.push(char::from_u32(value).expect("single-byte codec scalar"));
            }
            offsets.push((text.len(), input.bytes.len()));
            (text, Some(offsets))
        } else {
            return Err(SourceError::new(
                "unsupported-encoding",
                format!("Source encoding {encoding:?} is not supported; file was not analyzed"),
            ));
        };
        if text.contains('\0') {
            return Err(SourceError::new(
                "invalid-text",
                "Source contains a NUL character",
            ));
        }
        let mut starts = vec![0];
        let bytes = text.as_bytes();
        for (index, byte) in bytes.iter().enumerate() {
            if *byte == b'\n' || (*byte == b'\r' && bytes.get(index + 1) != Some(&b'\n')) {
                starts.push(index + 1);
            }
        }
        Ok(Self {
            path: input.path.clone(),
            text,
            encoding,
            bom_bytes,
            starts,
            raw_offsets,
        })
    }
    pub fn location(&self, offset: usize) -> (u32, u32) {
        let offset = offset.min(self.text.len());
        let mut boundary = offset;
        while !self.text.is_char_boundary(boundary) {
            boundary -= 1;
        }
        let row = self
            .starts
            .partition_point(|value| *value <= boundary)
            .saturating_sub(1);
        (
            (row + 1) as u32,
            (self.text[self.starts[row]..boundary].chars().count() + 1) as u32,
        )
    }
    pub fn span(&self, range: TextRange) -> SourceSpan {
        debug_assert!(self.raw_offset(range.start().to_usize()).is_some());
        let (start_line, start_column) = self.location(range.start().to_usize());
        let mut inclusive_end = range.end().to_usize().min(self.text.len());
        if inclusive_end > range.start().to_usize() {
            inclusive_end -= 1;
            while !self.text.is_char_boundary(inclusive_end) {
                inclusive_end -= 1;
            }
        }
        let (end_line, end_column) = self.location(inclusive_end);
        SourceSpan {
            path: self.path.clone(),
            start_line,
            end_line: Some(end_line),
            start_column: Some(start_column),
            end_column: Some(end_column),
            label: String::new(),
        }
    }
    pub fn target(&self, range: TextRange, name: impl Into<String>) -> InspectionTarget {
        let span = self.span(range);
        let mut target = InspectionTarget::at(&self.path, span.start_line);
        target.end_line = span.end_line;
        target.start_column = span.start_column;
        target.end_column = span.end_column;
        target.target_name = name.into();
        target
    }
    pub fn raw_offset(&self, decoded: usize) -> Option<usize> {
        if decoded > self.text.len() || !self.text.is_char_boundary(decoded) {
            return None;
        }
        match &self.raw_offsets {
            None => Some(decoded + self.bom_bytes),
            Some(map) => map
                .binary_search_by_key(&decoded, |item| item.0)
                .ok()
                .map(|index| map[index].1),
        }
    }
    pub fn snippet(&self, range: TextRange, max_lines: usize) -> String {
        self.text
            .get(range.start().to_usize()..range.end().to_usize())
            .unwrap_or("")
            .lines()
            .take(max_lines)
            .collect::<Vec<_>>()
            .join("\n")
    }
    pub fn lines(&self) -> Vec<&str> {
        let mut lines = self.text.split_inclusive(['\r', '\n']).peekable();
        let mut result = Vec::new();
        while let Some(line) = lines.next() {
            if line.ends_with('\r') && lines.peek() == Some(&"\n") {
                lines.next();
            }
            result.push(line.trim_end_matches(['\r', '\n']));
        }
        result
    }
}

pub fn exclusion_reasons(path: &str) -> Vec<&'static str> {
    let parts: Vec<String> = path.split('/').map(str::to_lowercase).collect();
    let directories = &parts[..parts.len().saturating_sub(1)];
    let name = parts.last().map_or("", String::as_str);
    let mut reasons = Vec::new();
    if directories.iter().any(|part| {
        matches!(
            part.as_str(),
            "_deps"
                | "deps"
                | "extern"
                | "external"
                | "node_modules"
                | "subprojects"
                | "third-party"
                | "third_party"
                | "vendor"
        )
    }) {
        reasons.push("vendor");
    }
    if directories
        .iter()
        .any(|part| matches!(part.as_str(), "generated" | "autogen") || part.ends_with("_autogen"))
        || name.starts_with("moc_")
        || name.starts_with("qrc_")
        || name.starts_with("ui_")
        || name.starts_with("mocs_compilation")
        || name.ends_with(".moc")
    {
        reasons.push("generated");
    }
    reasons
}
