use crate::{
    CppError, QmakeCaptureRequest,
    input::{read_contained, scoped, strict_json},
};
use regex::Regex;
use std::{
    collections::BTreeSet,
    ffi::OsString,
    fs,
    os::unix::{fs::PermissionsExt, process::CommandExt},
    path::{Path, PathBuf},
    process::Command,
    sync::LazyLock,
};

static DRIVER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^(?:[A-Za-z0-9_+.]+-)*(?:gcc|g\+\+|clang|clang\+\+|cc|c\+\+)(?:-[0-9][A-Za-z0-9_.-]*)?$",
    )
    .expect("constant driver expression")
});
const MAX_MAKEFILES: usize = 4096;
const MAX_MAKEFILE_BYTES: usize = 4 * 1024 * 1024;
const MAX_METADATA_BYTES: usize = 32 * 1024 * 1024;

fn quote(value: &str) -> String {
    // First quote the Make recipe's shell token, then preserve those quotes through qmake parsing.
    format!("'{}'", value.replace('\'', "'\\''")).replace('\'', "\\'")
}
fn compiler(value: &str) -> Result<String, CppError> {
    let words = crate::split_posix(value)?;
    if words.len() != 1
        || !DRIVER.is_match(
            Path::new(&words[0])
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or(""),
        )
    {
        return Err(CppError::new(
            "unsupported-qmake-compiler",
            "qmake compiler must be one recognized GCC/Clang driver token",
        ));
    }
    let raw = Path::new(&words[0]);
    let path = if raw.is_absolute() {
        raw.to_path_buf()
    } else {
        std::env::var_os("PATH")
            .into_iter()
            .flat_map(|path| std::env::split_paths(&path).collect::<Vec<_>>())
            .filter(|directory| !directory.as_os_str().is_empty())
            .map(|directory| directory.join(raw))
            .find(|candidate| {
                candidate.is_file()
                    && candidate
                        .metadata()
                        .is_ok_and(|metadata| metadata.permissions().mode() & 0o111 != 0)
            })
            .ok_or_else(|| {
                CppError::new(
                    "qmake-compiler-missing",
                    format!("qmake compiler {} is unavailable", raw.display()),
                )
            })?
    };
    let metadata = path
        .metadata()
        .map_err(|error| CppError::new("qmake-compiler-missing", error.to_string()))?;
    if !metadata.is_file() || metadata.permissions().mode() & 0o111 == 0 {
        return Err(CppError::new(
            "qmake-compiler-not-executable",
            "qmake compiler is not an executable regular file",
        ));
    }
    // Preserve the invocation alias: multicall compilers use argv[0] to choose language/driver mode.
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| CppError::new("qmake-compiler-encoding", "compiler path must be UTF-8"))
}
fn makefiles(root: &Path, shadow: &Path) -> Result<Vec<PathBuf>, CppError> {
    let mut pending = vec![shadow.to_path_buf()];
    let mut files = vec![];
    let mut visited = 0;
    while let Some(directory) = pending.pop() {
        let mut entries = fs::read_dir(&directory)
            .map_err(|error| CppError::new("qmake-metadata-read", error.to_string()))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| CppError::new("qmake-metadata-read", error.to_string()))?;
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries {
            visited += 1;
            if visited > MAX_MAKEFILES * 8 {
                return Err(CppError::new(
                    "qmake-metadata-too-large",
                    "qmake metadata tree exceeds its entry bound",
                ));
            }
            let kind = entry
                .file_type()
                .map_err(|error| CppError::new("qmake-metadata-read", error.to_string()))?;
            if kind.is_symlink() {
                if entry.file_name().to_string_lossy().starts_with("Makefile") {
                    return Err(CppError::new(
                        "qmake-metadata-symlink",
                        "qmake Makefile metadata may not be a symlink",
                    ));
                }
                continue;
            }
            if kind.is_dir() {
                pending.push(entry.path());
            } else if kind.is_file() && entry.file_name().to_string_lossy().starts_with("Makefile")
            {
                let path = entry.path();
                let (_, scope, physical) = scoped(root, root, &path.display().to_string())?;
                if scope != "project" || !physical.starts_with(shadow) {
                    return Err(CppError::new(
                        "qmake-metadata-outside-shadow",
                        "qmake metadata leaves owned shadow",
                    ));
                }
                files.push(physical);
                if files.len() > MAX_MAKEFILES {
                    return Err(CppError::new(
                        "qmake-metadata-too-many",
                        "qmake metadata exceeds 4096 Makefiles",
                    ));
                }
            }
        }
    }
    Ok(files)
}
fn observed_compilers(root: &Path, shadow: &Path) -> Result<(String, String), CppError> {
    let (mut c, mut cxx) = (BTreeSet::new(), BTreeSet::new());
    let mut total = 0;
    for path in makefiles(root, shadow)? {
        let bytes = read_contained(root, &path, MAX_MAKEFILE_BYTES)?;
        total += bytes.len();
        if total > MAX_METADATA_BYTES {
            return Err(CppError::new(
                "qmake-metadata-too-large",
                "qmake metadata exceeds aggregate byte bound",
            ));
        }
        let text = std::str::from_utf8(&bytes)
            .map_err(|_| CppError::new("qmake-metadata-encoding", "qmake metadata is not UTF-8"))?;
        if text.contains('\0') {
            return Err(CppError::new(
                "qmake-metadata-null",
                "qmake metadata contains a null byte",
            ));
        }
        for line in text.lines() {
            if let Some((name, value)) = line.split_once('=') {
                match name.trim() {
                    "CC" => {
                        c.insert(value.trim().to_owned());
                    }
                    "CXX" => {
                        cxx.insert(value.trim().to_owned());
                    }
                    _ => {}
                }
            }
        }
    }
    if c.len() != 1 || cxx.len() != 1 {
        return Err(CppError::new(
            "qmake-compiler-ambiguous",
            "qmake metadata must declare one consistent CC/CXX pair",
        ));
    }
    Ok((
        compiler(c.first().expect("one"))?,
        compiler(cxx.first().expect("one"))?,
    ))
}

/// Read only the generated CC/CXX assignments. Recipes and variable expressions are never evaluated.
pub fn qmake_capture_command(request: &QmakeCaptureRequest) -> Result<Vec<String>, CppError> {
    crate::build::validate_qmake_arguments(&request.arguments)?;
    for assignment in &request.assignments {
        let (name, value) = assignment.split_once("+=").ok_or_else(|| {
            CppError::new(
                "capture-flags",
                "Invalid capture instrumentation assignment",
            )
        })?;
        if !["QMAKE_CFLAGS", "QMAKE_CXXFLAGS", "QMAKE_LFLAGS"].contains(&name) {
            return Err(CppError::new(
                "capture-flags",
                "Unknown capture instrumentation assignment",
            ));
        }
        crate::build::analysis_flags(
            &value
                .split_whitespace()
                .map(str::to_owned)
                .collect::<Vec<_>>(),
        )?;
    }
    let root = request
        .root
        .canonicalize()
        .map_err(|error| CppError::new("capture-root", error.to_string()))?;
    let shadow = root.join(&request.shadow);
    let (_, scope, physical) = scoped(&root, &root, &request.shadow)?;
    if scope != "project"
        || physical != shadow
        || !request.shadow.starts_with(".ici/rust-v1/build/")
        || request.journal != format!("{}/capture.jsonl", request.shadow)
    {
        return Err(CppError::new(
            "capture-shadow",
            "qmake capture paths must belong to the owned Rust-v1 shadow",
        ));
    }
    let marker = strict_json(&read_contained(
        &root,
        &shadow.join(".ici-owner.json"),
        4096,
    )?)?;
    if marker["schema_id"] != "ici.rust.build-owner" || marker["digest"] != request.owner_digest {
        return Err(CppError::new(
            "capture-owner",
            "qmake shadow owner does not match request",
        ));
    }
    let (_, project_scope, project) = scoped(&root, &root, &request.project)?;
    if project_scope != "project" {
        return Err(CppError::new(
            "capture-project",
            "qmake project leaves the workspace",
        ));
    }
    if !request.ici_binary.is_absolute() {
        return Err(CppError::new(
            "capture-recorder",
            "ici recorder must be absolute",
        ));
    }
    let binary = request
        .ici_binary
        .to_str()
        .ok_or_else(|| CppError::new("capture-recorder", "ici recorder path must be UTF-8"))?;
    let (cc, cxx) = observed_compilers(&root, &shadow)?;
    let mut argv = vec![
        request.qmake.clone(),
        "-recursive".into(),
        project.display().to_string(),
    ];
    argv.extend(request.arguments.clone());
    argv.push("-after".into());
    argv.extend(request.assignments.clone());
    argv.extend([
        format!(
            "QMAKE_CC={} __qmake-record -- {}",
            quote(binary),
            quote(&cc)
        ),
        format!(
            "QMAKE_CXX={} __qmake-record -- {}",
            quote(binary),
            quote(&cxx)
        ),
        format!(
            "CONFIG+={}",
            if request.variant.eq_ignore_ascii_case("release") {
                "release"
            } else {
                "debug"
            }
        ),
        "CONFIG-=debug_and_release".into(),
    ]);
    Ok(argv)
}

/// Hidden same-binary entry point; accepts one bounded JSON argument after an optional `--`.
pub fn qmake_configure_capture_main(args: &[OsString]) -> i32 {
    let args = if args.first().is_some_and(|value| value == "--") {
        &args[1..]
    } else {
        args
    };
    let result = (|| -> Result<(), CppError> {
        if args.len() != 1 {
            return Err(CppError::new(
                "capture-request",
                "one JSON capture request is required",
            ));
        }
        let text = args[0]
            .to_str()
            .ok_or_else(|| CppError::new("capture-request", "capture request must be UTF-8"))?;
        if text.len() > 64 * 1024 {
            return Err(CppError::new(
                "capture-request-too-large",
                "capture request exceeds 64 KiB",
            ));
        }
        let value = strict_json(text.as_bytes())?;
        let request: QmakeCaptureRequest = serde_json::from_value(value)
            .map_err(|error| CppError::new("capture-request", error.to_string()))?;
        let cwd = std::env::current_dir()
            .map_err(|error| CppError::new("capture-cwd", error.to_string()))?;
        if cwd
            .canonicalize()
            .map_err(|error| CppError::new("capture-cwd", error.to_string()))?
            != request
                .root
                .join(&request.shadow)
                .canonicalize()
                .map_err(|error| CppError::new("capture-shadow", error.to_string()))?
        {
            return Err(CppError::new(
                "capture-cwd",
                "capture helper must run inside its owned shadow",
            ));
        }
        let expected = request.root.join(&request.journal);
        if std::env::var_os("ICI_QMAKE_CAPTURE_PATH").as_deref() != Some(expected.as_os_str()) {
            return Err(CppError::new(
                "capture-journal",
                "capture journal environment differs from request",
            ));
        }
        let argv = qmake_capture_command(&request)?;
        let error = Command::new(&argv[0]).args(&argv[1..]).exec();
        Err(CppError::new("qmake-exec", error.to_string()))
    })();
    if let Err(error) = result {
        eprintln!(
            "ici qmake configure capture: {}",
            error.to_string().chars().take(300).collect::<String>()
        );
    }
    125
}
