use crate::{
    input::{normalize, read_contained, scoped, strict_json},
    *,
};
use ici_domain::{TaskKind, TaskSpec};
use nix::{
    errno::Errno,
    fcntl::{OFlag, open, openat},
    sys::stat::{Mode, mkdirat},
};
use serde_json::json;
use std::{
    fs,
    path::{Component, Path, PathBuf},
    time::Duration,
};

/// Hold this guard across prepare, every build phase, and finalization. The transaction key is
/// distinct from TaskSpec's per-phase resource key, so Scheduler can keep its normal OS locks.
pub struct BuildLease {
    root: PathBuf,
    shadow: String,
    _locks: ici_execution::scheduler::ResourceLocks,
}
impl BuildLease {
    pub fn acquire(
        root: &Path,
        plan: &BuildPlan,
        cancellation: &ici_execution::Cancellation,
        timeout: Duration,
    ) -> Result<Self, CppError> {
        let root = root
            .canonicalize()
            .map_err(|error| CppError::new("invalid-root", error.to_string()))?;
        if !identifier(&plan.id) || !identifier(&plan.variant) || !valid_shadow(plan) {
            return Err(CppError::new(
                "invalid-shadow",
                "build lease requires an owned Rust-v1 shadow",
            ));
        }
        let locks = ici_execution::scheduler::ResourceLocks::acquire(
            &root.join(".ici/rust-v1/locks/builds"),
            &[format!("build-transaction:{}", plan.shadow)],
            cancellation,
            timeout,
        )
        .map_err(|error| CppError::new("build-lease", error.to_string()))?;
        Ok(Self {
            root,
            shadow: plan.shadow.clone(),
            _locks: locks,
        })
    }
    fn matches(&self, plan: &BuildPlan) -> Result<(), CppError> {
        if self.shadow != plan.shadow {
            return Err(CppError::new(
                "build-lease-mismatch",
                "lease does not own this build plan",
            ));
        }
        Ok(())
    }
    pub fn prepare(&self, plan: &BuildPlan) -> Result<PathBuf, CppError> {
        self.matches(plan)?;
        prepare_owned_shadow(&self.root, plan)
    }
    pub fn finalize_qmake(&self, plan: &BuildPlan) -> Result<CompilationContext, CppError> {
        self.matches(plan)?;
        finalize_qmake_capture(&self.root, plan)
    }
}

fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "_.-".contains(c))
        && value != "."
        && value != ".."
}
/// Rust-owned shadows are fixed per build/variant. Only configured Make may name a custom,
/// variant-shared project-relative shadow; it still passes the no-follow ownership guard.
fn valid_shadow(plan: &BuildPlan) -> bool {
    if plan.shadow == format!(".ici/rust-v1/build/{}/{}", plan.id, plan.variant) {
        return true;
    }
    plan.system == "make" && valid_make_shadow(&plan.shadow)
}
fn valid_make_shadow(shadow: &str) -> bool {
    crate::contained_make_path(&serde_json::Value::String(shadow.into()), "shadow", false)
        .is_ok_and(|normal| normal == shadow)
        && !shadow.split('/').any(|part| part.contains('\0'))
        && shadow.split('/').next() != Some(".ici")
}
fn make_owner_record(plan: &BuildPlan) -> String {
    format!(
        ".ici/rust-v1/build/make-owners/{}.json",
        &ici_domain::sha256(&plan.shadow)[7..39]
    )
}
fn tool(request: &BuildRequest, name: &str) -> String {
    request
        .tools
        .get(name)
        .cloned()
        .unwrap_or_else(|| name.into())
}

pub(crate) fn analysis_flags(flags: &[String]) -> Result<(), CppError> {
    for flag in flags {
        let exact = [
            "-O0",
            "-O1",
            "-O2",
            "-O3",
            "-Og",
            "-Os",
            "-Oz",
            "-g",
            "-g1",
            "-g2",
            "-g3",
            "--coverage",
            "-fprofile-arcs",
            "-ftest-coverage",
            "-fno-omit-frame-pointer",
            "-fno-inline",
            "-ffunction-sections",
            "-fdata-sections",
            "-fno-lto",
            "-fno-pie",
            "-no-pie",
            "-pthread",
            "-static-libasan",
            "-static-libubsan",
            "-static-libtsan",
            "-Wl,--gc-sections",
            "-Wl,--print-gc-sections",
        ];
        let sanitize = ["-fsanitize=", "-fno-sanitize=", "-fno-sanitize-recover="]
            .iter()
            .any(|prefix| {
                flag.strip_prefix(prefix).is_some_and(|tail| {
                    !tail.is_empty()
                        && tail
                            .bytes()
                            .all(|byte| byte.is_ascii_lowercase() || byte == b',' || byte == b'-')
                })
            });
        if !exact.contains(&flag.as_str()) && !sanitize {
            return Err(CppError::new(
                "unsupported-analysis-build-flag",
                format!(
                    "Additional analysis flag {flag:?} is outside the bounded instrumentation contract"
                ),
            ));
        }
    }
    Ok(())
}
fn qmake_assignments(request: &BuildRequest) -> Vec<String> {
    [
        ("QMAKE_CFLAGS", &request.extra_c_flags),
        ("QMAKE_CXXFLAGS", &request.extra_cxx_flags),
        ("QMAKE_LFLAGS", &request.extra_link_flags),
    ]
    .into_iter()
    .filter(|(_, flags)| !flags.is_empty())
    .map(|(key, flags)| format!("{key}+={}", flags.join(" ")))
    .collect()
}
pub(crate) fn validate_qmake_arguments(arguments: &[String]) -> Result<(), CppError> {
    if arguments.len() > 256
        || arguments.iter().map(String::len).sum::<usize>() > 32 * 1024
        || arguments.iter().any(|argument| {
            argument.contains(['\0', '\r', '\n'])
                || argument.starts_with("-o")
                || ["-set", "-unset", "-install", "-query", "-project"].contains(&argument.as_str())
        })
    {
        return Err(CppError::new(
            "unsupported-qmake-argument",
            "qmake arguments may only configure the owned native project",
        ));
    }
    Ok(())
}

/// Pure command construction: no mkdir, package install, configure, build, or version probe.
pub fn plan_build(root: &Path, request: &BuildRequest) -> Result<BuildPlan, CppError> {
    let mut effective = request.clone();
    let (compile, link): (&[&str], &[&str]) = match request.variant.as_str() {
        "coverage" => (&["--coverage"], &["--coverage"]),
        "sanitize" => (
            &[
                "-fsanitize=address,undefined",
                "-fno-omit-frame-pointer",
                "-g",
            ],
            &["-fsanitize=address,undefined"],
        ),
        "thread-sanitize" => (
            &["-fsanitize=thread", "-fno-omit-frame-pointer", "-g"],
            &["-fsanitize=thread"],
        ),
        _ => (&[], &[]),
    };
    effective
        .extra_c_flags
        .splice(0..0, compile.iter().map(|flag| (*flag).to_owned()));
    effective
        .extra_cxx_flags
        .splice(0..0, compile.iter().map(|flag| (*flag).to_owned()));
    effective
        .extra_link_flags
        .splice(0..0, link.iter().map(|flag| (*flag).to_owned()));
    let request = &effective;
    if !root.is_absolute()
        || !identifier(&request.id)
        || !identifier(&request.variant)
        || !(1..=64).contains(&request.jobs)
    {
        return Err(CppError::new(
            "invalid-build-request",
            "build id/variant/jobs/root must be explicit and bounded",
        ));
    }
    if Path::new(&request.project).is_absolute()
        || Path::new(&request.project)
            .components()
            .any(|c| matches!(c, Component::ParentDir))
        || request.project.contains(['\0', '\\'])
    {
        return Err(CppError::new(
            "invalid-project",
            "build project must stay workspace-relative",
        ));
    }
    analysis_flags(&request.extra_c_flags)?;
    analysis_flags(&request.extra_cxx_flags)?;
    analysis_flags(&request.extra_link_flags)?;
    validate_qmake_arguments(&request.extra_qmake_args)?;
    let make = if request.system == "make" {
        Some(request.make.as_ref().ok_or_else(|| {
            CppError::new(
                "missing-make-contract",
                "Make backend requires the validated build.make command contract",
            )
        })?)
    } else {
        None
    };
    let shadow = match make {
        Some(make) if valid_make_shadow(&make.shadow) => make.shadow.clone(),
        Some(make) => {
            return Err(CppError::new(
                "make-config",
                format!(
                    "build.make.shadow_dir {:?} must be a contained non-root directory outside .ici",
                    make.shadow
                ),
            ));
        }
        None => format!(".ici/rust-v1/build/{}/{}", request.id, request.variant),
    };
    let shadow_path = root.join(&shadow);
    let project = root.join(&request.project);
    // Configured Make shares one shadow across variants, so its owner excludes the variant.
    let identity = if make.is_some() {
        json!({"schema":"ici.rust.make-owner/v1","root":root,"id":request.id,"system":"make","shadow":shadow})
    } else {
        json!({"schema":"ici.rust.build-owner/v1","root":root,"id":request.id,"variant":request.variant,"project":request.project})
    };
    let digest = ici_domain::sha256(
        ici_domain::canonical_json(&identity)
            .map_err(|e| CppError::new("identity", e.to_string()))?,
    );
    let mut limitations = vec![];
    let mut tasks = vec![];
    let mut capture = None;
    let configure_id = format!("build:{}:{}:configure", request.id, request.variant);
    let task = |id: String, provider: &str, argv: Vec<String>, cwd: &Path| {
        let mut task = TaskSpec::command(id, provider, argv, cwd.display().to_string());
        task.kind = TaskKind::Prepare;
        task.cacheable = false;
        task.timeout_seconds = Some(900.0);
        task.resource_keys = vec![format!("build:{}", shadow)];
        task.work_dirs = vec![shadow_path.display().to_string()];
        task.env_overlay = request.environment.clone();
        task.variant = Some(request.variant.clone());
        task
    };
    match request.system.as_str() {
        "cmake" => {
            let source = if project.file_name().is_some_and(|n| n == "CMakeLists.txt") {
                project.parent().unwrap().to_path_buf()
            } else {
                project
            };
            let build_type = if request.variant.eq_ignore_ascii_case("release") {
                "Release"
            } else {
                "Debug"
            };
            let mut argv = vec![
                tool(request, "cmake"),
                "-S".into(),
                source.display().to_string(),
                "-B".into(),
                shadow_path.display().to_string(),
                "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON".into(),
                format!("-DCMAKE_BUILD_TYPE={build_type}"),
            ];
            if !request.compiler_c.is_empty() {
                argv.push(format!("-DCMAKE_C_COMPILER={}", request.compiler_c));
            }
            if !request.compiler_cxx.is_empty() {
                argv.push(format!("-DCMAKE_CXX_COMPILER={}", request.compiler_cxx));
            }
            for arg in &request.extra_cmake_args {
                if !arg.starts_with("-D") || arg.contains('\0') {
                    return Err(CppError::new(
                        "invalid-cmake-option",
                        "extra CMake options must be direct -D definitions",
                    ));
                }
                argv.push(arg.clone());
            }
            for (key, environment, flags) in [
                ("CMAKE_C_FLAGS", "CFLAGS", &request.extra_c_flags),
                ("CMAKE_CXX_FLAGS", "CXXFLAGS", &request.extra_cxx_flags),
                (
                    "CMAKE_EXE_LINKER_FLAGS",
                    "LDFLAGS",
                    &request.extra_link_flags,
                ),
            ] {
                if flags.is_empty() {
                    continue;
                }
                let prefix = format!("-D{key}=");
                let base = request
                    .extra_cmake_args
                    .iter()
                    .rev()
                    .find_map(|argument| {
                        let (name, value) = argument.strip_prefix("-D")?.split_once('=')?;
                        (name.split(':').next() == Some(key)).then(|| value.to_owned())
                    })
                    .or_else(|| request.environment.get(environment).cloned())
                    .or_else(|| std::env::var(environment).ok())
                    .unwrap_or_default();
                argv.push(format!(
                    "{prefix}{}{}{}",
                    base,
                    if base.is_empty() { "" } else { " " },
                    flags.join(" ")
                ));
            }
            tasks.push(task(configure_id.clone(), "cmake", argv, root));
            let mut build = task(
                format!("build:{}:{}:compile", request.id, request.variant),
                "cmake",
                vec![
                    tool(request, "cmake"),
                    "--build".into(),
                    shadow_path.display().to_string(),
                    "--parallel".into(),
                    request.jobs.to_string(),
                ],
                root,
            );
            build.depends_on = vec![configure_id];
            tasks.push(build);
        }
        "qmake" => {
            let binary = request.ici_binary.as_ref().ok_or_else(|| {
                CppError::new(
                    "missing-recorder",
                    "qmake capture requires the ici Rust binary path",
                )
            })?;
            if !binary.is_absolute() || binary.as_os_str().is_empty() {
                return Err(CppError::new(
                    "unsupported-recorder-path",
                    "ici recorder path must be absolute",
                ));
            }
            let journal = format!("{shadow}/capture.jsonl");
            capture = Some(journal.clone());
            let probe_id = format!("{configure_id}:probe");
            let mut probe_argv = vec![
                tool(request, "qmake"),
                "-recursive".into(),
                project.display().to_string(),
            ];
            probe_argv.extend(request.extra_qmake_args.clone());
            probe_argv.push("-after".into());
            probe_argv.extend(qmake_assignments(request));
            if !request.compiler_c.is_empty() {
                probe_argv.push(format!("QMAKE_CC={}", request.compiler_c));
            }
            if !request.compiler_cxx.is_empty() {
                probe_argv.push(format!("QMAKE_CXX={}", request.compiler_cxx));
            }
            probe_argv.extend([
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
            tasks.push(task(probe_id.clone(), "qmake", probe_argv, &shadow_path));
            let capture_request = QmakeCaptureRequest {
                root: root.to_path_buf(),
                shadow: shadow.clone(),
                owner_digest: digest.clone(),
                project: request.project.clone(),
                variant: request.variant.clone(),
                qmake: tool(request, "qmake"),
                ici_binary: binary.clone(),
                journal: journal.clone(),
                arguments: request.extra_qmake_args.clone(),
                assignments: qmake_assignments(request),
            };
            let encoded = ici_domain::canonical_json(&capture_request)
                .map_err(|error| CppError::new("capture-request", error.to_string()))?;
            if encoded.len() > 64 * 1024 {
                return Err(CppError::new(
                    "capture-request-too-large",
                    "qmake capture request exceeds 64 KiB",
                ));
            }
            let mut configure = task(
                configure_id.clone(),
                "qmake-capture",
                vec![
                    binary.display().to_string(),
                    "__qmake-configure-capture".into(),
                    "--".into(),
                    encoded,
                ],
                &shadow_path,
            );
            configure.depends_on = vec![probe_id];
            configure.env_overlay.insert(
                "ICI_QMAKE_CAPTURE_PATH".into(),
                root.join(&journal).display().to_string(),
            );
            tasks.push(configure);
            let mut build = task(
                format!("build:{}:{}:compile", request.id, request.variant),
                "make",
                vec![
                    tool(request, "make"),
                    "-B".into(),
                    format!("-j{}", request.jobs),
                ],
                &shadow_path,
            );
            build.env_overlay.insert(
                "ICI_QMAKE_CAPTURE_PATH".into(),
                root.join(journal).display().to_string(),
            );
            build.depends_on = vec![configure_id];
            tasks.push(build);
        }
        "make" => {
            let make = make.expect("checked Make contract");
            if make.build_argv.is_empty() {
                return Err(CppError::new(
                    "missing-build-argv",
                    "Make backend requires an explicit direct build argv",
                ));
            }
            if !(1..=64).contains(&make.jobs)
                || !(1..=make.jobs).contains(&make.effective_jobs)
                || !["allow", "required"].contains(&make.out_of_tree.as_str())
                || (make.out_of_tree == "required" && make.workdir == ".")
            {
                return Err(CppError::new(
                    "make-config",
                    "Make jobs/out_of_tree/workdir contract is inconsistent",
                ));
            }
            let workdir = crate::contained_make_path(
                &serde_json::Value::String(make.workdir.clone()),
                "build.make.workdir",
                true,
            )?;
            if workdir != make.workdir {
                return Err(CppError::new(
                    "make-config",
                    "build.make.workdir must be normalized",
                ));
            }
            let cwd = if workdir == "." {
                root.to_path_buf()
            } else {
                root.join(&workdir)
            };
            // Read-only check: the cwd must already be a contained real directory. ici never
            // creates, adopts or writes this user directory; only the shadow is ici-owned.
            if !fs::symlink_metadata(&cwd).is_ok_and(|metadata| metadata.is_dir())
                || cwd.canonicalize().ok().as_deref() != Some(cwd.as_path())
            {
                return Err(CppError::new(
                    "make-config",
                    format!(
                        "build.make.workdir {workdir:?} must be an existing project directory without symlink traversal"
                    ),
                ));
            }
            if make.effective_jobs < make.jobs {
                limitations.push(format!(
                    "build.make.jobs={} was capped to {} native parallel jobs; whole {{jobs}} tokens receive {}",
                    make.jobs, make.effective_jobs, make.effective_jobs
                ));
            }
            let phases = [
                (
                    "configure",
                    "build.make.configure_argv",
                    &make.configure_argv,
                ),
                ("clean", "build.make.clean_argv", &make.clean_argv),
                ("compile", "build.make.build_argv", &make.build_argv),
            ];
            let mut previous: Option<String> = None;
            for (phase, setting, argv) in phases {
                if argv.is_empty() {
                    continue;
                }
                crate::validate_direct_make_argv(argv, setting)?;
                let mut step = task(
                    format!("build:{}:{}:{phase}", request.id, request.variant),
                    "make",
                    argv.clone(),
                    &cwd,
                );
                // Frozen order: configure, then clean, then build; a failed step stops the chain.
                if let Some(previous) = previous.replace(step.id.clone()) {
                    step.depends_on = vec![previous];
                }
                tasks.push(step);
            }
        }
        _ => {
            return Err(CppError::new(
                "unsupported-build-system",
                "build system must be cmake, qmake or make",
            ));
        }
    }
    Ok(BuildPlan {
        id: request.id.clone(),
        system: request.system.clone(),
        variant: request.variant.clone(),
        shadow: shadow.clone(),
        owner_digest: digest,
        tasks,
        compilation_database: format!("{shadow}/compile_commands.json"),
        capture_journal: capture,
        generated_roots: vec![shadow],
        limitations,
    })
}

/// Explicit execution preparation. Never adopts nonempty, unowned or symlinked directories.
pub fn prepare_owned_shadow(root: &Path, plan: &BuildPlan) -> Result<PathBuf, CppError> {
    let root = root
        .canonicalize()
        .map_err(|e| CppError::new("invalid-root", e.to_string()))?;
    if !valid_shadow(plan) || !identifier(&plan.id) || !identifier(&plan.variant) {
        return Err(CppError::new(
            "invalid-shadow",
            "shadow must use the owned Rust-v1 namespace",
        ));
    }
    let custom = plan.shadow != format!(".ici/rust-v1/build/{}/{}", plan.id, plan.variant);
    let record = custom.then(|| make_owner_record(plan));
    let path = owned_directory(&root, &plan.shadow)?;
    let marker = path.join(".ici-owner.json");
    // A configured Make clean recipe may delete and recreate its whole shadow, including the
    // in-shadow marker. The Rust-namespace record proves this exact path was adopted earlier
    // for the same owner; any other nonempty directory remains unowned and untouched.
    let recorded = match &record {
        Some(record) if root.join(record).exists() => {
            let metadata = strict_json(&read_contained(&root, &root.join(record), 4096)?)?;
            metadata["schema_id"] == "ici.rust.make-shadow-owner"
                && metadata["digest"] == plan.owner_digest
                && metadata["shadow"] == plan.shadow
        }
        _ => false,
    };
    if marker.exists() {
        let bytes = read_contained(&root, &marker, 4096)?;
        let metadata = strict_json(&bytes)?;
        if metadata["schema_id"] != "ici.rust.build-owner"
            || metadata["digest"] != plan.owner_digest
        {
            return Err(CppError::new(
                "ownership-mismatch",
                "build directory belongs to another request/project",
            ));
        }
    } else {
        if !recorded
            && fs::read_dir(&path)
                .map_err(|e| CppError::new("shadow-inspect", e.to_string()))?
                .next()
                .is_some()
        {
            return Err(CppError::new(
                "unowned-build-directory",
                if custom {
                    format!(
                        "refusing to adopt nonempty build.make.shadow_dir {:?} that ici did not create; move its contents away or choose an empty shadow_dir",
                        plan.shadow
                    )
                } else {
                    "refusing to adopt a nonempty build directory".into()
                },
            ));
        }
        let bytes=serde_json::to_vec(&json!({"schema_id":"ici.rust.build-owner","schema_version":1,"digest":plan.owner_digest,"id":plan.id,"variant":plan.variant})).unwrap();
        ici_execution::storage::atomic_write(
            &marker,
            &bytes,
            ici_execution::storage::WriteMode::CreateNew,
        )
        .map_err(|e| CppError::new("owner-write", e.to_string()))?;
    }
    if let Some(record) = &record {
        owned_directory(&root, ".ici/rust-v1/build/make-owners")?;
        let bytes = serde_json::to_vec(&json!({"schema_id":"ici.rust.make-shadow-owner","schema_version":1,"digest":plan.owner_digest,"id":plan.id,"shadow":plan.shadow})).unwrap();
        ici_execution::storage::atomic_write(
            &root.join(record),
            &bytes,
            ici_execution::storage::WriteMode::ReplaceRegular,
        )
        .map_err(|e| CppError::new("owner-write", e.to_string()))?;
    }
    if let Some(journal) = &plan.capture_journal {
        if journal != &format!("{}/capture.jsonl", plan.shadow) {
            return Err(CppError::new(
                "invalid-capture-path",
                "capture journal must be in owned shadow",
            ));
        }
        // Replacement is atomic and refuses symlinks, hard links and redirected ancestors.
        // Never truncate an existing inode before validating its identity.
        ici_execution::storage::atomic_write(
            &root.join(journal),
            &[],
            ici_execution::storage::WriteMode::ReplaceRegular,
        )
        .map_err(|e| CppError::new("capture-reset", e.to_string()))?;
    }
    Ok(path)
}

/// Create or open each component without following symlinks; returns the physical path.
fn owned_directory(root: &Path, relative: &str) -> Result<PathBuf, CppError> {
    let flags = OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC;
    let mut directory = open(root, flags, Mode::empty())
        .map_err(|e| CppError::new("shadow-open", e.to_string()))?;
    let mut path = root.to_path_buf();
    for part in Path::new(relative).components() {
        let Component::Normal(name) = part else {
            return Err(CppError::new(
                "invalid-shadow",
                "non-normal shadow component",
            ));
        };
        path.push(name);
        match mkdirat(&directory, Path::new(name), Mode::S_IRWXU) {
            Ok(()) | Err(Errno::EEXIST) => {}
            Err(e) => return Err(CppError::new("shadow-create", e.to_string())),
        }
        directory = openat(&directory, Path::new(name), flags, Mode::empty())
            .map_err(|e| CppError::new("unsafe-shadow", e.to_string()))?;
    }
    Ok(path)
}

pub fn finalize_qmake_capture(
    root: &Path,
    plan: &BuildPlan,
) -> Result<CompilationContext, CppError> {
    let root = root
        .canonicalize()
        .map_err(|e| CppError::new("invalid-root", e.to_string()))?;
    let journal = plan
        .capture_journal
        .as_ref()
        .ok_or_else(|| CppError::new("missing-capture", "build plan has no qmake journal"))?;
    let marker = root.join(&plan.shadow).join(".ici-owner.json");
    let owner = strict_json(&read_contained(&root, &marker, 4096)?)?;
    if owner["digest"] != plan.owner_digest {
        return Err(CppError::new(
            "ownership-mismatch",
            "capture shadow ownership differs",
        ));
    }
    let bytes = read_contained(&root, &root.join(journal), 32 * 1024 * 1024)?;
    let mut rows = vec![];
    for line in bytes.split(|b| *b == b'\n').filter(|line| !line.is_empty()) {
        if rows.len() >= 200_000 || line.len() > 1024 * 1024 {
            return Err(CppError::new(
                "capture-too-large",
                "capture record bounds exceeded",
            ));
        }
        let mut row = strict_json(line)?;
        let directory = row["directory"]
            .as_str()
            .ok_or_else(|| CppError::new("invalid-capture", "capture lacks cwd"))?;
        let (_, scope, cwd) = scoped(&root, &root, directory)?;
        if scope != "project" || !cwd.starts_with(root.join(&plan.shadow)) {
            return Err(CppError::new(
                "capture-outside-shadow",
                "compiler cwd does not belong to capture shadow",
            ));
        }
        let argv: Vec<String> = serde_json::from_value(row["arguments"].clone())
            .map_err(|e| CppError::new("invalid-capture", e.to_string()))?;
        crate::arguments::validate(&argv)?;
        let mut candidates = vec![];
        let mut skip = false;
        for token in argv.iter().skip(1) {
            if skip {
                skip = false;
                continue;
            }
            if [
                "-D",
                "-I",
                "-MF",
                "-MQ",
                "-MT",
                "-include",
                "-imacros",
                "-iquote",
                "-isystem",
                "-isysroot",
                "-o",
                "-x",
                "--sysroot",
            ]
            .contains(&token.as_str())
            {
                skip = true;
                continue;
            }
            if !token.starts_with(['-', '@']) && crate::context::is_translation_unit(token) {
                let (path, scope, physical) = scoped(&root, &cwd, token)?;
                if scope == "project" && physical.is_file() {
                    candidates.push(path);
                }
            }
        }
        candidates.sort();
        candidates.dedup();
        if candidates.len() != 1 {
            return Err(CppError::new(
                "capture-source-ambiguous",
                "captured compile must name exactly one contained source",
            ));
        }
        row["file"] = json!(root.join(&candidates[0]).display().to_string());
        rows.push(row);
    }
    if rows.is_empty() {
        return Err(CppError::new(
            "capture-empty",
            "qmake build produced no measured compiler invocations",
        ));
    }
    let output = root.join(&plan.compilation_database);
    if normalize(output.parent().unwrap()) != root.join(&plan.shadow) {
        return Err(CppError::new(
            "unsafe-capture-output",
            "compile database must remain inside owned shadow",
        ));
    }
    let encoded = serde_json::to_vec_pretty(&rows).unwrap();
    ici_execution::storage::atomic_write(
        &output,
        &encoded,
        ici_execution::storage::WriteMode::ReplaceRegular,
    )
    .map_err(|e| CppError::new("capture-publish", e.to_string()))?;
    Ok(crate::load_compilation_context(
        &root,
        &ContextRequest {
            database: Some(plan.compilation_database.clone()),
            variant: Some(plan.variant.clone()),
            ..Default::default()
        },
    ))
}
