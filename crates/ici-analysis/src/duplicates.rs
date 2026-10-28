//! Shared bounded, region-preserving Type-2 matching for native language packs.
use crate::{cancelled, finish_status};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, EvidenceLevel, Finding, InspectionTarget, Measurement,
    SourceSpan,
};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::atomic::AtomicBool,
};

pub const FINGERPRINT_ALGORITHM: &str = "sha256/type2-region-v2";
#[derive(Clone, Debug)]
pub struct DuplicateFileData {
    pub file_path: String,
    pub language: String,
    pub raw_text: String,
    pub indexed: Vec<(u32, String)>,
    /// One (lexical scope, import/directive segment) key per indexed record.
    pub regions: Vec<(u32, u32)>,
}
type Location = (usize, u32, u32);
type Match = (Location, Location, usize);
type Windows = BTreeMap<(String, u128), Vec<(usize, usize)>>;
fn raw_lines(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut start = 0;
    let mut cursor = 0;
    let mut lines = vec![];
    while cursor < bytes.len() {
        if matches!(bytes[cursor], b'\r' | b'\n') {
            cursor += if bytes[cursor] == b'\r' && bytes.get(cursor + 1) == Some(&b'\n') {
                2
            } else {
                1
            };
            lines.push(&text[start..cursor]);
            start = cursor;
        } else {
            cursor += 1;
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}
#[derive(Clone, Copy)]
struct Limits {
    occurrences: usize,
    same: usize,
    cross_files: usize,
    cross: usize,
    comparisons: usize,
    matches: usize,
}
fn option(request: &AnalysisRequest, name: &str, default: usize) -> Result<usize, String> {
    request.options.get(name).map_or(Ok(default), |value| {
        value
            .as_u64()
            .and_then(|value| usize::try_from(value).ok())
            .ok_or_else(|| format!("{name} must be a non-negative integer"))
    })
}
fn limits(request: &AnalysisRequest) -> Result<Limits, String> {
    Ok(Limits {
        occurrences: option(request, "max_window_occurrences", 2048)?,
        same: option(request, "max_same_file_seed_pairs", 100_000)?,
        cross_files: option(request, "max_cross_file_pairs", 20_000)?,
        cross: option(request, "max_cross_file_seed_pairs", 250_000)?,
        comparisons: option(request, "max_extension_comparisons", 5_000_000)?,
        matches: option(request, "max_raw_matches", 10_000)?,
    })
}
fn signal(normalized: &str) -> bool {
    let tokens: Vec<_> = normalized.split('\x1f').collect();
    let literal = |token: &str| {
        matches!(
            token,
            "INT_LIT"
                | "FLOAT_LIT"
                | "COMPLEX_LIT"
                | "CHAR_LIT"
                | "STRING_LIT"
                | "RAW_STRING_LIT"
                | "BYTES_LIT"
                | "RAW_BYTES_LIT"
                | "FSTRING_LIT"
                | "RAW_FSTRING_LIT"
                | "False"
                | "None"
                | "True"
                | "false"
                | "nullptr"
                | "true"
        ) || [
            "INT_LIT:",
            "FLOAT_LIT:",
            "CHAR_LIT:",
            "STRING_LIT:",
            "RAW_STRING_LIT:",
        ]
        .iter()
        .any(|prefix| token.starts_with(prefix))
    };
    if tokens.iter().filter(|token| **token == "ID").count() >= 2
        && !tokens.iter().any(|token| literal(token))
    {
        return true;
    }
    tokens.iter().any(|token| {
        !literal(token)
            && !matches!(
                *token,
                "ID" | "INDENT"
                    | "DEDENT"
                    | "("
                    | ")"
                    | "["
                    | "]"
                    | "{"
                    | "}"
                    | ","
                    | ";"
                    | ":"
                    | "."
                    | "::"
                    | "="
                    | "@"
                    | "*"
                    | "&"
                    | "&&"
            )
            && !["ERROR_TOKEN(", "TOKEN_TYPE(", "UNTERMINATED_"]
                .iter()
                .any(|prefix| token.starts_with(prefix))
    })
}
fn has_signal(prefix: &[usize], start: usize, size: usize) -> bool {
    let minimum = (size.div_ceil(4)).max(if size >= 6 { 2 } else { 1 });
    prefix[start + size] - prefix[start] >= minimum
}
fn windows(
    files: &[&DuplicateFileData],
    size: usize,
    limits: Limits,
    cancellation: Option<&AtomicBool>,
) -> Result<Windows, String> {
    let mut windows = Windows::new();
    if !files.iter().any(|file| file.indexed.len() >= size) {
        return Ok(windows);
    }
    let base = 0x100000001b3_u128;
    let mut factor = 1_u128;
    for _ in 1..size {
        factor = factor.wrapping_mul(base);
    }
    for (file_index, file) in files.iter().enumerate() {
        if crate::cancellation_requested(cancellation) {
            return Err("analysis cancelled".into());
        }
        if file.indexed.len() < size {
            continue;
        }
        let mut hashes = Vec::with_capacity(file.indexed.len());
        let mut prefix = vec![0];
        for (_, record) in &file.indexed {
            if crate::cancellation_requested(cancellation) {
                return Err("analysis cancelled".into());
            }
            let digest = Sha256::digest(record.as_bytes());
            hashes.push(u128::from_be_bytes(
                digest[..16].try_into().expect("SHA256 prefix"),
            ));
            prefix.push(prefix.last().copied().unwrap_or(0) + usize::from(signal(record)));
        }
        let mut rolling = hashes[..size].iter().fold(0_u128, |hash, value| {
            hash.wrapping_mul(base).wrapping_add(*value)
        });
        let mut region_end = 0;
        for start in 0..=hashes.len() - size {
            if crate::cancellation_requested(cancellation) {
                return Err("analysis cancelled".into());
            }
            if start > 0 {
                rolling = rolling
                    .wrapping_sub(hashes[start - 1].wrapping_mul(factor))
                    .wrapping_mul(base)
                    .wrapping_add(hashes[start + size - 1]);
            }
            if !has_signal(&prefix, start, size) {
                continue;
            }
            if start >= region_end {
                region_end = start + 1;
                while region_end < file.indexed.len()
                    && file.regions[region_end] == file.regions[start]
                {
                    region_end += 1;
                }
            }
            if region_end < start + size {
                continue;
            }
            let occurrences = windows.entry((file.language.clone(), rolling)).or_default();
            if occurrences.len() >= limits.occurrences {
                return Err(format!(
                    "Shared duplicate-window occurrence count exceeds MAX_DUPLICATE_WINDOW_OCCURRENCES={}",
                    limits.occurrences
                ));
            }
            occurrences.push((file_index, start));
        }
    }
    Ok(windows)
}
struct Budget<'a> {
    files: &'a [&'a DuplicateFileData],
    limits: Limits,
    same: usize,
    cross: usize,
    comparisons: usize,
    pairs: BTreeSet<(usize, usize)>,
    cancellation: Option<&'a AtomicBool>,
}
impl Budget<'_> {
    fn equal(&mut self, a: usize, x: usize, b: usize, y: usize) -> Result<bool, String> {
        if crate::cancellation_requested(self.cancellation) {
            return Err("analysis cancelled".into());
        }
        self.comparisons += 1;
        if self.comparisons > self.limits.comparisons {
            return Err(format!(
                "Duplicate seed extension work exceeds MAX_DUPLICATE_EXTENSION_COMPARISONS={}",
                self.limits.comparisons
            ));
        }
        Ok(self.files[a].indexed[x].1 == self.files[b].indexed[y].1)
    }
    fn accept(
        &mut self,
        a: usize,
        x: usize,
        b: usize,
        y: usize,
        size: usize,
    ) -> Result<bool, String> {
        if a == b {
            self.same += 1;
            if self.same > self.limits.same {
                return Err(format!(
                    "Duplicate same-file seed pair count exceeds MAX_DUPLICATE_SAME_FILE_SEED_PAIRS={}",
                    self.limits.same
                ));
            }
            Ok(y - x >= size)
        } else {
            self.cross += 1;
            if self.cross > self.limits.cross {
                return Err(format!(
                    "Duplicate cross-file seed pair count exceeds MAX_DUPLICATE_CROSS_FILE_SEED_PAIRS={}",
                    self.limits.cross
                ));
            }
            self.pairs.insert((a, b));
            if self.pairs.len() > self.limits.cross_files {
                return Err(format!(
                    "Duplicate cross-file candidate count exceeds MAX_DUPLICATE_CROSS_FILE_PAIRS={}",
                    self.limits.cross_files
                ));
            }
            Ok(true)
        }
    }
    fn extend(
        &mut self,
        a: usize,
        x: usize,
        b: usize,
        y: usize,
        window: usize,
    ) -> Result<(usize, usize, usize), String> {
        let mut backward_limit = x.min(y);
        if a == b {
            backward_limit = backward_limit.min(y - x - window);
        }
        let mut backward = 0;
        while backward < backward_limit {
            let first = x - backward - 1;
            let second = y - backward - 1;
            if self.files[a].regions[first] != self.files[a].regions[x]
                || self.files[b].regions[second] != self.files[b].regions[y]
                || !self.equal(a, first, b, second)?
            {
                break;
            }
            backward += 1;
        }
        let first = x - backward;
        let second = y - backward;
        let mut size = window + backward;
        while first + size < self.files[a].indexed.len()
            && second + size < self.files[b].indexed.len()
            && (a != b || size < second - first)
            && self.files[a].regions[first + size] == self.files[a].regions[first]
            && self.files[b].regions[second + size] == self.files[b].regions[second]
            && self.equal(a, first + size, b, second + size)?
        {
            size += 1;
        }
        Ok((first, second, size))
    }
}
fn raw_matches(
    files: &[&DuplicateFileData],
    size: usize,
    limits: Limits,
    cancellation: Option<&AtomicBool>,
) -> Result<Vec<Match>, String> {
    let windows = windows(files, size, limits, cancellation)?;
    let mut budget = Budget {
        files,
        limits,
        same: 0,
        cross: 0,
        comparisons: 0,
        pairs: BTreeSet::new(),
        cancellation,
    };
    let mut covered: BTreeMap<(usize, usize, isize), Vec<(usize, usize)>> = BTreeMap::new();
    let mut matches = vec![];
    for occurrences in windows.values() {
        for (left, (a, x)) in occurrences.iter().enumerate() {
            for (b, y) in &occurrences[left + 1..] {
                if !budget.accept(*a, *x, *b, *y, size)? {
                    continue;
                }
                let key = (*a, *b, *y as isize - *x as isize);
                if covered
                    .get(&key)
                    .is_some_and(|ranges| ranges.iter().any(|(start, end)| start <= x && x <= end))
                {
                    continue;
                }
                let mut equal = true;
                for offset in 0..size {
                    if !budget.equal(*a, *x + offset, *b, *y + offset)? {
                        equal = false;
                        break;
                    }
                }
                if !equal {
                    continue;
                }
                let (first, second, length) = budget.extend(*a, *x, *b, *y, size)?;
                let ranges = covered.entry(key).or_default();
                ranges.push((first, first + length - 1));
                ranges.sort_unstable();
                let mut merged: Vec<(usize, usize)> = vec![];
                for &(start, end) in ranges.iter() {
                    if let Some(last) = merged.last_mut()
                        && start <= last.1 + 1
                    {
                        last.1 = last.1.max(end);
                        continue;
                    }
                    merged.push((start, end));
                }
                *ranges = merged;
                matches.push((
                    (
                        *a,
                        files[*a].indexed[first].0,
                        files[*a].indexed[first + length - 1].0,
                    ),
                    (
                        *b,
                        files[*b].indexed[second].0,
                        files[*b].indexed[second + length - 1].0,
                    ),
                    length,
                ));
                if matches.len() > limits.matches {
                    return Err(format!(
                        "Raw duplicate match count exceeds MAX_DUPLICATE_RAW_MATCHES={}",
                        limits.matches
                    ));
                }
            }
        }
    }
    matches.sort_by_key(|item| std::cmp::Reverse(item.2));
    let mut kept: Vec<Match> = vec![];
    for candidate in matches {
        if crate::cancellation_requested(cancellation) {
            return Err("analysis cancelled".into());
        }
        if kept.iter().any(|(first, second, _)| {
            candidate.0.0 == first.0
                && candidate.1.0 == second.0
                && candidate.0.1 <= first.2
                && candidate.0.2 >= first.1
                && candidate.1.1 <= second.2
                && candidate.1.2 >= second.1
        }) {
            continue;
        }
        kept.push(candidate);
    }
    Ok(kept)
}

pub fn analyze_type2(request: &AnalysisRequest, files: &[DuplicateFileData]) -> AnalysisOutput {
    analyze_inner(request, files, None)
}
pub fn analyze_type2_cancellable(
    request: &AnalysisRequest,
    files: &[DuplicateFileData],
    cancellation: &AtomicBool,
) -> AnalysisOutput {
    analyze_inner(request, files, Some(cancellation))
}
pub(crate) fn analyze_inner(
    request: &AnalysisRequest,
    files: &[DuplicateFileData],
    cancellation: Option<&AtomicBool>,
) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(&request.task_id, "ici.dup");
    if cancelled(&mut output, cancellation) {
        return output;
    }
    let mut ordered: Vec<_> = files.iter().collect();
    ordered.sort_by_key(|file| &file.file_path);
    let result = (|| -> Result<Vec<Match>, String> {
        let mut paths = BTreeSet::new();
        let mut records = 0;
        let mut chars = 0;
        for file in &ordered {
            if !paths.insert(&file.file_path) {
                return Err("Duplicate file paths are not a valid analysis inventory".into());
            }
            if file.regions.len() != file.indexed.len()
                || file.indexed.iter().enumerate().any(|(index, (line, _))| {
                    *line == 0 || (index > 0 && file.indexed[index - 1].0 >= *line)
                })
            {
                return Err(
                    "Duplicate regions must align with strictly increasing positive line records"
                        .into(),
                );
            }
            records += file.indexed.len();
            chars += file
                .indexed
                .iter()
                .map(|(_, tokens)| tokens.chars().count())
                .sum::<usize>();
            if records > option(request, "max_indexed_records", 500_000)?
                || chars > option(request, "max_normalized_chars", 128 * 1024 * 1024)?
            {
                return Err(
                    "Normalized duplicate input exceeds the bounded record or character limit"
                        .into(),
                );
            }
        }
        let size = option(request, "window_size", option(request, "min_window", 6)?)?;
        if size == 0 {
            return Err("window_size must be a positive integer".into());
        }
        raw_matches(&ordered, size, limits(request)?, cancellation)
    })();
    let matches = match result {
        Ok(matches) => matches,
        Err(message) => {
            if !cancelled(&mut output, cancellation) {
                output.observation.state = ici_domain::TaskState::Failed;
                output.status = "ERROR".into();
                output.summary = "Duplicate analysis did not complete".into();
                output.observation.limitations.push(message.clone());
                let mut target = InspectionTarget::at(".", 1);
                target.target_name = "DuplicateAnalysisError".into();
                target.status = "ERROR".into();
                target.message = message;
                output.targets.push(target);
            }
            return output;
        }
    };
    let mut adjacency: BTreeMap<Location, BTreeSet<Location>> = BTreeMap::new();
    let mut lengths: BTreeMap<Location, usize> = BTreeMap::new();
    for (first, second, length) in matches {
        adjacency.entry(first).or_default().insert(second);
        adjacency.entry(second).or_default().insert(first);
        for location in [first, second] {
            let longest = lengths.entry(location).or_default();
            *longest = (*longest).max(length);
        }
    }
    let mut visited = BTreeSet::new();
    let mut groups = vec![];
    for start in adjacency.keys() {
        if !visited.insert(*start) {
            continue;
        }
        let mut component = vec![];
        let mut queue = vec![*start];
        while let Some(location) = queue.pop() {
            component.push(location);
            for next in &adjacency[&location] {
                if visited.insert(*next) {
                    queue.push(*next);
                }
            }
        }
        component.sort_by_key(|location| (&ordered[location.0].file_path, location.1, location.2));
        groups.push(component);
    }
    let longest = |group: &Vec<Location>| {
        group
            .iter()
            .map(|location| lengths[location])
            .max()
            .unwrap_or(0)
    };
    groups.sort_by(|left, right| {
        longest(right)
            .cmp(&longest(left))
            .then_with(|| right.len().cmp(&left.len()))
            .then_with(|| left.cmp(right))
    });
    let mut duplicated = BTreeSet::new();
    let mut serialized_groups = vec![];
    for (index, group) in groups.iter().enumerate() {
        if cancelled(&mut output, cancellation) {
            return output;
        }
        let representative = group
            .iter()
            .min_by_key(|location| {
                (
                    std::cmp::Reverse(lengths[location]),
                    &ordered[location.0].file_path,
                    location.1,
                    location.2,
                )
            })
            .expect("nonempty clone component");
        let file = ordered[representative.0];
        let normalized = file
            .indexed
            .iter()
            .filter(|(line, _)| representative.1 <= *line && *line <= representative.2)
            .map(|(_, tokens)| tokens.as_str())
            .collect::<Vec<_>>()
            .join("\n");
        let fingerprint = format!(
            "{:x}",
            Sha256::digest(
                format!("{FINGERPRINT_ALGORITHM}\0{}\0{normalized}", file.language).as_bytes()
            )
        );
        let raw_lines = raw_lines(&file.raw_text);
        let snippet = raw_lines
            .get(representative.1.saturating_sub(1) as usize..representative.2 as usize)
            .unwrap_or(&[])
            .concat()
            .trim_end()
            .to_owned();
        let length = longest(group);
        let mut occurrences = vec![];
        for (position, location) in group.iter().enumerate() {
            let file = ordered[location.0];
            if position > 0 {
                duplicated.extend(
                    file.indexed
                        .iter()
                        .filter(|(line, _)| location.1 <= *line && *line <= location.2)
                        .map(|(line, _)| (location.0, *line)),
                );
            }
            let message = format!(
                "duplicate block ({length} lines, group #{}) shared across {} locations",
                index + 1,
                group.len()
            );
            let mut finding = Finding::new(
                "dup.type2-clone",
                message,
                SourceSpan {
                    path: file.file_path.clone(),
                    start_line: location.1,
                    end_line: Some(location.2),
                    ..SourceSpan::line(&file.file_path, location.1)
                },
                "ici.dup",
            );
            finding.fingerprint = format!(
                "sha256:{:x}",
                Sha256::digest(
                    format!(
                        "ici.dup\0{fingerprint}\0{}\0{}\0{}",
                        file.file_path, location.1, location.2
                    )
                    .as_bytes()
                )
            );
            finding.severity = "medium".into();
            finding.confidence = "high".into();
            finding.evidence = EvidenceLevel::Measured;
            finding.component_id = Some(request.component_id.clone());
            finding.task_id = Some(request.task_id.clone());
            output.observation.findings.push(finding);
            let mut target = InspectionTarget::at(&file.file_path, location.1);
            target.end_line = Some(location.2);
            target.target_name = format!("CloneGroup#{}", index + 1);
            target.status = "WARN".into();
            target.message = format!(
                "Duplicate code block ({length} lines) shared across {} locations",
                group.len()
            );
            target.snippet = snippet.chars().take(300).collect();
            target.metrics = BTreeMap::from([
                ("clone_group".into(), json!(index + 1)),
                ("duplicate_lines".into(), json!(length)),
                ("fingerprint".into(), json!(fingerprint)),
                ("fingerprint_algorithm".into(), json!(FINGERPRINT_ALGORITHM)),
            ]);
            output.targets.push(target);
            occurrences.push(json!({"file_path":file.file_path,"start_line":location.1,"end_line":location.2,"loc":format!("{}:{}-{}",file.file_path,location.1,location.2)}));
        }
        serialized_groups.push(json!({"id":index+1,"fingerprint":fingerprint,"fingerprint_algorithm":FINGERPRINT_ALGORITHM,"detection":"type2-lexical-region","language":file.language,"lines_count":length,"occurrences_count":group.len(),"occurrences":occurrences,"snippet":snippet}));
    }
    let total: usize = ordered.iter().map(|file| file.indexed.len()).sum();
    let duplicated = duplicated.len();
    let percent = if total == 0 {
        0.0
    } else {
        duplicated as f64 * 100.0 / total as f64
    };
    let warn = request
        .options
        .get("warn_pct")
        .and_then(serde_json::Value::as_f64)
        .unwrap_or(5.0);
    let fail = request
        .options
        .get("fail_pct")
        .and_then(serde_json::Value::as_f64)
        .unwrap_or(15.0);
    if percent <= warn && percent <= fail {
        for target in &mut output.targets {
            target.status = "PASS".into();
        }
    }
    for file in &ordered {
        if !output
            .targets
            .iter()
            .any(|target| target.file_path == file.file_path)
        {
            let mut target = InspectionTarget::at(&file.file_path, 1);
            target.target_name = "DuplicateScan".into();
            target.message = "Source was analyzed and has no reported duplicate region".into();
            output.targets.push(target);
        }
    }
    output.observation.measurements.extend([
        Measurement {
            unit: "groups".into(),
            ..Measurement::measured("clone_groups", groups.len() as f64)
        },
        Measurement {
            unit: "lines".into(),
            numerator: Some(duplicated as u64),
            denominator: if total == 0 { None } else { Some(total as u64) },
            ..Measurement::measured("duplicated_lines", duplicated as f64)
        },
    ]);
    output.extra = json!({"duplication_pct":percent,"clone_groups_count":groups.len(),"clone_groups":serialized_groups,"fingerprint_algorithm":FINGERPRINT_ALGORITHM,"duplicated_lines":duplicated,"total_code_lines":total,"source_files_analyzed":ordered.len(),"analysis_provenance":"language-lexical-region-heuristic"});
    output.score = Some(percent);
    output.summary = format!(
        "Code Duplication Rate: {percent:.1}% ({} distinct clone groups found)",
        groups.len()
    );
    finish_status(&mut output, request);
    if percent > fail {
        output.status = if request
            .options
            .get("mode")
            .and_then(serde_json::Value::as_str)
            == Some("pass_warn")
        {
            "WARN"
        } else {
            "FAIL"
        }
        .into();
    }
    output
}
