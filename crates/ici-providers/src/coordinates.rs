//! Pure, bounded native-coordinate conversion over the application's immutable source snapshot.
use crate::{ProviderError, ProviderKind, ProviderPlan};
use ici_domain::{Finding, SourceInput, SourceSpan};
use std::{collections::BTreeMap, sync::Arc};

const INDEX_BUDGET: usize = 8 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ColumnKind {
    Unicode,
    Utf8,
    Utf16,
    Unknown,
}

#[derive(Debug, Default)]
pub struct SourceCoordinates {
    files: BTreeMap<String, Option<FileIndex>>,
}
#[derive(Debug)]
struct FileIndex {
    lines: Vec<LineIndex>,
    bom: bool,
}
#[derive(Debug)]
struct LineIndex {
    bytes: u32,
    utf16: u32,
    characters: u32,
    non_ascii: Vec<Boundary>,
}
#[derive(Debug)]
struct Boundary {
    byte: u32,
    utf16: u32,
    character: u32,
    bytes: u8,
    utf16_units: u8,
}

/// Build once and share the Arc between provider plans. Raw source bytes and this index are not
/// serialized into public plan JSON. Exceeding the index budget leaves coordinates unconfirmed.
pub fn source_coordinates<'a>(
    inputs: impl IntoIterator<Item = &'a SourceInput>,
) -> Arc<SourceCoordinates> {
    let mut result = SourceCoordinates::default();
    let mut remaining = INDEX_BUDGET;
    for input in inputs {
        let index = std::str::from_utf8(&input.bytes)
            .ok()
            .and_then(|text| index(text, remaining));
        if let Some((_, weight)) = &index {
            remaining = remaining.saturating_sub(*weight);
        }
        result
            .files
            .insert(input.path.clone(), index.map(|(file, _)| file));
    }
    Arc::new(result)
}

pub fn attach_sources(plan: &mut ProviderPlan, sources: Arc<SourceCoordinates>) {
    plan.context.source_coordinates = sources;
}

fn index(text: &str, budget: usize) -> Option<(FileIndex, usize)> {
    let bom = text.starts_with('\u{feff}');
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let mut lines = Vec::new();
    let mut weight: usize = 0;
    for line in universal_lines(text) {
        weight = weight.checked_add(std::mem::size_of::<LineIndex>())?;
        if weight > budget {
            return None;
        }
        let mut row = LineIndex {
            bytes: u32::try_from(line.len()).ok()?,
            utf16: 0,
            characters: 0,
            non_ascii: vec![],
        };
        for (byte, character) in line.char_indices() {
            if !character.is_ascii() {
                weight = weight.checked_add(std::mem::size_of::<Boundary>())?;
                if weight > budget {
                    return None;
                }
                row.non_ascii.push(Boundary {
                    byte: byte as u32,
                    utf16: row.utf16,
                    character: row.characters,
                    bytes: character.len_utf8() as u8,
                    utf16_units: character.len_utf16() as u8,
                });
            }
            row.utf16 += character.len_utf16() as u32;
            row.characters += 1;
        }
        lines.push(row);
    }
    Some((FileIndex { lines, bom }, weight))
}
fn universal_lines(text: &str) -> impl Iterator<Item = &str> {
    let mut position = 0;
    let mut finished = false;
    std::iter::from_fn(move || {
        if finished {
            return None;
        }
        let start = position;
        let bytes = text.as_bytes();
        while position < bytes.len() && !matches!(bytes[position], b'\r' | b'\n') {
            position += 1;
        }
        let end = position;
        if position == bytes.len() {
            finished = true;
        } else {
            let newline = bytes[position];
            position += 1;
            if newline == b'\r' && bytes.get(position) == Some(&b'\n') {
                position += 1;
            }
        }
        Some(&text[start..end])
    })
}

impl SourceCoordinates {
    fn boundary(
        &self,
        path: &str,
        line: u32,
        offset: u32,
        kind: ColumnKind,
        compiler_bom: bool,
    ) -> Result<Option<u32>, ProviderError> {
        let Some(Some(file)) = self.files.get(path) else {
            return Ok(None);
        };
        let row = file
            .lines
            .get(line.saturating_sub(1) as usize)
            .ok_or_else(|| {
                ProviderError("native diagnostic line exceeds captured source".into())
            })?;
        let offset = if compiler_bom && file.bom && line == 1 {
            offset.checked_sub(3).ok_or_else(|| {
                ProviderError("native compiler column lies inside the UTF-8 BOM".into())
            })?
        } else {
            offset
        };
        let maximum = match kind {
            ColumnKind::Unicode => row.characters,
            ColumnKind::Utf8 => row.bytes,
            ColumnKind::Utf16 => row.utf16,
            ColumnKind::Unknown => return Ok(None),
        };
        if offset > maximum {
            return Err(ProviderError(
                "native diagnostic column exceeds captured source".into(),
            ));
        }
        if kind == ColumnKind::Unicode {
            return Ok(Some(offset));
        }
        let native_start = |boundary: &Boundary| match kind {
            ColumnKind::Utf8 => boundary.byte,
            ColumnKind::Utf16 => boundary.utf16,
            ColumnKind::Unicode => boundary.character,
            ColumnKind::Unknown => 0,
        };
        let index = row
            .non_ascii
            .partition_point(|boundary| native_start(boundary) <= offset);
        let Some(previous) = index.checked_sub(1).map(|index| &row.non_ascii[index]) else {
            return Ok(Some(offset));
        };
        let start = native_start(previous);
        let width = match kind {
            ColumnKind::Utf8 => u32::from(previous.bytes),
            ColumnKind::Utf16 => u32::from(previous.utf16_units),
            ColumnKind::Unicode => 1,
            ColumnKind::Unknown => 0,
        };
        if offset == start {
            Ok(Some(previous.character))
        } else if offset < start + width {
            Err(ProviderError(
                "native diagnostic column splits an encoded character".into(),
            ))
        } else {
            Ok(Some(previous.character + 1 + offset - start - width))
        }
    }
}

pub(crate) fn normalize(
    finding: &mut Finding,
    plan: &ProviderPlan,
    override_kind: Option<ColumnKind>,
) -> Result<(), ProviderError> {
    let kind = override_kind.unwrap_or(match plan.kind {
        ProviderKind::Eslint | ProviderKind::Tsc => ColumnKind::Utf16,
        ProviderKind::Compiler if plan.context.compiler_columns.is_none() => ColumnKind::Unknown,
        ProviderKind::Mypy
        | ProviderKind::Compiler
        | ProviderKind::ClangTidy
        | ProviderKind::Clazy => ColumnKind::Utf8,
        _ => ColumnKind::Unicode,
    });
    if kind == ColumnKind::Unicode {
        return Ok(());
    }
    let compiler_bom = plan.context.compiler_columns.as_deref() == Some("clang-byte");
    let mut omitted = false;
    for span in
        std::iter::once(&mut finding.primary_location).chain(finding.related_locations.iter_mut())
    {
        omitted |= normalize_span(span, plan, kind, compiler_bom)?;
    }
    if omitted {
        finding.limitations.push(
            "native columns omitted: immutable UTF-8 source/index is unavailable for this location"
                .into(),
        );
    }
    Ok(())
}
fn normalize_span(
    span: &mut SourceSpan,
    plan: &ProviderPlan,
    kind: ColumnKind,
    compiler_bom: bool,
) -> Result<bool, ProviderError> {
    let original_start = span.start_column;
    if let Some(column) = span.start_column {
        span.start_column = plan
            .context
            .source_coordinates
            .boundary(&span.path, span.start_line, column - 1, kind, compiler_bom)?
            .map(|offset| offset + 1);
    }
    let original_end = span.end_column;
    if let Some(column) = span.end_column {
        // Parsers have already changed native exclusive ends to inclusive native units. Its value
        // is therefore the 0-based exclusive boundary to convert, not an interior surrogate/byte.
        span.end_column = plan
            .context
            .source_coordinates
            .boundary(
                &span.path,
                span.end_line.unwrap_or(span.start_line),
                column,
                kind,
                compiler_bom,
            )?
            .map(|offset| {
                if span.end_line.unwrap_or(span.start_line) == span.start_line {
                    offset.max(span.start_column.unwrap_or(1))
                } else {
                    offset.max(1)
                }
            });
    }
    Ok((original_start.is_some() && span.start_column.is_none())
        || (original_end.is_some() && span.end_column.is_none()))
}
