//! Bounded producer facts for native binaries, libraries and regular SONAME alias copies.
use super::{PreparedContext, build_metadata_path, relative};
use ici_config::{Component, ResolvedProject};
use ici_domain::sha256;
use std::{
    collections::BTreeMap,
    fs,
    io::Read,
    os::unix::fs::{MetadataExt, PermissionsExt},
    path::Path,
};

/// Measured primary-build inputs for the shared release packager. Aliases copy canonical bytes.
pub fn compiled_artifacts(
    project: &ResolvedProject,
    component: &Component,
    contexts: &[PreparedContext],
) -> Result<Vec<crate::artifact_build::ArtifactSource>, String> {
    let preferred = crate::cpp::linked_build(project, component)
        .map(|build| build.variant.as_str())
        .unwrap_or("release");
    let candidates = contexts
        .iter()
        .filter(|context| {
            context.plan.component_id == component.id && context.plan.build.variant == preferred
        })
        .collect::<Vec<_>>();
    let context = candidates
        .first()
        .ok_or("the primary compiled build context was not prepared")?;
    if candidates.iter().any(|candidate| {
        candidate.plan.root != context.plan.root
            || candidate.plan.build.owner_digest != context.plan.build.owner_digest
    }) {
        return Err("primary compiled build context is ambiguous".into());
    }
    if context.plan.root != project.root {
        return Err("compiled producer belongs to another workspace".into());
    }
    let shadow = project.root.join(&context.plan.build.shadow);
    let entries = crate::files::tree(&project.root, &shadow).map_err(|error| error.to_string())?;
    let explicit = context
        .plan
        .declared_build
        .as_ref()
        .filter(|build| !build.artifacts.is_empty());
    let allowed = if let Some(build) = explicit {
        let view = Component {
            root: context.plan.build.shadow.clone(),
            ..component.clone()
        };
        Some(
            ici_config::matching_source_paths(
                &view,
                &entries
                    .iter()
                    .map(|entry| entry.relative.clone())
                    .collect::<Vec<_>>(),
                &build.artifacts,
            )
            .map_err(|error| error.to_string())?
            .into_iter()
            .collect::<std::collections::BTreeSet<_>>(),
        )
    } else {
        None
    };
    let mut records = BTreeMap::new();
    let mut total = 0u64;
    let mut alias_sources = std::collections::BTreeSet::new();
    for entry in &entries {
        let path = project.root.join(&entry.relative);
        if entry.contained
            && allowed
                .as_ref()
                .is_none_or(|allowed| allowed.contains(&entry.relative))
            && fs::symlink_metadata(&path).is_ok_and(|metadata| metadata.is_symlink())
        {
            let canonical = path.canonicalize().map_err(|error| error.to_string())?;
            if canonical.starts_with(&shadow) {
                alias_sources.insert(relative(&project.root, &canonical)?);
            }
        }
    }
    for entry in &entries {
        if !entry.file
            || !entry.contained
            || allowed.as_ref().is_some_and(|allowed| {
                !allowed.contains(&entry.relative) && !alias_sources.contains(&entry.relative)
            })
            || allowed.is_none() && build_metadata_path(&entry.relative, &context.plan.build.shadow)
        {
            continue;
        }
        let path = project.root.join(&entry.relative);
        if fs::symlink_metadata(&path)
            .map_err(|error| error.to_string())?
            .is_symlink()
        {
            continue;
        }
        let Some((bytes, metadata)) = artifact_bytes(&path)? else {
            continue;
        };
        let Some(kind) = artifact_bytes_kind(&bytes)? else {
            continue;
        };
        total = total
            .checked_add(metadata.len())
            .ok_or("compiled artifact size overflow")?;
        if total > 512 * 1024 * 1024 || records.len() >= 4096 {
            return Err("compiled artifacts exceed 4096 files or 512 MiB aggregate".into());
        }
        records.insert(
            entry.relative.clone(),
            crate::artifact_build::ArtifactSource {
                path: entry.relative.clone(),
                kind: kind.into(),
                sha256: sha256(&bytes),
                size: metadata.len(),
                mode: metadata.permissions().mode() & 0o777,
                destination_name: None,
            },
        );
    }
    let mut aliases = vec![];
    for entry in entries {
        let path = project.root.join(&entry.relative);
        if !entry.contained
            || allowed
                .as_ref()
                .is_some_and(|allowed| !allowed.contains(&entry.relative))
            || allowed.is_none() && build_metadata_path(&entry.relative, &context.plan.build.shadow)
            || !fs::symlink_metadata(&path).is_ok_and(|metadata| metadata.is_symlink())
        {
            continue;
        }
        let target = path.canonicalize().map_err(|error| error.to_string())?;
        if !target.starts_with(&shadow) {
            continue;
        }
        let source = relative(&project.root, &target)?;
        if let Some(record) = records.get(&source) {
            let mut alias = record.clone();
            alias.destination_name = Some(
                path.file_name()
                    .and_then(|name| name.to_str())
                    .ok_or("compiled alias name is not UTF-8")?
                    .into(),
            );
            aliases.push(alias);
        }
    }
    let mut outputs = records
        .into_iter()
        .filter(|(path, _)| {
            allowed
                .as_ref()
                .is_none_or(|allowed| allowed.contains(path))
        })
        .map(|(_, record)| record)
        .collect::<Vec<_>>();
    outputs.extend(aliases);
    if outputs.len() > 4096 {
        return Err("compiled artifacts and aliases exceed 4096 records".into());
    }
    Ok(outputs)
}

/// Classify linked ELF programs/libraries and bounded native archives from their actual bytes.
pub fn compiled_artifact_kind(path: &Path) -> Result<Option<&'static str>, String> {
    match artifact_bytes(path)? {
        Some((bytes, _)) => artifact_bytes_kind(&bytes),
        None => Ok(None),
    }
}
fn artifact_bytes(path: &Path) -> Result<Option<(Vec<u8>, fs::Metadata)>, String> {
    let before = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    if !before.is_file() || path.canonicalize().map_err(|error| error.to_string())? != path {
        return Err("compiled artifact must be a regular file without symlink traversal".into());
    }
    let mut header = [0u8; 20];
    let length = usize::try_from(before.len().min(20)).map_err(|error| error.to_string())?;
    let mut file = fs::File::open(path).map_err(|error| error.to_string())?;
    file.read_exact(&mut header[..length])
        .map_err(|error| error.to_string())?;
    if !header[..length].starts_with(b"\x7fELF") && !header[..length].starts_with(b"!<arch>\n") {
        if header[..length].starts_with(b"!<thin>\n") {
            return Err("thin archives reference external members and cannot be published as standalone libraries".into());
        }
        return Ok(None);
    }
    if header[..length].starts_with(b"\x7fELF") && length >= 18 {
        let kind = match header[5] {
            1 => u16::from_le_bytes([header[16], header[17]]),
            2 => u16::from_be_bytes([header[16], header[17]]),
            _ => return Err("ELF data encoding is unsupported".into()),
        };
        if !matches!(kind, 2 | 3) {
            return Ok(None);
        }
    }
    if before.nlink() != 1 {
        return Err("compiled artifacts must not be multiply linked".into());
    }
    let bytes = ici_execution::storage::read_bounded_regular(path, 256 * 1024 * 1024)
        .map_err(|error| error.to_string())?;
    let after = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    let signature = |metadata: &fs::Metadata| {
        (
            metadata.dev(),
            metadata.ino(),
            metadata.len(),
            metadata.mode(),
            metadata.nlink(),
            metadata.mtime(),
            metadata.mtime_nsec(),
            metadata.ctime(),
            metadata.ctime_nsec(),
        )
    };
    if signature(&before) != signature(&after)
        || path.canonicalize().map_err(|error| error.to_string())? != path
    {
        return Err("compiled artifact changed while capturing producer evidence".into());
    }
    Ok(Some((bytes, after)))
}
fn artifact_bytes_kind(bytes: &[u8]) -> Result<Option<&'static str>, String> {
    if bytes.starts_with(b"!<arch>\n") {
        let mut offset = 8usize;
        let mut members = 0usize;
        while offset < bytes.len() {
            let end = offset.checked_add(60).ok_or("archive header overflow")?;
            let header = bytes
                .get(offset..end)
                .ok_or("native archive member header is truncated")?;
            if header[58..60] != [0x60, b'\n'] {
                return Err("native archive member trailer is invalid".into());
            }
            let size = std::str::from_utf8(&header[48..58])
                .map_err(|_| "native archive size is not ASCII")?
                .trim()
                .parse::<usize>()
                .map_err(|_| "native archive member size is invalid")?;
            offset = end
                .checked_add(size)
                .and_then(|end| end.checked_add(size % 2))
                .ok_or("archive member size overflow")?;
            members += 1;
            if offset > bytes.len() || members > 100000 {
                return Err("native archive exceeds its member or byte bounds".into());
            }
        }
        return Ok(Some("static-library"));
    }
    if !bytes.starts_with(b"\x7fELF") {
        return Ok(None);
    }
    let class = *bytes.get(4).ok_or("ELF header is truncated")?;
    let little = match bytes.get(5) {
        Some(1) => true,
        Some(2) => false,
        _ => return Err("ELF data encoding is unsupported".into()),
    };
    let number = |offset, width| read_number(bytes, offset, width, little);
    let (header_size, phoff, phsize, phnum, entry_size) = match class {
        1 => (52, number(28, 4)?, number(42, 2)?, number(44, 2)?, 8),
        2 => (64, number(32, 8)?, number(54, 2)?, number(56, 2)?, 16),
        _ => return Err("ELF class is unsupported".into()),
    };
    if bytes.len() < header_size || number(20, 4)? != 1 {
        return Err("ELF header version or size is invalid".into());
    }
    let kind = number(16, 2)?;
    if !matches!(kind, 2 | 3) {
        return Ok(None);
    }
    if phnum == 0 || phnum > 4096 || phsize < if class == 1 { 32 } else { 56 } || phsize > 4096 {
        return Err("ELF program header geometry is invalid or exceeds 4096 records".into());
    }
    let end = phoff
        .checked_add(
            phsize
                .checked_mul(phnum)
                .ok_or("ELF program table overflow")?,
        )
        .ok_or("ELF program table overflow")?;
    if end > bytes.len() as u64 {
        return Err("ELF program header table is truncated".into());
    }
    let mut executable = kind == 2;
    for index in 0..phnum {
        let start = usize::try_from(phoff + index * phsize).map_err(|error| error.to_string())?;
        let segment = number(start, 4)?;
        let offset = number(
            start + if class == 1 { 4 } else { 8 },
            if class == 1 { 4 } else { 8 },
        )?;
        let size = number(
            start + if class == 1 { 16 } else { 32 },
            if class == 1 { 4 } else { 8 },
        )?;
        let end = offset.checked_add(size).ok_or("ELF segment overflow")?;
        if end > bytes.len() as u64 {
            return Err("ELF file segment is truncated".into());
        }
        if segment == 3 {
            executable = true;
        }
        if segment == 2 {
            if size > 1024 * 1024 || size % entry_size != 0 {
                return Err("ELF dynamic table exceeds its bounds".into());
            }
            let mut position = offset;
            while position < end {
                let width = if class == 1 { 4 } else { 8 };
                let position_usize =
                    usize::try_from(position).map_err(|error| error.to_string())?;
                let tag = number(position_usize, width)?;
                let value = number(position_usize + width, width)?;
                if tag == 0 {
                    break;
                }
                if tag == 0x6fff_fffb && value & 0x0800_0000 != 0 {
                    executable = true;
                }
                position += entry_size;
            }
        }
    }
    Ok(Some(if executable {
        "executable"
    } else {
        "shared-library"
    }))
}
fn read_number(bytes: &[u8], offset: usize, width: usize, little: bool) -> Result<u64, String> {
    let slice = bytes
        .get(
            offset
                ..offset
                    .checked_add(width)
                    .ok_or("binary integer offset overflow")?,
        )
        .ok_or("binary integer is truncated")?;
    let mut value = 0u64;
    if little {
        for byte in slice.iter().rev() {
            value = (value << 8) | u64::from(*byte);
        }
    } else {
        for byte in slice {
            value = (value << 8) | u64::from(*byte);
        }
    }
    Ok(value)
}
