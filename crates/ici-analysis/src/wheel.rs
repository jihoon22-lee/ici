//! Wheel metadata and RECORD contracts; archives are never extracted or executed.
use crate::{
    package::{self, Package, Policy},
    unicode,
    wheel_zip::{Archive, safe_name},
};
use ici_domain::{AnalysisRequest, Finding, SourceInput};
use pep440_rs::Version;
use serde::Serialize;
use std::{
    collections::{BTreeMap, BTreeSet},
    str::FromStr,
    sync::atomic::AtomicBool,
};

#[derive(Serialize)]
pub struct Evidence {
    pub path: String,
    pub members: usize,
    pub uncompressed_bytes: u64,
    pub pure: bool,
    pub native_members: Vec<String>,
    pub direct_url: bool,
    pub build_details: bool,
}
struct Tags {
    python: BTreeSet<String>,
    abi: BTreeSet<String>,
    platform: BTreeSet<String>,
}
impl Tags {
    fn matches(&self, declared: &BTreeSet<String>) -> bool {
        let count = self
            .python
            .len()
            .checked_mul(self.abi.len())
            .and_then(|count| count.checked_mul(self.platform.len()));
        count == Some(declared.len())
            && declared.iter().all(|tag| {
                let mut parts = tag.split('-');
                let python = parts.next().unwrap_or("");
                let abi = parts.next().unwrap_or("");
                let platform = parts.next().unwrap_or("");
                parts.next().is_none()
                    && self.python.contains(python)
                    && self.abi.contains(abi)
                    && self.platform.contains(platform)
            })
    }
    fn pure(&self) -> bool {
        !self.python.is_empty()
            && self.abi.len() == 1
            && self.abi.contains("none")
            && self.platform.len() == 1
            && self.platform.contains("any")
    }
}
struct Identity {
    name: String,
    version: Version,
    tags: Tags,
}
fn filename(path: &str) -> Result<Identity, String> {
    let invalid = || format!("invalid wheel filename: {path}");
    let leaf = path.rsplit('/').next().unwrap_or(path);
    let stem = leaf.strip_suffix(".whl").ok_or_else(invalid)?;
    let parts: Vec<_> = stem.split('-').collect();
    if !matches!(parts.len(), 5 | 6) {
        return Err(invalid());
    }
    let name = parts[0];
    if name.is_empty()
        || name.contains("__")
        || !name
            .split('.')
            .all(|part| part.is_empty() || unicode::word(part))
    {
        return Err(invalid());
    }
    let version = Version::from_str(parts[1]).map_err(|_| invalid())?;
    if parts.len() == 6
        && !parts[2]
            .chars()
            .next()
            .is_some_and(|character| character.is_ascii_digit())
    {
        return Err(invalid());
    }
    let components: Vec<Vec<_>> = parts[parts.len() - 3..]
        .iter()
        .map(|part| part.split('.').collect())
        .collect();
    if components
        .iter()
        .any(|component| component.iter().any(|part| part.is_empty()))
        || components[0].iter().any(|part| !unicode::identifier(part))
    {
        return Err(invalid());
    }
    let lower = |values: &Vec<&str>| values.iter().map(|value| unicode::lower(value)).collect();
    let tags = Tags {
        python: lower(&components[0]),
        abi: lower(&components[1]),
        platform: lower(&components[2]),
    };
    Ok(Identity {
        name: package::canonical_name(name),
        version,
        tags,
    })
}
fn headers(payload: Vec<u8>, member: &str) -> Result<BTreeMap<String, Vec<String>>, String> {
    let text =
        String::from_utf8(payload).map_err(|_| format!("wheel metadata is not UTF-8: {member}"))?;
    let text = text.replace("\r\n", "\n");
    let mut headers: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for line in text.split([
        '\n', '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}',
        '\u{2029}',
    ]) {
        if line.is_empty() {
            break;
        }
        if line.chars().next().is_some_and(char::is_whitespace) {
            continue;
        }
        if let Some((name, value)) = line.split_once(':') {
            headers
                .entry(unicode::fold(name))
                .or_default()
                .push(value.trim().into());
        }
    }
    Ok(headers)
}
fn strict_csv(text: &str) -> Result<(), bool> {
    #[derive(Clone, Copy)]
    enum State {
        Start,
        Plain,
        Quoted,
        AfterQuote,
    }
    let mut state = State::Start;
    let mut nonempty = false;
    let mut blank = false;
    let bytes = text.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        match state {
            State::Quoted => {
                if byte == b'"' {
                    state = State::AfterQuote;
                }
            }
            State::AfterQuote => {
                if byte == b'"' {
                    state = State::Quoted;
                } else if byte == b',' {
                    state = State::Start;
                } else if !matches!(byte, b'\r' | b'\n') {
                    return Err(false);
                }
            }
            State::Start => {
                if byte == b'"' {
                    state = State::Quoted;
                } else if byte != b',' && !matches!(byte, b'\r' | b'\n') {
                    state = State::Plain;
                }
            }
            State::Plain => {
                if byte == b',' {
                    state = State::Start;
                }
            }
        }
        if matches!(byte, b'\r' | b'\n') && !matches!(state, State::Quoted) {
            if !nonempty {
                blank = true;
            }
            if byte == b'\r' && bytes.get(index + 1).is_some_and(|next| *next != b'\n') {
                return Err(false);
            }
            state = State::Start;
            nonempty = false;
            if byte == b'\r' && bytes.get(index + 1) == Some(&b'\n') {
                index += 1;
            }
        } else {
            nonempty = true;
        }
        index += 1;
    }
    if matches!(state, State::Quoted) {
        Err(false)
    } else if blank {
        Err(true)
    } else {
        Ok(())
    }
}
fn records(payload: Vec<u8>, member: &str) -> Result<BTreeMap<String, (String, String)>, String> {
    let text = String::from_utf8(payload)
        .map_err(|_| format!("wheel RECORD could not be parsed: {member}"))?;
    if let Err(blank) = strict_csv(&text) {
        return Err(if blank {
            format!("wheel RECORD contains a malformed row: {member}")
        } else {
            format!("wheel RECORD could not be parsed: {member}")
        });
    }
    // csv-core strips an initial UTF-8 BOM; CPython deliberately retains it in
    // field one. A complete dummy record preserves the original CSV grammar.
    let text = format!("x,x,x\n{text}");
    let mut reader = csv::ReaderBuilder::new()
        .has_headers(false)
        .flexible(true)
        .from_reader(text.as_bytes());
    let mut result = BTreeMap::new();
    for (index, row) in reader.records().enumerate() {
        let row = row.map_err(|_| format!("wheel RECORD could not be parsed: {member}"))?;
        if index == 0 {
            continue;
        }
        if row.len() != 3 || !safe_name(&row[0]) {
            return Err(format!("wheel RECORD contains a malformed row: {member}"));
        }
        if result
            .insert(row[0].to_owned(), (row[1].to_owned(), row[2].to_owned()))
            .is_some()
        {
            return Err(format!("wheel RECORD contains a duplicate path: {member}"));
        }
    }
    Ok(result)
}
fn integer(value: &str) -> Option<u64> {
    let value = value.trim();
    let (negative, value) = if let Some(value) = value.strip_prefix('-') {
        (true, value)
    } else {
        (false, value.strip_prefix('+').unwrap_or(value))
    };
    let mut total = 0u64;
    let mut digit = false;
    let mut digits = 0;
    for character in value.chars() {
        if character == '_' {
            if !digit {
                return None;
            }
            digit = false;
            continue;
        }
        let next = unicode::decimal_digit(character)? as u64;
        digits += 1;
        if digits > 4300 {
            return None;
        }
        total = total.checked_mul(10)?.checked_add(next)?;
        digit = true;
    }
    if !digit || (negative && total != 0) {
        None
    } else {
        Some(total)
    }
}
fn record_violations(
    archive: &mut Archive,
    record: usize,
    rows: &BTreeMap<String, (String, String)>,
    cancellation: Option<&AtomicBool>,
) -> Result<Vec<String>, String> {
    let name = archive.members[record].name.clone();
    let signatures = BTreeSet::from([format!("{name}.jws"), format!("{name}.p7s")]);
    let names: BTreeSet<_> = archive
        .members
        .iter()
        .map(|member| member.name.as_str())
        .collect();
    let mut violations = vec![];
    let missing = names
        .iter()
        .filter(|name| !signatures.contains(**name) && !rows.contains_key(**name))
        .count();
    let extra = rows
        .keys()
        .filter(|name| !names.contains(name.as_str()))
        .count();
    if missing > 0 {
        violations.push(format!("omits {missing} member(s)"));
    }
    if extra > 0 {
        violations.push(format!("references {extra} absent member(s)"));
    }
    for index in 0..archive.members.len() {
        if crate::cancellation_requested(cancellation) {
            return Err("analysis cancelled".into());
        }
        let member = &archive.members[index];
        if signatures.contains(&member.name) {
            continue;
        }
        let Some((hash, size)) = rows.get(&member.name) else {
            continue;
        };
        if index == record {
            if !hash.is_empty() || !size.is_empty() {
                violations.push("hashes or sizes RECORD itself".into());
            }
            continue;
        }
        let parsed = hash.split_once('=').filter(|(algorithm, digest)| {
            matches!(*algorithm, "sha256" | "sha384" | "sha512")
                && !digest.is_empty()
                && digest.chars().all(|character| {
                    character.is_ascii_alphanumeric() || matches!(character, '_' | '-')
                })
        });
        if parsed.is_none() || integer(size) != Some(member.size) {
            violations.push(format!(
                "has invalid hash/size metadata for {}",
                member.name
            ));
            continue;
        }
        let (algorithm, expected) = parsed.unwrap();
        let name = member.name.clone();
        if archive.digest(index, algorithm, cancellation)? != expected {
            violations.push(format!("has a digest mismatch for {name}"));
        }
    }
    Ok(violations)
}
fn ini_matches(
    payload: Vec<u8>,
    member: &str,
    expected: &[(String, String)],
) -> Result<bool, String> {
    let invalid = || format!("wheel entry_points.txt could not be parsed: {member}");
    let text = String::from_utf8(payload).map_err(|_| invalid())?;
    let mut sections: BTreeMap<String, BTreeMap<String, String>> = BTreeMap::new();
    let mut defaults = BTreeMap::new();
    let mut section: Option<String> = None;
    let mut option: Option<(String, usize)> = None;
    let mut seen = BTreeSet::new();
    for raw in text.lines() {
        let value = raw.trim();
        if value.starts_with(['#', ';']) {
            continue;
        }
        if value.is_empty() {
            if let Some((name, _)) = &option {
                let table = if section.as_deref() == Some("DEFAULT") {
                    &mut defaults
                } else {
                    sections
                        .get_mut(section.as_deref().ok_or_else(invalid)?)
                        .ok_or_else(invalid)?
                };
                table.get_mut(name).ok_or_else(invalid)?.push('\n');
            }
            continue;
        }
        let indent = raw
            .chars()
            .take_while(|character| character.is_whitespace())
            .count();
        if let Some((name, previous)) = &option
            && indent > *previous
        {
            let table = if section.as_deref() == Some("DEFAULT") {
                &mut defaults
            } else {
                sections
                    .get_mut(section.as_deref().ok_or_else(invalid)?)
                    .ok_or_else(invalid)?
            };
            let target = table.get_mut(name).ok_or_else(invalid)?;
            target.push('\n');
            target.push_str(value);
            continue;
        }
        if value.starts_with('[')
            && let Some(end) = value.rfind(']')
        {
            let name = &value[1..end];
            if name.is_empty() {
                return Err(invalid());
            }
            if name != "DEFAULT" && sections.insert(name.into(), BTreeMap::new()).is_some() {
                return Err(invalid());
            }
            section = Some(name.into());
            option = None;
            continue;
        }
        let group = section.as_deref().ok_or_else(invalid)?;
        let separator = value.find(['=', ':']).ok_or_else(invalid)?;
        let name = value[..separator].trim_end();
        if name.is_empty() || !seen.insert((group.to_owned(), name.to_owned())) {
            return Err(invalid());
        }
        let table = if group == "DEFAULT" {
            &mut defaults
        } else {
            sections.get_mut(group).ok_or_else(invalid)?
        };
        table.insert(name.into(), value[separator + 1..].trim().into());
        option = Some((name.into(), indent));
    }
    let mut total = sections
        .len()
        .checked_mul(defaults.len())
        .ok_or_else(invalid)?;
    for values in sections.values() {
        let overridden = values
            .keys()
            .filter(|name| defaults.contains_key(*name))
            .count();
        total = total
            .checked_add(values.len() - overridden)
            .ok_or_else(invalid)?;
    }
    if total != expected.len() {
        return Ok(false);
    }
    // Compare inherited defaults without expanding their values once per
    // section. A 1 MiB INI can otherwise amplify into unbounded copied text.
    let expected: BTreeMap<_, _> = expected
        .iter()
        .map(|(name, value)| (name.as_str(), value.as_str()))
        .collect();
    if expected.len() != total {
        return Ok(false);
    }
    for value in defaults
        .values_mut()
        .chain(sections.values_mut().flat_map(BTreeMap::values_mut))
    {
        *value = value.trim().to_owned();
    }
    for (group, values) in &sections {
        for (name, value) in defaults
            .iter()
            .filter(|(name, _)| !values.contains_key(*name))
            .chain(values)
        {
            if expected.get(format!("{group}:{name}").as_str()).copied() != Some(value.as_str()) {
                return Ok(false);
            }
        }
    }
    Ok(true)
}
pub fn inspect(
    request: &AnalysisRequest,
    input: &SourceInput,
    package: &Package<'_>,
    policy: &Policy,
    cancellation: Option<&AtomicBool>,
) -> Result<(Vec<Finding>, Evidence), String> {
    let path = &input.path;
    let identity = filename(path)?;
    let mut archive = Archive::new(&input.bytes, path, policy)?;
    let mut findings = vec![];
    let find = |suffix: &str| {
        archive
            .members
            .iter()
            .enumerate()
            .filter(|(_, member)| member.name.ends_with(suffix))
            .map(|(index, _)| index)
            .collect::<Vec<_>>()
    };
    let wheels = find(".dist-info/WHEEL");
    let metadata = find(".dist-info/METADATA");
    let records_index = find(".dist-info/RECORD");
    let required =
        || format!("wheel must contain exactly one WHEEL, METADATA, and RECORD file: {path}");
    if wheels.len() != 1 || metadata.len() != 1 || records_index.len() != 1 {
        return Err(required());
    }
    let mut directories = BTreeSet::new();
    for member in &archive.members {
        let mut prefix = String::new();
        for part in member.name.split('/') {
            if !prefix.is_empty() {
                prefix.push('/');
            }
            prefix.push_str(part);
            if part.ends_with(".dist-info") {
                directories.insert(prefix.clone());
                if directories.len() > 1 {
                    break;
                }
            }
        }
        if directories.len() > 1 {
            break;
        }
    }
    if directories.len() != 1 {
        return Err(format!(
            "wheel must contain exactly one .dist-info directory: {path}"
        ));
    }
    let directory = directories.into_iter().next().unwrap();
    if directory.contains('/') {
        return Err(format!(
            "wheel .dist-info directory must be at the archive root: {path}"
        ));
    }
    let wheel = wheels[0];
    let metadata = metadata[0];
    let record = records_index[0];
    for (index, name) in [(wheel, "WHEEL"), (metadata, "METADATA"), (record, "RECORD")] {
        if archive.members[index].name != format!("{directory}/{name}") {
            return Err(required());
        }
    }
    let matched = directory
        .strip_suffix(".dist-info")
        .and_then(|stem| stem.rsplit_once('-'))
        .is_some_and(|(name, version)| {
            package::canonical_name(name) == identity.name
                && Version::from_str(version).is_ok_and(|version| version == identity.version)
        });
    let add = |findings: &mut Vec<Finding>, rule: &str, message: String, native: &str| {
        findings.push(package::issue(
            request, rule, path, 1, message, "high", native,
        ))
    };
    if !matched {
        add(
            &mut findings,
            "ici.package.wheel-metadata-mismatch",
            "Wheel .dist-info directory identity does not match its filename".into(),
            "wheel.dist-info-identity",
        );
    }
    let wheel_name = archive.members[wheel].name.clone();
    let metadata_name = archive.members[metadata].name.clone();
    let record_name = archive.members[record].name.clone();
    let wheel_headers = headers(archive.metadata(wheel, cancellation)?, &wheel_name)?;
    let metadata_headers = headers(archive.metadata(metadata, cancellation)?, &metadata_name)?;
    let mut duplicates: Vec<_> = ["name", "version"]
        .into_iter()
        .filter(|name| {
            metadata_headers
                .get(*name)
                .is_some_and(|values| values.len() > 1)
        })
        .collect();
    duplicates.extend(
        ["generator", "root-is-purelib", "wheel-version"]
            .into_iter()
            .filter(|name| {
                wheel_headers
                    .get(*name)
                    .is_some_and(|values| values.len() > 1)
            }),
    );
    if !duplicates.is_empty() {
        return Err(format!(
            "wheel contains duplicate singleton headers: {}",
            duplicates.join(", ")
        ));
    }
    let rows = records(archive.metadata(record, cancellation)?, &record_name)?;
    let violations = record_violations(&mut archive, record, &rows, cancellation)?;
    if !violations.is_empty() {
        add(
            &mut findings,
            "ici.package.wheel-record-integrity",
            format!(
                "Wheel RECORD integrity failed: {}",
                violations
                    .into_iter()
                    .take(3)
                    .collect::<Vec<_>>()
                    .join("; ")
            ),
            "wheel.record",
        );
    }
    let name = metadata_headers
        .get("name")
        .and_then(|values| values.first())
        .map_or("", String::as_str);
    let version = metadata_headers
        .get("version")
        .and_then(|values| values.first())
        .map_or("", String::as_str);
    if name.is_empty()
        || version.is_empty()
        || identity.name != package::canonical_name(&package.name)
        || (!package.version.is_empty()
            && (!Version::from_str(&package.version)
                .is_ok_and(|version| version == identity.version)
                || version != package.version))
        || package::canonical_name(name) != package::canonical_name(&package.name)
    {
        add(
            &mut findings,
            "ici.package.wheel-metadata-mismatch",
            "Wheel filename/METADATA identity does not match pyproject.toml".into(),
            "wheel.metadata-identity",
        );
    }
    let declared: BTreeSet<_> = wheel_headers
        .get("tag")
        .into_iter()
        .flatten()
        .cloned()
        .collect();
    if declared.is_empty() || !identity.tags.matches(&declared) {
        add(
            &mut findings,
            "ici.package.wheel-tag-mismatch",
            "Wheel filename tags do not exactly match WHEEL Tag headers".into(),
            "wheel.tags",
        );
    }
    let mut native: Vec<_> = archive
        .members
        .iter()
        .map(|member| &member.name)
        .filter(|name| {
            let name = unicode::fold(name);
            [".so", ".pyd", ".dll", ".dylib"]
                .iter()
                .any(|suffix| name.ends_with(suffix))
        })
        .cloned()
        .collect();
    native.sort();
    let root_pure = wheel_headers
        .get("root-is-purelib")
        .and_then(|values| values.first())
        .is_some_and(|value| unicode::fold(value) == "true");
    let pure = root_pure && identity.tags.pure() && native.is_empty();
    if policy.pure && !pure {
        add(
            &mut findings,
            "ici.package.native-wheel-forbidden",
            "Wheel violates the configured pure-Python policy".into(),
            "wheel.pure-policy",
        );
    }
    if policy.package_files {
        let names: BTreeSet<_> = archive.members.iter().map(|member| &member.name).collect();
        let missing = package
            .files
            .iter()
            .filter(|name| !names.contains(name))
            .count();
        if missing > 0 {
            add(
                &mut findings,
                "ici.package.package-files-missing",
                format!("Wheel omits {missing} discovered Python package file(s)"),
                "wheel.package-files",
            );
        }
    }
    if policy.entrypoints && !package.entrypoints.is_empty() {
        if let Some(index) = archive
            .members
            .iter()
            .position(|member| member.name == format!("{directory}/entry_points.txt"))
        {
            let member = archive.members[index].name.clone();
            if !ini_matches(
                archive.metadata(index, cancellation)?,
                &member,
                &package.entrypoints,
            )? {
                add(
                    &mut findings,
                    "ici.package.wheel-entrypoints-mismatch",
                    "Wheel entry_points.txt does not match declared project entry points".into(),
                    "wheel.entry-points",
                );
            }
        } else {
            add(
                &mut findings,
                "ici.package.wheel-entrypoints-missing",
                "Wheel omits entry_points.txt for declared project entry points".into(),
                "wheel.entry-points",
            );
        }
    }
    let evidence = Evidence {
        path: path.clone(),
        members: archive.members.len(),
        uncompressed_bytes: archive.bytes,
        pure,
        native_members: native,
        direct_url: archive
            .members
            .iter()
            .any(|member| member.name.ends_with(".dist-info/direct_url.json")),
        build_details: archive
            .members
            .iter()
            .any(|member| member.name.ends_with(".dist-info/build-details.json")),
    };
    Ok((findings, evidence))
}
