//! Strict, immutable Python source decoding and checked Unicode locations.
use ici_domain::{InspectionTarget, SourceInput, SourceSpan};
use regex::bytes::Regex;
use ruff_text_size::TextRange;
use std::sync::LazyLock;

pub const MAX_SOURCE_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_TOTAL_BYTES: usize = 512 * 1024 * 1024;
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
    raw_offsets: Option<crate::codecs::OffsetMap>,
}
static COOKIE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?-u)^[ \t\x0c]*#.*?coding[:=][ \t]*([-A-Za-z0-9_.]+)")
        .expect("constant encoding regex")
});

impl SourceDocument {
    pub fn decode(input: &SourceInput) -> Result<Self, SourceError> {
        Self::decode_cancellable(input, None)
    }
    pub fn decode_cancellable(
        input: &SourceInput,
        cancellation: Option<&std::sync::atomic::AtomicBool>,
    ) -> Result<Self, SourceError> {
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
                "Source exceeds the 16 MiB analysis bound",
            ));
        }
        let bom_bytes = usize::from(input.bytes.starts_with(&[0xef, 0xbb, 0xbf])) * 3;
        let bytes = &input.bytes[bom_bytes..];
        // Headers are bytes in the Python source encoding; ASCII cookie spelling is invariant.
        let mut encoding = "utf-8".to_owned();
        let python_source = input.path.ends_with(".py") || input.path.ends_with(".pyi");
        if python_source {
            let mut offset = 0usize;
            for _ in 0..2 {
                let end = bytes[offset..]
                    .iter()
                    .position(|byte| matches!(byte, b'\r' | b'\n'))
                    .map_or(bytes.len(), |position| offset + position);
                let header = &bytes[offset..end];
                if let Some(capture) = COOKIE.captures(header) {
                    encoding = std::str::from_utf8(&capture[1])
                        .expect("ASCII encoding label")
                        .to_ascii_lowercase()
                        .replace('_', "-");
                    break;
                }
                if header
                    .iter()
                    .find(|byte| !matches!(byte, b' ' | b'\t' | b'\x0c'))
                    .is_some_and(|byte| *byte != b'#')
                    || end == bytes.len()
                {
                    break;
                }
                offset = end
                    + if bytes[end] == b'\r' && bytes.get(end + 1) == Some(&b'\n') {
                        2
                    } else {
                        1
                    };
                if offset >= bytes.len() {
                    break;
                }
            }
        }
        // CPython normalizes only these PEP 263 families before its codec
        // lookup and BOM comparison. In particular BOM+utf8/cp65001 is invalid,
        // even though either alias decodes UTF-8 without a BOM.
        let prefix = encoding.chars().take(12).collect::<String>();
        let selected = if prefix == "utf-8" || prefix.starts_with("utf-8-") {
            "utf-8"
        } else if ["latin-1", "iso-8859-1", "iso-latin-1"]
            .iter()
            .any(|name| prefix == *name || prefix.starts_with(&format!("{name}-")))
        {
            "iso-8859-1"
        } else {
            &encoding
        };
        if bom_bytes != 0 && selected != "utf-8" {
            return Err(SourceError::new(
                "encoding-conflict",
                "UTF-8 BOM conflicts with the source encoding cookie",
            ));
        }
        let codec = crate::codecs::lookup(selected).ok_or_else(|| {
            SourceError::new(
                "unsupported-encoding",
                format!("Source encoding {encoding:?} is not supported; file was not analyzed"),
            )
        })?;
        let decoded = if python_source && matches!(codec, crate::codecs::Codec::Hz) {
            crate::stateful::hz_source(bytes, bom_bytes, cancellation)?
        } else {
            crate::codecs::decode(codec, bytes, bom_bytes, cancellation)?
        };
        let (text, raw_offsets) = (decoded.text, decoded.map);
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
    pub(crate) fn cache_weight(&self) -> usize {
        // Retain the prior text/AST-string headroom and also count actual
        // source-index allocation capacities, including expanded raw maps.
        self.text
            .capacity()
            .saturating_add(self.text.len().saturating_mul(3))
            .saturating_add(
                self.starts
                    .capacity()
                    .saturating_mul(std::mem::size_of::<usize>()),
            )
            .saturating_add(self.raw_offsets.as_ref().map_or(0, |map| map.heap_bytes()))
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
            Some(map) => map.raw_offset(&self.text, decoded),
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
    pub fn lines(&self) -> UniversalLines<'_> {
        UniversalLines {
            text: &self.text,
            offset: 0,
        }
    }
}

pub fn exclusion_reasons(path: &str) -> Vec<&'static str> {
    let parts: Vec<String> = path.split('/').map(crate::unicode::fold).collect();
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

pub struct UniversalLines<'a> {
    text: &'a str,
    offset: usize,
}
impl<'a> Iterator for UniversalLines<'a> {
    type Item = &'a str;
    fn next(&mut self) -> Option<Self::Item> {
        if self.offset >= self.text.len() {
            return None;
        }
        let start = self.offset;
        let end = self.text.as_bytes()[start..]
            .iter()
            .position(|byte| matches!(byte, b'\r' | b'\n'))
            .map_or(self.text.len(), |relative| start + relative);
        self.offset = end;
        if end < self.text.len() {
            self.offset += if self.text.as_bytes()[end] == b'\r'
                && self.text.as_bytes().get(end + 1) == Some(&b'\n')
            {
                2
            } else {
                1
            };
        }
        Some(&self.text[start..end])
    }
}
