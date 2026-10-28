use crate::{
    archive, files,
    model::{self, Error, Installation, Manifest, error, io_error},
};
use rustix::fs::{Mode, OFlags};
use std::{
    fs::{self, File},
    io::Read,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

const MARKER: &[u8] = b"ici.native-install/v1\n";
static TRANSACTION: AtomicU64 = AtomicU64::new(0);
struct Root {
    path: PathBuf,
    descriptor: File,
    _lock: File,
}
fn selected_key(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 150
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
        && !matches!(value, "." | "..")
}
fn open_directory(path: &Path) -> Result<File, Error> {
    files::directory(path, false)?;
    rustix::fs::open(
        path,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map(File::from)
    .map_err(|_| {
        error(
            "unsafe-path",
            "Installation directory could not be opened without following links",
        )
    })
}
impl Root {
    fn open(path: &Path, create: bool) -> Result<Self, Error> {
        files::directory(path, create)?;
        let path = path.canonicalize().map_err(io_error)?;
        let descriptor = open_directory(&path)?;
        if descriptor
            .metadata()
            .map_err(io_error)?
            .permissions()
            .mode()
            & 0o022
            != 0
        {
            return Err(error(
                "install-permissions",
                "Installation root must not be writable by other users",
            ));
        }
        let marker = path.join(".ici-install-root");
        match files::read_bytes(&marker, 128) {
            Ok(bytes) if bytes == MARKER => {}
            Err(_)
                if create
                    && !marker.exists()
                    && !fs::read_dir(&path)
                        .map_err(io_error)?
                        .any(|entry| entry.is_ok()) =>
            {
                files::write_new(&marker, MARKER, false)?
            }
            _ => {
                return Err(error(
                    "install-owned-root",
                    "Install into a dedicated empty directory or an existing ici native installation",
                ));
            }
        }
        let lock = File::from(
            rustix::fs::openat(
                &descriptor,
                "install.lock",
                OFlags::RDWR
                    | OFlags::CREATE
                    | OFlags::NOFOLLOW
                    | OFlags::CLOEXEC
                    | OFlags::NONBLOCK,
                Mode::RUSR | Mode::WUSR,
            )
            .map_err(|_| {
                error(
                    "install-lock",
                    "Installation lock is not a regular owned file",
                )
            })?,
        );
        if !lock.metadata().map_err(io_error)?.is_file() {
            return Err(error(
                "install-lock",
                "Installation lock is not a regular owned file",
            ));
        }
        rustix::fs::flock(&lock, rustix::fs::FlockOperation::NonBlockingLockExclusive).map_err(
            |_| {
                error(
                    "install-busy",
                    "Another native installation transaction holds this root",
                )
            },
        )?;
        files::directory(&path.join("versions"), create)?;
        Ok(Self {
            path,
            descriptor,
            _lock: lock,
        })
    }
    fn selector(&self, name: &str) -> Result<Option<String>, Error> {
        match rustix::fs::readlinkat(&self.descriptor, name, Vec::new()) {
            Ok(value) => {
                let value = value
                    .to_str()
                    .map_err(|_| error("install-selector", "Installation selector is not UTF-8"))?;
                let key = value
                    .strip_prefix("versions/")
                    .filter(|key| selected_key(key))
                    .ok_or_else(|| {
                        error(
                            "install-selector",
                            "Installation selector escapes its version directory",
                        )
                    })?;
                Ok(Some(key.into()))
            }
            Err(rustix::io::Errno::NOENT) => Ok(None),
            Err(_) => Err(error(
                "install-selector",
                "Installation selector is not an owned relative symbolic link",
            )),
        }
    }
    fn replace_selector(&self, name: &str, key: &str) -> Result<(), Error> {
        if !selected_key(key) {
            return Err(error("install-selector", "Invalid native version selector"));
        }
        self.selector(name)?;
        let temporary = format!(
            ".select-{}-{}",
            std::process::id(),
            TRANSACTION.fetch_add(1, Ordering::Relaxed)
        );
        rustix::fs::symlinkat(format!("versions/{key}"), &self.descriptor, &temporary).map_err(
            |_| {
                error(
                    "install-atomic",
                    "Could not create an atomic installation selector",
                )
            },
        )?;
        let renamed = rustix::fs::renameat(&self.descriptor, &temporary, &self.descriptor, name);
        if renamed.is_err() {
            let _ =
                rustix::fs::unlinkat(&self.descriptor, &temporary, rustix::fs::AtFlags::empty());
        }
        renamed.map_err(|_| {
            error(
                "install-atomic",
                "Could not commit an atomic installation selector",
            )
        })?;
        self.descriptor.sync_all().map_err(io_error)
    }
    fn entrypoint(&self, create: bool) -> Result<(), Error> {
        let bin = self.path.join("bin");
        files::directory(&bin, create)?;
        let descriptor = open_directory(&bin)?;
        match rustix::fs::readlinkat(&descriptor, "ici", Vec::new()) {
            Ok(target) if target.as_bytes() == b"../current/bin/ici" => Ok(()),
            Err(rustix::io::Errno::NOENT) if create => {
                rustix::fs::symlinkat("../current/bin/ici", &descriptor, "ici").map_err(|_| {
                    error(
                        "install-path-collision",
                        "Could not create the native entrypoint without replacing another file",
                    )
                })
            }
            _ => Err(error(
                "install-path-collision",
                "The requested bin/ici belongs to another installation; choose a dedicated native prefix",
            )),
        }
    }
    fn verified(&self, key: &str) -> Result<Manifest, Error> {
        if !selected_key(key) {
            return Err(error("install-selector", "Invalid installed version key"));
        }
        let directory = self.path.join("versions").join(key);
        files::directory(&directory, false)?;
        let receipt =
            String::from_utf8(files::read_bytes(&directory.join(".archive-sha256"), 128)?)
                .map_err(|_| error("install-receipt", "Invalid archive receipt"))?;
        let manifest = archive::verify_installed(&directory)?;
        if !model::valid_digest(&receipt) || key != format!("{}-{receipt}", manifest.version) {
            return Err(error(
                "install-receipt",
                "Installed version key and archive receipt disagree",
            ));
        }
        Ok(manifest)
    }
    fn activate(&self, key: &str) -> Result<Installation, Error> {
        let manifest = self.verified(key)?;
        health(
            &self.path.join("versions").join(key).join("bin/ici"),
            &manifest,
        )?;
        self.entrypoint(true)?;
        let old = self.selector("current")?;
        if old.as_deref() != Some(key) {
            if let Some(old) = old {
                self.replace_selector("previous", &old)?;
            }
            self.replace_selector("current", key)?;
        }
        self.report(key, manifest)
    }
    fn report(&self, key: &str, manifest: Manifest) -> Result<Installation, Error> {
        let executable = self.path.join("bin/ici");
        let mut guidance=vec![format!("Add {} to PATH and verify ici --version.",self.path.join("bin").display()),"Update any task that stores an absolute path such as .venv/bin/ici to the selected native executable.".into()];
        if std::env::var_os("ICI_PYTHON").is_some() {
            guidance.push("ICI_PYTHON applies only to legacy Python artifacts.".into());
        }
        let mut collisions = vec![];
        if let Some(paths) = std::env::var_os("PATH") {
            for directory in std::env::split_paths(&paths) {
                if directory.as_os_str().is_empty() {
                    continue;
                }
                let candidate = directory.join("ici");
                if candidate != executable
                    && candidate.is_file()
                    && !collisions.contains(&candidate)
                {
                    collisions.push(candidate);
                }
            }
        }
        Ok(Installation {
            root: self.path.clone(),
            selected: key.into(),
            previous: self.selector("previous")?,
            executable,
            manifest,
            path_collisions: collisions,
            guidance,
        })
    }
}

fn health(executable: &Path, manifest: &Manifest) -> Result<(), Error> {
    if std::env::consts::OS != "linux" || std::env::consts::ARCH != "x86_64" {
        return Err(error(
            "unsupported-host",
            "This native archive is tested only for Linux/WSL x86_64",
        ));
    }
    validate_elf(executable, manifest.target.ends_with("-musl"))?;
    probe(
        executable,
        &manifest.files["bin/ici"].sha256,
        &format!("ici {}", manifest.version),
    )?;
    let root = executable
        .parent()
        .and_then(Path::parent)
        .ok_or_else(|| error("install-path", "Native core has no version root"))?;
    let tools: crate::ToolManifest = crate::json::parse(&files::read_bytes(
        &root.join("tools/manifest.json"),
        model::MAX_MANIFEST_BYTES,
    )?)?;
    for (id, tool) in tools.tools {
        let executable = root.join("tools").join(&tool.path);
        validate_elf(&executable, true)?;
        probe(&executable, &tool.sha256, &format!("{id} {}", tool.version))?;
    }
    Ok(())
}
fn probe(executable: &Path, digest: &str, expected: &str) -> Result<(), Error> {
    let mut task = ici_domain::TaskSpec::command(
        "native-install-version",
        "ici.distribution",
        vec![
            executable.to_string_lossy().into_owned(),
            "--version".into(),
        ],
        executable
            .parent()
            .unwrap_or(Path::new("."))
            .to_string_lossy(),
    );
    task.kind = ici_domain::TaskKind::Prepare;
    task.timeout_seconds = Some(5.0);
    task.output_limit_bytes = Some(4096);
    task.tool_digest = Some(format!("sha256:{digest}"));
    let environment = std::collections::BTreeMap::from([
        ("PATH".into(), String::new()),
        ("LC_ALL".into(), "C".into()),
    ]);
    let outcome = ici_execution::Runner::with_environment(environment)
        .run(&task, &ici_execution::Cancellation::default());
    if !outcome.prerequisite_succeeded()
        || !outcome.cleanup.is_clean()
        || String::from_utf8_lossy(&outcome.stdout).trim() != expected
    {
        return Err(error(
            "install-execution",
            "Native version probe failed, exceeded its bounds or does not match the manifest",
        ));
    }
    Ok(())
}
fn validate_elf(path: &Path, static_required: bool) -> Result<(), Error> {
    use std::io::{Seek, SeekFrom};
    let mut file = files::read_file(path, model::MAX_FILE_BYTES)?;
    let size = file.metadata().map_err(io_error)?.len();
    let mut header = [0; 64];
    file.read_exact(&mut header).map_err(|_| {
        error(
            "install-elf",
            "Native executable has a truncated ELF header",
        )
    })?;
    if &header[..6] != b"\x7fELF\x02\x01"
        || u16::from_le_bytes(header[18..20].try_into().unwrap()) != 62
    {
        return Err(error(
            "install-elf",
            "Native executable is not a little-endian x86_64 ELF binary",
        ));
    }
    let offset = u64::from_le_bytes(header[32..40].try_into().unwrap());
    let width = u16::from_le_bytes(header[54..56].try_into().unwrap()) as u64;
    let count = u16::from_le_bytes(header[56..58].try_into().unwrap()) as u64;
    if width != 56
        || count > 1024
        || offset
            .checked_add(width * count)
            .is_none_or(|end| end > size)
    {
        return Err(error(
            "install-elf",
            "Native executable has an invalid program header table",
        ));
    }
    if static_required {
        for index in 0..count {
            file.seek(SeekFrom::Start(offset + index * width))
                .map_err(io_error)?;
            let mut program = [0; 56];
            file.read_exact(&mut program).map_err(io_error)?;
            let kind = u32::from_le_bytes(program[..4].try_into().unwrap());
            if kind == 3 {
                return Err(error(
                    "install-elf",
                    "A static target unexpectedly contains a runtime interpreter",
                ));
            }
            if kind == 2 {
                let dynamic = u64::from_le_bytes(program[8..16].try_into().unwrap());
                let length = u64::from_le_bytes(program[32..40].try_into().unwrap());
                if length > 1024 * 1024 || dynamic.checked_add(length).is_none_or(|end| end > size)
                {
                    return Err(error(
                        "install-elf",
                        "Native executable has an invalid dynamic table",
                    ));
                }
                file.seek(SeekFrom::Start(dynamic)).map_err(io_error)?;
                for _ in 0..length / 16 {
                    let mut entry = [0; 16];
                    file.read_exact(&mut entry).map_err(io_error)?;
                    if u64::from_le_bytes(entry[..8].try_into().unwrap()) == 1 {
                        return Err(error(
                            "install-elf",
                            "A static target unexpectedly depends on a shared library",
                        ));
                    }
                }
            }
        }
    }
    Ok(())
}

/// Fully verify and stage a version before atomically changing its selector.
pub fn install(
    archive_path: &Path,
    expected_sha256: &str,
    install_root: &Path,
) -> Result<Installation, Error> {
    let root = Root::open(install_root, true)?;
    root.selector("current")?;
    if root.path.join("bin").exists() {
        root.entrypoint(false)?;
    }
    let stage = tempfile::Builder::new()
        .prefix(".install-")
        .tempdir_in(root.path.join("versions"))
        .map_err(io_error)?;
    let manifest = archive::extract_bundle(archive_path, expected_sha256, stage.path())?;
    let key = format!("{}-{expected_sha256}", manifest.version);
    files::write_new(
        &stage.path().join(".archive-sha256"),
        expected_sha256.as_bytes(),
        false,
    )?;
    archive::verify_installed(stage.path())?;
    health(&stage.path().join("bin/ici"), &manifest)?;
    let versions = open_directory(&root.path.join("versions"))?;
    if root.path.join("versions").join(&key).exists() {
        if root.verified(&key)? != manifest {
            return Err(error(
                "install-version-collision",
                "A different installed version already uses this archive identity",
            ));
        }
    } else {
        let name = stage
            .path()
            .file_name()
            .ok_or_else(|| error("install-stage", "Invalid staging directory name"))?;
        rustix::fs::renameat_with(
            &versions,
            name,
            &versions,
            &key,
            rustix::fs::RenameFlags::NOREPLACE,
        )
        .map_err(|_| {
            error(
                "install-atomic",
                "Could not commit the verified version directory",
            )
        })?;
        versions.sync_all().map_err(io_error)?;
    }
    root.activate(&key)
}
/// Select an installed verified version/key; ambiguous same-version builds are refused.
pub fn activate(install_root: &Path, version_or_key: &str) -> Result<Installation, Error> {
    let root = Root::open(install_root, false)?;
    if !selected_key(version_or_key) {
        return Err(error("install-selector", "Invalid requested version"));
    }
    let direct = root.path.join("versions").join(version_or_key);
    let key = if direct.exists() {
        version_or_key.to_owned()
    } else {
        let mut matching = vec![];
        for entry in fs::read_dir(root.path.join("versions")).map_err(io_error)? {
            let entry = entry.map_err(io_error)?;
            let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
                continue;
            };
            if name.starts_with(&format!("{version_or_key}-"))
                && root
                    .verified(&name)
                    .is_ok_and(|manifest| manifest.version == version_or_key)
            {
                matching.push(name);
            }
        }
        if matching.len() != 1 {
            return Err(error(
                "install-version",
                "Requested installed version is missing or ambiguous; use its complete selection key",
            ));
        }
        matching.remove(0)
    };
    root.activate(&key)
}
/// Swap to the previous verified artifact; config/results/baselines are never touched.
pub fn rollback(install_root: &Path) -> Result<Installation, Error> {
    let root = Root::open(install_root, false)?;
    let previous = root
        .selector("previous")?
        .ok_or_else(|| error("install-previous", "No previous native version is recorded"))?;
    root.activate(&previous)
}
pub fn inspect_installation(install_root: &Path) -> Result<Installation, Error> {
    let root = Root::open(install_root, false)?;
    let current = root
        .selector("current")?
        .ok_or_else(|| error("install-current", "No native version is selected"))?;
    root.report(&current, root.verified(&current)?)
}
