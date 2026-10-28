use crate::{
    files,
    model::{
        self, BuildInputs, BundleIdentity, BundleInput, Error, FileRecord, Manifest, error,
        io_error,
    },
};
use flate2::{Compression, GzBuilder, bufread::GzDecoder};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{self, BufReader, Read, Write},
    path::Path,
};

struct HashReader<R> {
    inner: R,
    hash: Sha256,
    size: u64,
    bound: u64,
}
impl<R: Read> HashReader<R> {
    fn new(inner: R, bound: u64) -> Self {
        Self {
            inner,
            hash: Sha256::new(),
            size: 0,
            bound,
        }
    }
    fn identity(&self) -> (u64, String) {
        (self.size, format!("{:x}", self.hash.clone().finalize()))
    }
}
impl<R: Read> Read for HashReader<R> {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        let count = self.inner.read(buffer)?;
        self.size = self
            .size
            .checked_add(count as u64)
            .ok_or_else(|| io::Error::other("distribution size overflow"))?;
        if self.size > self.bound {
            return Err(io::Error::other("distribution byte bound exceeded"));
        }
        self.hash.update(&buffer[..count]);
        Ok(count)
    }
}
fn file_identity(path: &Path, bound: u64) -> Result<(u64, String), Error> {
    let mut reader = HashReader::new(files::read_file(path, bound)?, bound);
    io::copy(&mut reader, &mut io::sink()).map_err(io_error)?;
    Ok(reader.identity())
}
fn header(path: &str, size: u64, executable: bool, epoch: u64) -> Result<tar::Header, Error> {
    let mut header = tar::Header::new_ustar();
    header.set_path(path).map_err(|_| {
        error(
            "archive-path",
            "Native archive path does not fit canonical USTAR",
        )
    })?;
    header.set_size(size);
    header.set_mode(if executable { 0o755 } else { 0o644 });
    header.set_uid(0);
    header.set_gid(0);
    header.set_mtime(epoch);
    header.set_entry_type(tar::EntryType::Regular);
    header.set_cksum();
    Ok(header)
}
/// Create a canonical gzip/USTAR archive. Existing outputs are never overwritten.
pub fn create_bundle(
    version: &str,
    inputs: BuildInputs,
    files_input: &[BundleInput],
    output: &Path,
) -> Result<BundleIdentity, Error> {
    let mut records = BTreeMap::new();
    let mut sources = BTreeMap::new();
    for input in files_input {
        if !model::safe_path(&input.path) || records.contains_key(&input.path) {
            return Err(error(
                "archive-path",
                "Native bundle input paths are unsafe or duplicated",
            ));
        }
        let (size, sha256) = file_identity(&input.source, model::MAX_FILE_BYTES)?;
        records.insert(
            input.path.clone(),
            FileRecord {
                size,
                sha256,
                executable: input.executable,
            },
        );
        sources.insert(input.path.clone(), input.source.clone());
    }
    let manifest = Manifest {
        schema_id: "ici.native-distribution".into(),
        schema_version: 1,
        version: version.into(),
        target: inputs.target.clone(),
        build_inputs_sha256: model::digest(
            &serde_json::to_vec(&inputs)
                .map_err(|_| error("manifest-inputs", "Build inputs cannot be serialized"))?,
        ),
        build_inputs: inputs,
        files: records,
    };
    manifest.validate()?;
    let serialized = serde_json::to_vec_pretty(&manifest)
        .map_err(|_| error("manifest-schema", "Manifest cannot be serialized"))?;
    if serialized.len() as u64 > model::MAX_MANIFEST_BYTES {
        return Err(error("manifest-bound", "Manifest exceeds its byte bound"));
    }
    files::directory(output.parent().unwrap_or(Path::new(".")), true)?;
    let temporary = tempfile::NamedTempFile::new_in(output.parent().unwrap_or(Path::new(".")))
        .map_err(io_error)?;
    {
        let gzip = GzBuilder::new()
            .mtime(0)
            .operating_system(255)
            .write(temporary.as_file(), Compression::new(6));
        let mut archive = tar::Builder::new(gzip);
        archive
            .append(
                &header(
                    "manifest.json",
                    serialized.len() as u64,
                    false,
                    manifest.build_inputs.source_date_epoch,
                )?,
                serialized.as_slice(),
            )
            .map_err(io_error)?;
        for (path, record) in &manifest.files {
            let mut reader = HashReader::new(
                files::read_file(&sources[path], model::MAX_FILE_BYTES)?,
                model::MAX_FILE_BYTES,
            );
            archive
                .append(
                    &header(
                        path,
                        record.size,
                        record.executable,
                        manifest.build_inputs.source_date_epoch,
                    )?,
                    &mut reader,
                )
                .map_err(io_error)?;
            // append reads the declared size; explicitly reject a growing input.
            let mut extra = [0];
            if reader.read(&mut extra).map_err(io_error)? != 0
                || reader.identity() != (record.size, record.sha256.clone())
            {
                return Err(error(
                    "input-drift",
                    "Bundle input changed while creating its archive",
                ));
            }
        }
        archive
            .into_inner()
            .map_err(io_error)?
            .finish()
            .map_err(io_error)?;
    }
    temporary.as_file().sync_all().map_err(io_error)?;
    let (size, sha256) = file_identity(temporary.path(), model::MAX_ARCHIVE_BYTES)?;
    // Inspect our exact emitted bytes before exposing them as a candidate.
    inspect_bundle(temporary.path(), &sha256)?;
    temporary.persist_noclobber(output).map_err(|_| {
        error(
            "output-exists",
            "Archive output already exists or could not be committed",
        )
    })?;
    Ok(BundleIdentity {
        path: output.to_owned(),
        sha256,
        size,
        manifest,
    })
}

/// Validate every byte and file without unpacking or executing the product.
pub fn inspect_bundle(path: &Path, expected_sha256: &str) -> Result<Manifest, Error> {
    read_bundle(path, expected_sha256, None)
}
pub(crate) fn extract_bundle(
    path: &Path,
    expected_sha256: &str,
    destination: &Path,
) -> Result<Manifest, Error> {
    read_bundle(path, expected_sha256, Some(destination))
}
fn read_bundle(
    path: &Path,
    expected_sha256: &str,
    destination: Option<&Path>,
) -> Result<Manifest, Error> {
    if !model::valid_digest(expected_sha256) {
        return Err(error(
            "archive-digest",
            "Expected archive SHA-256 must be a lowercase 64-digit digest",
        ));
    }
    let compressed = HashReader::new(
        files::read_file(path, model::MAX_ARCHIVE_BYTES)?,
        model::MAX_ARCHIVE_BYTES,
    );
    let decoded = GzDecoder::new(BufReader::new(compressed));
    let expanded = HashReader::new(decoded, model::MAX_TOTAL_BYTES + 16 * 1024 * 1024);
    let mut archive = tar::Archive::new(expanded);
    let mut manifest: Option<Manifest> = None;
    let mut visited = BTreeSet::new();
    let mut tool_manifest = vec![];
    for entry in archive
        .entries()
        .map_err(|_| {
            error(
                "archive-format",
                "Native archive is not readable USTAR/gzip",
            )
        })?
        .raw(true)
    {
        let mut entry =
            entry.map_err(|_| error("archive-format", "Native archive has an invalid header"))?;
        let path = entry
            .path()
            .map_err(|_| error("archive-path", "Native archive path cannot be decoded"))?
            .to_str()
            .ok_or_else(|| error("archive-path", "Native archive paths must be UTF-8"))?
            .to_owned();
        if !model::safe_path(&path)
            || !visited.insert(path.clone())
            || visited.len() > model::MAX_FILES + 1
            || entry.header().entry_type() != tar::EntryType::Regular
            || entry
                .header()
                .link_name()
                .map_err(|_| error("archive-path", "Invalid link field"))?
                .is_some()
        {
            return Err(error(
                "archive-path",
                "Native archive contains an unsafe, duplicate, link, sparse or special entry",
            ));
        }
        let size = entry.size();
        if manifest.is_none() {
            if path != "manifest.json" || size > model::MAX_MANIFEST_BYTES {
                return Err(error(
                    "archive-manifest",
                    "A bounded manifest must be the first archive entry",
                ));
            }
            let mut bytes = vec![];
            entry
                .read_to_end(&mut bytes)
                .map_err(|_| error("archive-format", "Native manifest payload is truncated"))?;
            let parsed: Manifest = crate::json::parse(&bytes)
                .map_err(|_| error("manifest-schema", "Native manifest cannot be parsed"))?;
            parsed.validate()?;
            if let Some(root) = destination {
                files::write_new(&root.join("manifest.json"), &bytes, false)?;
            }
            manifest = Some(parsed);
            continue;
        }
        let record = manifest
            .as_ref()
            .and_then(|manifest| manifest.files.get(&path))
            .ok_or_else(|| {
                error(
                    "archive-unlisted",
                    "Native archive contains a file absent from its manifest",
                )
            })?;
        if size != record.size
            || entry
                .header()
                .mode()
                .map_err(|_| error("archive-mode", "Invalid archive mode"))?
                != if record.executable { 0o755 } else { 0o644 }
        {
            return Err(error(
                "archive-size",
                "Archive mode or declared size differs from its manifest",
            ));
        }
        let mut file = destination
            .map(|root| files::create_file(&root.join(&path), record.executable))
            .transpose()?;
        let mut reader = HashReader::new(&mut entry, record.size);
        if path == "tools/manifest.json" {
            if record.size > model::MAX_MANIFEST_BYTES {
                return Err(error(
                    "tool-manifest",
                    "Native tool manifest exceeds its byte bound",
                ));
            }
            reader.read_to_end(&mut tool_manifest).map_err(io_error)?;
            if let Some(file) = file.as_mut() {
                file.write_all(&tool_manifest).map_err(io_error)?;
                file.sync_all().map_err(io_error)?;
            }
        } else if let Some(file) = file.as_mut() {
            io::copy(&mut reader, file).map_err(io_error)?;
            file.sync_all().map_err(io_error)?;
        } else {
            io::copy(&mut reader, &mut io::sink()).map_err(io_error)?;
        }
        if reader.identity() != (record.size, record.sha256.clone()) {
            return Err(error(
                "file-digest",
                "Native archive member digest does not match its manifest",
            ));
        }
    }
    let manifest =
        manifest.ok_or_else(|| error("archive-manifest", "Native archive contains no manifest"))?;
    if visited.len() != manifest.files.len() + 1 {
        return Err(error(
            "archive-missing",
            "Native archive omits files declared in its manifest",
        ));
    }
    crate::tools::validate(&tool_manifest, &manifest)?;
    // Only bounded zero padding can follow USTAR's end marker. Drain gzip to
    // validate its CRC/trailer, then reject concatenated streams or suffixes.
    let mut remaining = archive.into_inner();
    let mut padding = 0;
    let mut buffer = [0; 65_536];
    loop {
        let count = remaining.read(&mut buffer).map_err(|_| {
            error(
                "archive-format",
                "Native gzip payload or trailer is invalid",
            )
        })?;
        if count == 0 {
            break;
        }
        padding += count;
        if padding > 1024 * 1024 || buffer[..count].iter().any(|byte| *byte != 0) {
            return Err(error(
                "archive-trailing",
                "Native archive contains trailing expanded content",
            ));
        }
    }
    let mut compressed = remaining.inner.into_inner();
    if compressed.read(&mut buffer[..1]).map_err(io_error)? != 0 {
        return Err(error(
            "archive-trailing",
            "Native archive contains trailing compressed content",
        ));
    }
    if compressed.into_inner().identity().1 != expected_sha256 {
        return Err(error(
            "archive-digest",
            "Native archive SHA-256 does not match the trusted expected digest",
        ));
    }
    Ok(manifest)
}

pub(crate) fn verify_installed(root: &Path) -> Result<Manifest, Error> {
    let manifest: Manifest = crate::json::parse(&files::read_bytes(
        &root.join("manifest.json"),
        model::MAX_MANIFEST_BYTES,
    )?)
    .map_err(|_| error("manifest-schema", "Installed manifest cannot be parsed"))?;
    manifest.validate()?;
    for (path, record) in &manifest.files {
        if file_identity(&root.join(path), model::MAX_FILE_BYTES)?
            != (record.size, record.sha256.clone())
        {
            return Err(error(
                "file-digest",
                "Installed product files no longer match their manifest",
            ));
        }
        use std::os::unix::fs::PermissionsExt;
        if std::fs::symlink_metadata(root.join(path))
            .map_err(io_error)?
            .permissions()
            .mode()
            & 0o777
            != if record.executable { 0o755 } else { 0o644 }
        {
            return Err(error(
                "archive-mode",
                "Installed file mode differs from its manifest",
            ));
        }
    }
    crate::tools::validate(
        &files::read_bytes(&root.join("tools/manifest.json"), model::MAX_MANIFEST_BYTES)?,
        &manifest,
    )?;
    let mut expected: BTreeSet<_> = manifest.files.keys().cloned().collect();
    expected.extend(["manifest.json".into(), ".archive-sha256".into()]);
    let mut directories = BTreeSet::new();
    for path in &expected {
        let mut parent = Path::new(path).parent();
        while let Some(path) = parent {
            if path.as_os_str().is_empty() {
                break;
            }
            directories.insert(path.to_string_lossy().into_owned());
            parent = path.parent();
        }
    }
    let mut pending = vec![root.to_path_buf()];
    let mut count = 0;
    while let Some(directory) = pending.pop() {
        for entry in std::fs::read_dir(directory).map_err(io_error)? {
            count += 1;
            if count > model::MAX_FILES * 16 + 2 {
                return Err(error(
                    "installed-bound",
                    "Installed inventory exceeds its bound",
                ));
            }
            let entry = entry.map_err(io_error)?;
            let kind = entry.file_type().map_err(io_error)?;
            let path = entry.path();
            let relative = path
                .strip_prefix(root)
                .map_err(|_| {
                    error(
                        "installed-path",
                        "Installed entry escapes its version directory",
                    )
                })?
                .to_str()
                .ok_or_else(|| error("installed-path", "Installed entry is not UTF-8"))?;
            if kind.is_dir() && directories.contains(relative) {
                pending.push(path);
            } else if !kind.is_file() || !expected.contains(relative) {
                return Err(error(
                    "installed-extra",
                    "Installed directory contains an unlisted file, link or special entry",
                ));
            }
        }
    }
    Ok(manifest)
}
