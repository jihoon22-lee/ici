//! Bounded ZIP structure before a library can allocate or deduplicate members.
use crate::{package::Policy, unicode};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use sha2::{Digest, Sha256, Sha384, Sha512};
use std::{
    collections::BTreeSet,
    io::{Cursor, Read},
    sync::atomic::AtomicBool,
};
use zip::ZipArchive;

pub struct Member {
    pub name: String,
    pub size: u64,
    pub flags: u16,
    pub attributes: u32,
}
pub struct Archive {
    pub zip: ZipArchive<Cursor<Vec<u8>>>,
    pub members: Vec<Member>,
    pub bytes: u64,
}
fn u16le(bytes: &[u8], offset: usize) -> Option<u16> {
    Some(u16::from_le_bytes(
        bytes.get(offset..offset.checked_add(2)?)?.try_into().ok()?,
    ))
}
fn u32le(bytes: &[u8], offset: usize) -> Option<u32> {
    Some(u32::from_le_bytes(
        bytes.get(offset..offset.checked_add(4)?)?.try_into().ok()?,
    ))
}
fn u64le(bytes: &[u8], offset: usize) -> Option<u64> {
    Some(u64::from_le_bytes(
        bytes.get(offset..offset.checked_add(8)?)?.try_into().ok()?,
    ))
}
pub fn safe_name(value: &str) -> bool {
    let stem = value.strip_suffix('/').unwrap_or(value);
    !stem.is_empty()
        && !value.contains(['\\', '\0'])
        && !stem.starts_with('/')
        && stem.split('/').all(|part| !matches!(part, "" | "." | ".."))
}
pub(crate) fn decode_name(bytes: &[u8], flags: u16) -> Option<String> {
    if flags & 0x800 != 0 {
        std::str::from_utf8(bytes).ok().map(str::to_owned)
    } else {
        Some(
            bytes
                .iter()
                .map(|byte| {
                    if *byte < 128 {
                        *byte as char
                    } else {
                        CP437_HIGH[(*byte - 128) as usize]
                    }
                })
                .collect(),
        )
    }
}
struct Directory {
    start: usize,
    end: usize,
    count: u64,
    prefix: u64,
}
fn directory(bytes: &[u8]) -> Option<Directory> {
    let first = bytes.len().saturating_sub(65_557);
    let end = (first..bytes.len().saturating_sub(21))
        .rev()
        .find(|offset| {
            bytes.get(*offset..*offset + 4) == Some(b"PK\x05\x06")
                && u16le(bytes, *offset + 20)
                    .is_some_and(|length| *offset + 22 + length as usize <= bytes.len())
        })?;
    if u16le(bytes, end + 4)? != 0 || u16le(bytes, end + 6)? != 0 {
        return None;
    }
    let mut count = u16le(bytes, end + 10)? as u64;
    let mut size = u32le(bytes, end + 12)? as u64;
    let mut offset = u32le(bytes, end + 16)? as u64;
    let mut central_end = end;
    if count == u16::MAX as u64
        || size == u32::MAX as u64
        || offset == u32::MAX as u64
        || end
            .checked_sub(20)
            .is_some_and(|locator| bytes.get(locator..locator + 4) == Some(b"PK\x06\x07"))
    {
        let locator = end.checked_sub(20)?;
        if bytes.get(locator..locator + 4) != Some(b"PK\x06\x07")
            || u32le(bytes, locator + 4)? != 0
            || u32le(bytes, locator + 16)? != 1
        {
            return None;
        }
        let declared = usize::try_from(u64le(bytes, locator + 8)?).ok()?;
        let record = if bytes.get(declared..declared.checked_add(4)?) == Some(b"PK\x06\x06") {
            declared
        } else {
            (0..locator.saturating_sub(11)).rev().find(|position| {
                bytes.get(*position..*position + 4) == Some(b"PK\x06\x06")
                    && u64le(bytes, *position + 4)
                        .and_then(|length| usize::try_from(length).ok())
                        .is_some_and(|length| {
                            position
                                .checked_add(12)
                                .and_then(|value| value.checked_add(length))
                                == Some(locator)
                        })
            })?
        };
        if u64le(bytes, record + 4)? < 44
            || u32le(bytes, record + 16)? != 0
            || u32le(bytes, record + 20)? != 0
        {
            return None;
        }
        count = u64le(bytes, record + 32)?;
        if u64le(bytes, record + 24)? != count {
            return None;
        }
        size = u64le(bytes, record + 40)?;
        offset = u64le(bytes, record + 48)?;
        central_end = record;
    } else if u16le(bytes, end + 8)? as u64 != count {
        return None;
    }
    let start = central_end.checked_sub(usize::try_from(size).ok()?)?;
    let prefix = (start as u64).checked_sub(offset)?;
    Some(Directory {
        start,
        end: central_end,
        count,
        prefix,
    })
}
fn extras<'a>(
    bytes: &'a [u8],
    start: usize,
    end: usize,
    patches: &mut Vec<usize>,
) -> Option<Option<&'a [u8]>> {
    let mut position = start;
    let mut zip64 = None;
    while position < end {
        let kind = u16le(bytes, position)?;
        let size = u16le(bytes, position + 2)? as usize;
        let next = position.checked_add(4)?.checked_add(size)?;
        if next > end {
            return None;
        }
        if kind == 1 {
            if zip64.is_some() {
                return None;
            }
            zip64 = Some(bytes.get(position + 4..next)?);
        }
        // Unicode paths are applied by our preflight before duplicate/path
        // validation. The decompressor uses untouched original member names;
        // this prevents its internal name map from hiding duplicate entries.
        if matches!(kind, 0x7075 | 0x6375) {
            patches.push(position);
        }
        position = next;
    }
    Some(zip64)
}
fn unicode_path(
    bytes: &[u8],
    start: usize,
    end: usize,
    raw: &[u8],
    mut name: String,
) -> Option<String> {
    let mut position = start;
    let mut crc = None;
    while position < end {
        let kind = u16le(bytes, position)?;
        let size = u16le(bytes, position + 2)? as usize;
        let data = bytes.get(position + 4..position + 4 + size)?;
        if kind == 0x7075 {
            let version = *data.first()?;
            let expected = u32le(data, 1)?;
            if version == 1 && expected == *crc.get_or_insert_with(|| crc32fast::hash(raw)) {
                let value = std::str::from_utf8(data.get(5..)?).ok()?;
                if !value.is_empty() {
                    name = value.to_owned();
                }
            }
        }
        position += 4 + size;
    }
    Some(name)
}
fn preflight(
    bytes: &[u8],
    relative: &str,
    policy: &Policy,
) -> Result<(Vec<Member>, Vec<usize>), String> {
    let malformed = || format!("wheel is not a readable ZIP archive: {relative}");
    let directory = directory(bytes).ok_or_else(malformed)?;
    if directory.count > policy.max_members as u64 {
        return Err(format!(
            "wheel member count exceeds the configured bound: {relative}"
        ));
    }
    let mut members = vec![];
    let mut patches = vec![];
    let mut ranges = vec![];
    let mut position = directory.start;
    while position < directory.end {
        if members.len() >= policy.max_members {
            return Err(format!(
                "wheel member count exceeds the configured bound: {relative}"
            ));
        }
        if bytes.get(position..position + 4) != Some(b"PK\x01\x02") {
            return Err(malformed());
        }
        let flags = u16le(bytes, position + 8).ok_or_else(malformed)?;
        let mut compressed = u32le(bytes, position + 20).ok_or_else(malformed)? as u64;
        let mut size = u32le(bytes, position + 24).ok_or_else(malformed)? as u64;
        let length = u16le(bytes, position + 28).ok_or_else(malformed)? as usize;
        let extra = u16le(bytes, position + 30).ok_or_else(malformed)? as usize;
        let comment = u16le(bytes, position + 32).ok_or_else(malformed)? as usize;
        let attributes = u32le(bytes, position + 38).ok_or_else(malformed)?;
        let mut local = u32le(bytes, position + 42).ok_or_else(malformed)? as u64;
        let name_start = position.checked_add(46).ok_or_else(malformed)?;
        let extra_start = name_start.checked_add(length).ok_or_else(malformed)?;
        let extra_end = extra_start.checked_add(extra).ok_or_else(malformed)?;
        let next = extra_end.checked_add(comment).ok_or_else(malformed)?;
        if next > directory.end {
            return Err(malformed());
        }
        let zip64 = extras(bytes, extra_start, extra_end, &mut patches).ok_or_else(malformed)?;
        let mut cursor = 0;
        if size == u32::MAX as u64 {
            size = u64le(zip64.ok_or_else(malformed)?, cursor).ok_or_else(malformed)?;
            cursor += 8;
        }
        if compressed == u32::MAX as u64 {
            compressed = u64le(zip64.ok_or_else(malformed)?, cursor).ok_or_else(malformed)?;
            cursor += 8;
        }
        if local == u32::MAX as u64 {
            local = u64le(zip64.ok_or_else(malformed)?, cursor).ok_or_else(malformed)?;
        }
        let raw = bytes.get(name_start..extra_start).ok_or_else(malformed)?;
        let name = decode_name(raw, flags)
            .ok_or_else(|| format!("wheel member name is not valid UTF-8: {relative}"))?;
        let name = unicode_path(bytes, extra_start, extra_end, raw, name).ok_or_else(malformed)?;
        let local = usize::try_from(local.checked_add(directory.prefix).ok_or_else(malformed)?)
            .map_err(|_| malformed())?;
        if bytes.get(local..local.checked_add(4).ok_or_else(malformed)?) != Some(b"PK\x03\x04") {
            return Err(malformed());
        }
        let local_flags = u16le(bytes, local + 6).ok_or_else(malformed)?;
        let local_name_length = u16le(bytes, local + 26).ok_or_else(malformed)? as usize;
        let local_extra_length = u16le(bytes, local + 28).ok_or_else(malformed)? as usize;
        let local_name = local.checked_add(30).ok_or_else(malformed)?;
        let local_extra = local_name
            .checked_add(local_name_length)
            .ok_or_else(malformed)?;
        if bytes.get(local_name..local_extra) != Some(raw) || local_flags & 0x800 != flags & 0x800 {
            return Err(format!(
                "wheel local and central member names disagree: {relative}"
            ));
        }
        extras(
            bytes,
            local_extra,
            local_extra
                .checked_add(local_extra_length)
                .filter(|end| *end <= directory.start)
                .ok_or_else(malformed)?,
            &mut patches,
        )
        .ok_or_else(malformed)?;
        let data_start = local_extra
            .checked_add(local_extra_length)
            .ok_or_else(malformed)? as u64;
        let data_end = data_start.checked_add(compressed).ok_or_else(malformed)?;
        ranges.push((local as u64, data_end));
        members.push(Member {
            name,
            size,
            flags,
            attributes,
        });
        position = next;
    }
    if members.len() as u64 != directory.count {
        return Err(malformed());
    }
    let names: BTreeSet<_> = members.iter().map(|member| &member.name).collect();
    if names.len() != members.len() {
        return Err(format!("wheel contains duplicate member names: {relative}"));
    }
    let portable: BTreeSet<_> = members
        .iter()
        .map(|member| unicode::portable(&member.name))
        .collect();
    if portable.len() != members.len() {
        return Err(format!(
            "wheel contains names that collide on portable filesystems: {relative}"
        ));
    }
    if members.iter().any(|member| !safe_name(&member.name)) {
        return Err(format!("wheel contains an unsafe member path: {relative}"));
    }
    for member in &members {
        if member.name.ends_with('/') && member.size != 0 {
            return Err(format!(
                "wheel contains a non-empty directory member: {relative}"
            ));
        }
        let kind = (member.attributes >> 16) & 0xf000;
        if !matches!(kind, 0 | 0x8000 | 0x4000) {
            return Err(format!(
                "wheel contains a symlink or special member: {relative}"
            ));
        }
    }
    if members.iter().any(|member| member.flags & 1 != 0) {
        return Err(format!(
            "encrypted wheel members are unsupported: {relative}"
        ));
    }
    let total = members
        .iter()
        .try_fold(0u64, |total, member| total.checked_add(member.size));
    if total.is_none_or(|total| total > policy.max_bytes) {
        return Err(format!(
            "wheel uncompressed size exceeds the configured bound: {relative}"
        ));
    }
    ranges.sort_unstable();
    for (index, (_, end)) in ranges.iter().enumerate() {
        let limit = ranges
            .get(index + 1)
            .map_or(directory.start as u64, |(start, _)| *start);
        if *end > limit {
            return Err(format!("wheel contains overlapping members: {relative}"));
        }
    }
    Ok((members, patches))
}
impl Archive {
    pub fn new(bytes: &[u8], relative: &str, policy: &Policy) -> Result<Self, String> {
        let (members, patches) = preflight(bytes, relative, policy)?;
        let total = members.iter().map(|member| member.size).sum();
        let mut payload = bytes.to_vec();
        for offset in patches {
            payload[offset..offset + 2].copy_from_slice(&0xffffu16.to_le_bytes());
        }
        let zip = ZipArchive::new(Cursor::new(payload))
            .map_err(|_| format!("wheel is not a readable ZIP archive: {relative}"))?;
        if zip.len() != members.len() {
            return Err(format!("wheel contains duplicate member names: {relative}"));
        }
        Ok(Self {
            zip,
            members,
            bytes: total,
        })
    }
    pub fn metadata(
        &mut self,
        index: usize,
        cancellation: Option<&AtomicBool>,
    ) -> Result<Vec<u8>, String> {
        let member = &self.members[index];
        if member.size > 1024 * 1024 {
            return Err(format!(
                "wheel metadata member is too large: {}",
                member.name
            ));
        }
        let mut file = self
            .zip
            .by_index(index)
            .map_err(|_| format!("wheel member could not be read: {}", member.name))?;
        let mut bytes = Vec::with_capacity(member.size as usize);
        let mut buffer = [0; 65_536];
        loop {
            if crate::cancellation_requested(cancellation) {
                return Err("analysis cancelled".into());
            }
            let count = file
                .read(&mut buffer)
                .map_err(|_| format!("wheel member could not be read: {}", member.name))?;
            if count == 0 {
                break;
            }
            if bytes.len() as u64 + count as u64 > member.size {
                return Err(format!(
                    "wheel member size changed while reading: {}",
                    member.name
                ));
            }
            bytes.extend_from_slice(&buffer[..count]);
        }
        if bytes.len() as u64 != member.size {
            return Err(format!(
                "wheel member size changed while reading: {}",
                member.name
            ));
        }
        Ok(bytes)
    }
    pub fn digest(
        &mut self,
        index: usize,
        algorithm: &str,
        cancellation: Option<&AtomicBool>,
    ) -> Result<String, String> {
        enum Hash {
            H256(Sha256),
            H384(Sha384),
            H512(Sha512),
        }
        let mut hash = match algorithm {
            "sha256" => Hash::H256(Sha256::new()),
            "sha384" => Hash::H384(Sha384::new()),
            "sha512" => Hash::H512(Sha512::new()),
            _ => unreachable!("validated RECORD algorithm"),
        };
        let member = &self.members[index];
        let mut file = self
            .zip
            .by_index(index)
            .map_err(|_| format!("wheel member could not be hashed: {}", member.name))?;
        let mut bytes = 0u64;
        let mut buffer = [0; 65_536];
        loop {
            if crate::cancellation_requested(cancellation) {
                return Err("analysis cancelled".into());
            }
            let count = file
                .read(&mut buffer)
                .map_err(|_| format!("wheel member could not be hashed: {}", member.name))?;
            if count == 0 {
                break;
            }
            bytes += count as u64;
            if bytes > member.size {
                return Err(format!(
                    "wheel member expanded beyond its declared size: {}",
                    member.name
                ));
            }
            match &mut hash {
                Hash::H256(hash) => hash.update(&buffer[..count]),
                Hash::H384(hash) => hash.update(&buffer[..count]),
                Hash::H512(hash) => hash.update(&buffer[..count]),
            }
        }
        if bytes != member.size {
            return Err(format!(
                "wheel member size changed while hashing: {}",
                member.name
            ));
        }
        let digest = match hash {
            Hash::H256(hash) => hash.finalize().to_vec(),
            Hash::H384(hash) => hash.finalize().to_vec(),
            Hash::H512(hash) => hash.finalize().to_vec(),
        };
        Ok(URL_SAFE_NO_PAD.encode(digest))
    }
}

// Generated from the CPython 3.14 CP437 codec; verified across all 256 bytes.
const CP437_HIGH: [char; 128] = [
    '\u{c7}', '\u{fc}', '\u{e9}', '\u{e2}', '\u{e4}', '\u{e0}', '\u{e5}', '\u{e7}', '\u{ea}',
    '\u{eb}', '\u{e8}', '\u{ef}', '\u{ee}', '\u{ec}', '\u{c4}', '\u{c5}', '\u{c9}', '\u{e6}',
    '\u{c6}', '\u{f4}', '\u{f6}', '\u{f2}', '\u{fb}', '\u{f9}', '\u{ff}', '\u{d6}', '\u{dc}',
    '\u{a2}', '\u{a3}', '\u{a5}', '\u{20a7}', '\u{192}', '\u{e1}', '\u{ed}', '\u{f3}', '\u{fa}',
    '\u{f1}', '\u{d1}', '\u{aa}', '\u{ba}', '\u{bf}', '\u{2310}', '\u{ac}', '\u{bd}', '\u{bc}',
    '\u{a1}', '\u{ab}', '\u{bb}', '\u{2591}', '\u{2592}', '\u{2593}', '\u{2502}', '\u{2524}',
    '\u{2561}', '\u{2562}', '\u{2556}', '\u{2555}', '\u{2563}', '\u{2551}', '\u{2557}', '\u{255d}',
    '\u{255c}', '\u{255b}', '\u{2510}', '\u{2514}', '\u{2534}', '\u{252c}', '\u{251c}', '\u{2500}',
    '\u{253c}', '\u{255e}', '\u{255f}', '\u{255a}', '\u{2554}', '\u{2569}', '\u{2566}', '\u{2560}',
    '\u{2550}', '\u{256c}', '\u{2567}', '\u{2568}', '\u{2564}', '\u{2565}', '\u{2559}', '\u{2558}',
    '\u{2552}', '\u{2553}', '\u{256b}', '\u{256a}', '\u{2518}', '\u{250c}', '\u{2588}', '\u{2584}',
    '\u{258c}', '\u{2590}', '\u{2580}', '\u{3b1}', '\u{df}', '\u{393}', '\u{3c0}', '\u{3a3}',
    '\u{3c3}', '\u{b5}', '\u{3c4}', '\u{3a6}', '\u{398}', '\u{3a9}', '\u{3b4}', '\u{221e}',
    '\u{3c6}', '\u{3b5}', '\u{2229}', '\u{2261}', '\u{b1}', '\u{2265}', '\u{2264}', '\u{2320}',
    '\u{2321}', '\u{f7}', '\u{2248}', '\u{b0}', '\u{2219}', '\u{b7}', '\u{221a}', '\u{207f}',
    '\u{b2}', '\u{25a0}', '\u{a0}',
];
