use crate::CppError;
use nix::unistd::Uid;
use serde_json::json;
use std::{
    ffi::OsString,
    fs::OpenOptions,
    io::Write,
    os::unix::{
        fs::{MetadataExt, OpenOptionsExt},
        process::CommandExt,
    },
    path::Path,
    process::Command,
    time::{Duration, Instant},
};

pub fn record_compiler_invocation(
    journal: &Path,
    arguments: &[String],
    cwd: &Path,
) -> Result<(), CppError> {
    crate::arguments::validate(arguments)?;
    let payload = serde_json::to_vec(&json!({"arguments":arguments,"directory":cwd}))
        .map_err(|e| CppError::new("capture-encoding", e.to_string()))?;
    if payload.len() + 1 > 1024 * 1024 {
        return Err(CppError::new(
            "capture-record-too-large",
            "compiler invocation exceeds 1 MiB",
        ));
    }
    let mut file = OpenOptions::new()
        .append(true)
        .custom_flags(nix::libc::O_NOFOLLOW | nix::libc::O_CLOEXEC | nix::libc::O_NONBLOCK)
        .open(journal)
        .map_err(|e| CppError::new("capture-open", e.to_string()))?;
    let started = Instant::now();
    loop {
        match file.try_lock() {
            Ok(()) => break,
            Err(std::fs::TryLockError::WouldBlock)
                if started.elapsed() < Duration::from_secs(2) =>
            {
                std::thread::sleep(Duration::from_millis(5))
            }
            Err(error) => return Err(CppError::new("capture-lock", error.to_string())),
        }
    }
    let metadata = file
        .metadata()
        .map_err(|e| CppError::new("capture-metadata", e.to_string()))?;
    if !metadata.is_file()
        || metadata.nlink() != 1
        || metadata.uid() != Uid::effective().as_raw()
        || metadata.mode() & 0o077 != 0
    {
        return Err(CppError::new(
            "unsafe-capture",
            "journal must be an owned private regular file",
        ));
    }
    if metadata.len() + payload.len() as u64 + 1 > 32 * 1024 * 1024 {
        return Err(CppError::new("capture-too-large", "journal exceeds 32 MiB"));
    }
    file.write_all(&payload)
        .and_then(|_| file.write_all(b"\n"))
        .and_then(|_| file.sync_data())
        .map_err(|e| CppError::new("capture-write", e.to_string()))?;
    file.unlock()
        .map_err(|e| CppError::new("capture-unlock", e.to_string()))?;
    Ok(())
}

/// Hidden same-binary entrypoint: capture only compile invocations, then replace this process.
/// Pass argv after `__qmake-record`; a leading `--` is accepted.
pub fn recorder_main(args: &[OsString]) -> i32 {
    let args = if args.first().is_some_and(|v| v == "--") {
        &args[1..]
    } else {
        args
    };
    let result = (|| -> Result<(), CppError> {
        let arguments = args
            .iter()
            .map(|v| {
                v.clone()
                    .into_string()
                    .map_err(|_| CppError::new("capture-encoding", "compiler argv must be UTF-8"))
            })
            .collect::<Result<Vec<_>, _>>()?;
        crate::arguments::validate(&arguments)?;
        if arguments.iter().any(|v| v == "-c") {
            let path = std::env::var_os("ICI_QMAKE_CAPTURE_PATH").ok_or_else(|| {
                CppError::new(
                    "capture-not-configured",
                    "capture journal was not configured",
                )
            })?;
            let cwd =
                std::env::current_dir().map_err(|e| CppError::new("capture-cwd", e.to_string()))?;
            record_compiler_invocation(Path::new(&path), &arguments, &cwd)?;
        }
        let error = Command::new(&args[0]).args(&args[1..]).exec();
        Err(CppError::new("compiler-exec", error.to_string()))
    })();
    if let Err(error) = result {
        eprintln!(
            "ici qmake capture: {}",
            error.to_string().chars().take(300).collect::<String>()
        );
    }
    125
}
