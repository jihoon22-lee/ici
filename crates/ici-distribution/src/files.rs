use crate::model::{Error, error, io_error};
use rustix::fs::{Mode, OFlags};
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Component, Path},
};

/// Refuse symlink ancestors rather than following a user-selected install/cache path.
pub(crate) fn directory(path: &Path, create: bool) -> Result<(), Error> {
    let absolute = if path.is_absolute() {
        path.to_owned()
    } else {
        std::env::current_dir().map_err(io_error)?.join(path)
    };
    let mut current = std::path::PathBuf::new();
    for component in absolute.components() {
        match component {
            Component::RootDir | Component::Normal(_) => current.push(component.as_os_str()),
            _ => {
                return Err(error(
                    "unsafe-path",
                    "Native installation paths cannot contain parent components",
                ));
            }
        }
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {}
            Err(error) if create && error.kind() == std::io::ErrorKind::NotFound => {
                use std::os::unix::fs::DirBuilderExt;
                fs::DirBuilder::new()
                    .mode(0o700)
                    .create(&current)
                    .map_err(io_error)?;
            }
            _ => {
                return Err(error(
                    "unsafe-path",
                    "Native installation directory is missing or is not a real directory",
                ));
            }
        }
    }
    Ok(())
}
pub(crate) fn read_file(path: &Path, bound: u64) -> Result<File, Error> {
    directory(path.parent().unwrap_or(Path::new(".")), false)?;
    let file = File::from(
        rustix::fs::open(
            path,
            OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::CLOEXEC | OFlags::NONBLOCK,
            Mode::empty(),
        )
        .map_err(|_| {
            error(
                "unsafe-file",
                "Native distribution input is not a readable regular file",
            )
        })?,
    );
    let metadata = file.metadata().map_err(io_error)?;
    if !metadata.is_file() || metadata.len() > bound {
        return Err(error(
            "input-bound",
            "Native distribution input is not regular or exceeds its byte bound",
        ));
    }
    Ok(file)
}
pub(crate) fn read_bytes(path: &Path, bound: u64) -> Result<Vec<u8>, Error> {
    let mut bytes = vec![];
    read_file(path, bound)?
        .take(bound + 1)
        .read_to_end(&mut bytes)
        .map_err(io_error)?;
    if bytes.len() as u64 > bound {
        return Err(error(
            "input-bound",
            "Native distribution input changed beyond its byte bound",
        ));
    }
    Ok(bytes)
}
pub(crate) fn create_file(path: &Path, executable: bool) -> Result<File, Error> {
    use std::os::unix::fs::PermissionsExt;
    directory(path.parent().unwrap_or(Path::new(".")), true)?;
    let file = File::from(
        rustix::fs::open(
            path,
            OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::RUSR | Mode::WUSR,
        )
        .map_err(|_| {
            error(
                "output-exists",
                "Native distribution output already exists or could not be created",
            )
        })?,
    );
    file.set_permissions(fs::Permissions::from_mode(if executable {
        0o755
    } else {
        0o644
    }))
    .map_err(io_error)?;
    Ok(file)
}
pub(crate) fn write_new(path: &Path, bytes: &[u8], executable: bool) -> Result<(), Error> {
    let mut file = create_file(path, executable)?;
    file.write_all(bytes).map_err(io_error)?;
    file.sync_all().map_err(io_error)
}
