//! Scope-aware diagnostics. Only bounded executable version queries may run here.
use crate::{
    commands::{CliError, CommandOutput, Result, under},
    planning::{self, PreparedPlan},
    registry::Invocation,
};
use ici_application::plan::{Work, engine_name, file_language};
use ici_config::Component;
use ici_domain::{TaskKind, TaskSpec, TaskState};
use ici_execution::{
    Cancellation, Runner, Scheduler, TaskOutcome,
    redaction::{redact_text, redact_value},
};
use ici_providers::resolver::{ResolvedTool, ToolRequest};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

const MAX_TOOLS: usize = 64;
const PROBE_TIMEOUT: f64 = 1.0;
/// Frozen `core/toolchain` `PROBE_TIMEOUT_SECONDS`, kept for the Python-module probes whose
/// interpreter start-up and import (mypy) routinely exceed the native one-second probe.
const MODULE_PROBE_TIMEOUT: f64 = crate::legacy_toolchain::PROBE_TIMEOUT_SECONDS;
const PROBE_BYTES: usize = 8192;
#[derive(Default)]
struct Policy {
    required_by: BTreeSet<String>,
    optional_by: BTreeSet<String>,
}
struct Need {
    path: Option<PathBuf>,
    digest: Option<String>,
    origin: String,
    error: Option<String>,
    aliases: BTreeMap<String, Policy>,
    essential_for: BTreeSet<String>,
    version_args: Option<Vec<String>>,
    /// The Python module probed through `path` (an interpreter), as frozen `python_module`.
    module: Option<String>,
    /// The candidate name that resolved (frozen `resolved_alias`).
    alias: String,
    /// Legacy view: the frozen metadata probe run after a successful version probe.
    detail: Option<crate::legacy_toolchain::Detail>,
    probe_id: Option<String>,
    outcome: Option<TaskOutcome>,
    detail_outcome: Option<TaskOutcome>,
}
#[derive(Default)]
struct Inventory {
    needs: BTreeMap<String, Need>,
    bindings: BTreeMap<String, BTreeSet<String>>,
    /// Frozen `core/toolchain` probe argv and row values (legacy `verify` and `doctor`).
    legacy: bool,
}
impl Inventory {
    fn add(
        &mut self,
        name: &str,
        tool: Option<&ResolvedTool>,
        reason: Option<String>,
        owner: &str,
        required: bool,
        essential: bool,
    ) -> String {
        // Frozen probes each registered name on its own (`qt5` and `pkg-config` are one
        // executable with different argv), so a legacy need is per name.
        let key = match tool {
            Some(tool) if self.legacy => {
                format!("{}\0{}\0{name}", tool.path.display(), tool.digest)
            }
            Some(tool) => format!("{}\0{}", tool.path.display(), tool.digest),
            None => format!("missing:{name}:{}", reason.as_deref().unwrap_or_default()),
        };
        let need = self.needs.entry(key.clone()).or_insert_with(|| Need {
            path: tool.map(|tool| tool.path.clone()),
            digest: tool.map(|tool| tool.digest.clone()),
            origin: tool
                .map(|tool| {
                    serde_json::to_value(&tool.source)
                        .expect("source enum")
                        .as_str()
                        .expect("string enum")
                        .to_owned()
                })
                .unwrap_or_else(|| "unresolved".into()),
            error: reason,
            aliases: BTreeMap::new(),
            essential_for: BTreeSet::new(),
            version_args: tool.and_then(|tool| {
                if self.legacy {
                    crate::legacy_toolchain::lookup(name).map(|registered| {
                        registered
                            .version
                            .iter()
                            .map(|arg| (*arg).to_owned())
                            .collect()
                    })
                } else {
                    version_arguments(&tool.id, &tool.path)
                }
            }),
            module: None,
            alias: tool.map(|tool| tool.id.clone()).unwrap_or_default(),
            detail: if self.legacy {
                crate::legacy_toolchain::lookup(name).and_then(|registered| registered.detail)
            } else {
                None
            },
            probe_id: None,
            outcome: None,
            detail_outcome: None,
        });
        let policy = need.aliases.entry(name.into()).or_default();
        if required {
            policy.required_by.insert(owner.into());
        } else {
            policy.optional_by.insert(owner.into());
        }
        if essential {
            need.essential_for.insert(owner.into());
        }
        self.bindings
            .entry(owner.into())
            .or_default()
            .insert(key.clone());
        key
    }
    /// A frozen Python-module tool: `interpreter -m <name> --version`, one need per module.
    fn add_module(
        &mut self,
        name: &str,
        interpreter: &ResolvedTool,
        owner: &str,
        required: bool,
    ) -> String {
        let key = format!(
            "{}\0{}\0-m {name}",
            interpreter.path.display(),
            interpreter.digest
        );
        self.needs.entry(key.clone()).or_insert_with(|| Need {
            path: Some(interpreter.path.clone()),
            digest: Some(interpreter.digest.clone()),
            origin: serde_json::to_value(&interpreter.source)
                .expect("source enum")
                .as_str()
                .expect("string enum")
                .to_owned(),
            error: None,
            aliases: BTreeMap::new(),
            essential_for: BTreeSet::new(),
            version_args: Some(vec!["-m".into(), name.into(), "--version".into()]),
            module: Some(name.into()),
            alias: format!(
                "{} -m {name}",
                interpreter
                    .path
                    .file_name()
                    .map(|file| file.to_string_lossy().into_owned())
                    .unwrap_or_default()
            ),
            detail: None,
            probe_id: None,
            outcome: None,
            detail_outcome: None,
        });
        let need = self.needs.get_mut(&key).expect("inserted need");
        let policy = need.aliases.entry(name.into()).or_default();
        if required {
            policy.required_by.insert(owner.into());
        } else {
            policy.optional_by.insert(owner.into());
        }
        if required {
            need.essential_for.insert(owner.into());
        }
        self.bindings
            .entry(owner.into())
            .or_default()
            .insert(key.clone());
        key
    }
    fn execute(
        &mut self,
        root: &Path,
        environment: &BTreeMap<String, String>,
        cancellation: &Cancellation,
    ) -> Result<()> {
        if self
            .needs
            .values()
            .filter(|need| need.path.is_some())
            .count()
            > MAX_TOOLS
        {
            return Err(CliError(format!(
                "doctor inventory exceeds {MAX_TOOLS} distinct executables; narrow the selected scope or doctor.required_tools"
            )));
        }
        let legacy = self.legacy;
        let limit = if legacy {
            crate::legacy_toolchain::PROBE_OUTPUT_LIMIT
        } else {
            PROBE_BYTES
        };
        // `<interpreter> -m <module>` puts its working directory first on `sys.path`: module
        // probes run from an empty directory (and with PYTHONSAFEPATH) so a project file named
        // like the module is never imported by a capability probe.
        let neutral = if self.needs.values().any(|need| need.module.is_some()) {
            Some(tempfile::tempdir().map_err(|error| {
                CliError(format!("cannot create the module probe directory: {error}"))
            })?)
        } else {
            None
        };
        let mut tasks = vec![];
        for (index, need) in self.needs.values_mut().enumerate() {
            let (Some(path), Some(args)) = (&need.path, &need.version_args) else {
                continue;
            };
            let id = format!("doctor.version.{index}");
            let timeout = if legacy || need.module.is_some() {
                MODULE_PROBE_TIMEOUT
            } else {
                PROBE_TIMEOUT
            };
            let mut task = probe_task(
                &id,
                path,
                args,
                match (&need.module, &neutral) {
                    (Some(_), Some(directory)) => directory.path(),
                    _ => root,
                },
                need.digest.as_deref(),
                timeout,
                limit,
            );
            if need.module.is_some() {
                task.env_overlay.insert("PYTHONSAFEPATH".into(), "1".into());
            }
            tasks.push(task);
            need.probe_id = Some(id);
        }
        let outcomes = run_probes(&tasks, environment, cancellation)?;
        for need in self.needs.values_mut() {
            need.outcome = need
                .probe_id
                .as_ref()
                .and_then(|id| outcomes.get(id))
                .cloned();
        }
        // Frozen runs the metadata probe only after a successful version probe.
        let mut details = vec![];
        let mut detail_ids = BTreeMap::new();
        for (index, (key, need)) in self.needs.iter().enumerate() {
            let (Some(kind), Some(path), Some(outcome)) = (need.detail, &need.path, &need.outcome)
            else {
                continue;
            };
            if outcome.timed_out || outcome.truncated || outcome.exit_code != Some(0) {
                continue;
            }
            let id = format!("doctor.detail.{index}");
            let args: Vec<String> = kind.args().iter().map(|arg| (*arg).to_owned()).collect();
            details.push(probe_task(
                &id,
                path,
                &args,
                root,
                need.digest.as_deref(),
                crate::legacy_toolchain::PROBE_TIMEOUT_SECONDS,
                limit,
            ));
            detail_ids.insert(key.clone(), id);
        }
        if !details.is_empty() {
            let outcomes = run_probes(&details, environment, cancellation)?;
            for (key, id) in detail_ids {
                if let Some(need) = self.needs.get_mut(&key) {
                    need.detail_outcome = outcomes.get(&id).cloned();
                }
            }
        }
        Ok(())
    }
    /// The version probe timeout `execute` gave this need.
    fn timeout(&self, need: &Need) -> f64 {
        if self.legacy || need.module.is_some() {
            MODULE_PROBE_TIMEOUT
        } else {
            PROBE_TIMEOUT
        }
    }
    fn problems(&self, owner: &str) -> Vec<String> {
        self.bindings
            .get(owner)
            .into_iter()
            .flatten()
            .filter_map(|key| {
                let need = &self.needs[key];
                if !need.essential_for.contains(owner) {
                    return None;
                }
                if need.path.is_none() {
                    return Some(
                        need.error
                            .clone()
                            .unwrap_or_else(|| "required executable was not resolved".into()),
                    );
                }
                need.outcome
                    .as_ref()
                    .filter(|outcome| !probe_complete(outcome))
                    .map(|outcome| probe_error(outcome, self.timeout(need)))
            })
            .collect()
    }
    fn rows(&self) -> Vec<Value> {
        let mut rows = vec![];
        for need in self.needs.values() {
            let complete = need.outcome.as_ref().is_some_and(probe_complete);
            let version = need
                .outcome
                .as_ref()
                .filter(|outcome| probe_complete(outcome))
                .and_then(version_text);
            let error = need.error.clone().or_else(|| {
                need.outcome
                    .as_ref()
                    .filter(|outcome| !probe_complete(outcome))
                    .map(|outcome| probe_error(outcome, self.timeout(need)))
            });
            for (name, policy) in &need.aliases {
                let mut row = json!({"name":name,"available":need.path.is_some(),"complete":complete,"path":need.path,"version":version,"origin":need.origin,"sha256":need.digest,"required":!policy.required_by.is_empty(),"optional":policy.required_by.is_empty()&&!policy.optional_by.is_empty(),"required_by":policy.required_by,"optional_by":policy.optional_by,"error":error,"details":{"evidence":if complete {"version-only"} else {"NOT_RUN"},"analysis_executed":false}});
                if let (Some(module), Some(path)) = (&need.module, &need.path) {
                    let interpreter = path
                        .file_name()
                        .map(|name| name.to_string_lossy().into_owned())
                        .unwrap_or_default();
                    row["details"]["provider"] = json!("python-module");
                    row["details"]["module"] = json!(module);
                    row["details"]["resolved_alias"] = json!(format!("{interpreter} -m {module}"));
                }
                if self.legacy {
                    legacy_values(&mut row, name, need, &policy.required_by);
                }
                if let Some(outcome) = &need.outcome {
                    row["probe"] = json!({"task_id":outcome.task_id,"argv":std::iter::once(need.path.as_ref().expect("probed executable").to_string_lossy().into_owned()).chain(need.version_args.clone().unwrap_or_default()).collect::<Vec<_>>(),"state":outcome.state,"exit_code":outcome.exit_code,"timed_out":outcome.timed_out,"truncated":outcome.truncated,"cancelled":outcome.cancelled,"signal":outcome.signal,"executed":outcome.cleanup.group.is_some(),"cleanup":outcome.cleanup});
                } else if need.path.is_some() {
                    row["details"]["limitation"] = json!(
                        "project executables and custom commands are not started by doctor; their capability remains unmeasured"
                    );
                }
                rows.push(row);
            }
        }
        rows.sort_by_key(|row| {
            let name = row["name"].as_str().unwrap_or_default();
            (
                if self.legacy {
                    crate::legacy_toolchain::registry_rank(name)
                } else {
                    0
                },
                name.to_owned(),
                row["path"].as_str().unwrap_or_default().to_owned(),
            )
        });
        rows
    }
}
/// Frozen `collect_registered_capability` values of a legacy row: availability and completeness
/// from the version and metadata probes, the first version line, frozen error texts, and the
/// vendor/family/alias/module/metadata details beside native's probe evidence fields.
fn legacy_values(row: &mut Value, name: &str, need: &Need, _required_by: &BTreeSet<String>) {
    use crate::legacy_toolchain::row as frozen_row;
    let probe = legacy_probe;
    let native = row["details"].as_object().cloned().unwrap_or_default();
    let measured = match (&need.path, &need.outcome) {
        (Some(_), Some(outcome)) if !outcome.cancelled => {
            let detail = need
                .detail
                .zip(need.detail_outcome.as_ref())
                .map(|(kind, outcome)| (kind, probe(outcome)));
            Some(frozen_row(
                name,
                &need.alias,
                need.module.as_deref(),
                &probe(outcome),
                detail.as_ref().map(|(kind, probe)| (*kind, probe)),
            ))
        }
        _ => None,
    };
    match measured {
        Some(frozen) => {
            row["available"] = json!(frozen.available);
            row["complete"] = json!(frozen.complete);
            row["version"] = json!(frozen.version);
            row["error"] = json!(frozen.error);
            let mut details = frozen.details;
            details.extend(native);
            row["details"] = Value::Object(details);
        }
        None => {
            row["available"] = json!(false);
            row["complete"] = json!(false);
            row["version"] = json!("");
            // Frozen reports an unresolved candidate with an empty error; a Python-module tool
            // always had frozen's own interpreter, so native's interpreter failure keeps its text,
            // and an unregistered name keeps "no registered capability probe".
            if need.path.is_none()
                && crate::legacy_toolchain::lookup(name)
                    .is_some_and(|registered| !registered.module)
            {
                row["error"] = json!("");
            }
        }
    }
    if need.path.is_none() {
        row["path"] = json!("");
    }
}

/// A finished probe task as the frozen capability rules read it.
fn legacy_probe(outcome: &TaskOutcome) -> crate::legacy_toolchain::Probe<'_> {
    crate::legacy_toolchain::Probe {
        exit_code: outcome.exit_code,
        signal: outcome.signal,
        timed_out: outcome.timed_out,
        truncated: outcome.truncated,
        stdout: &outcome.stdout,
        stderr: &outcome.stderr,
    }
}

/// What a doctor version probe inherits from the caller: locating and running a tool, its
/// locale and terminal, and the build/toolchain variables that select it.
const PROBE_ENVIRONMENT: [&str; 48] = [
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "TZ",
    "TERM",
    "NO_COLOR",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "PYTHONPATH",
    "PYTHONNOUSERSITE",
    "PYTHONDONTWRITEBYTECODE",
    "LD_LIBRARY_PATH",
    "LIBRARY_PATH",
    "CPATH",
    "CPLUS_INCLUDE_PATH",
    "CC",
    "CXX",
    "AR",
    "CFLAGS",
    "CXXFLAGS",
    "LDFLAGS",
    "PKG_CONFIG_PATH",
    "QTDIR",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM",
    "CARGO_HOME",
    "RUSTUP_HOME",
    "RUSTUP_TOOLCHAIN",
    "RUSTUP_AUTO_INSTALL",
    "CARGO_NET_OFFLINE",
    "CARGO_BUILD_JOBS",
    "CARGO_TARGET_DIR",
    "RUSTFLAGS",
    "RUSTDOCFLAGS",
    "NODE_OPTIONS",
    "NODE_PATH",
    "NPM_CONFIG_USERCONFIG",
];
/// One bounded, offline version or metadata probe of a resolved executable.
fn probe_task(
    id: &str,
    path: &Path,
    args: &[String],
    root: &Path,
    digest: Option<&str>,
    timeout: f64,
    limit: usize,
) -> TaskSpec {
    let mut task = TaskSpec::command(
        id,
        "doctor-version",
        std::iter::once(path.to_string_lossy().into_owned())
            .chain(args.iter().cloned())
            .collect(),
        root.to_string_lossy(),
    );
    task.kind = TaskKind::Probe;
    task.timeout_seconds = Some(timeout);
    task.output_limit_bytes = Some(limit);
    task.tool_digest = digest.map(str::to_owned);
    // A version probe needs no variable of the caller's own: it inherits only these, so a tool
    // that prints its environment cannot put a private value into the doctor report (decisions.md
    // "도구 실행 환경"). Analysis tasks get the whole environment.
    task.env_inherit = Some(
        PROBE_ENVIRONMENT
            .iter()
            .map(|name| (*name).to_owned())
            .collect(),
    );
    for (name, value) in [
        ("PYTHONDONTWRITEBYTECODE", "1"),
        ("PYTHONPATH", ""),
        ("PYTHONNOUSERSITE", "1"),
        ("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1"),
        ("PYTEST_ADDOPTS", ""),
        ("PYTEST_PLUGINS", ""),
        ("RUSTUP_AUTO_INSTALL", "0"),
        ("CARGO_NET_OFFLINE", "true"),
        ("UV_OFFLINE", "1"),
        ("COREPACK_ENABLE_NETWORK", "0"),
        ("OMP_NUM_THREADS", "2"),
        ("RAYON_NUM_THREADS", "2"),
    ] {
        task.env_overlay.insert(name.into(), value.into());
    }
    task
}

fn run_probes(
    tasks: &[TaskSpec],
    environment: &BTreeMap<String, String>,
    cancellation: &Cancellation,
) -> Result<BTreeMap<String, TaskOutcome>> {
    let result = Scheduler::new(Runner::with_environment(environment.clone()), 2)
        .run(tasks, cancellation)
        .map_err(|error| CliError(error.to_string()))?;
    Ok(result
        .outcomes
        .into_iter()
        .map(|outcome| (outcome.task_id.clone(), outcome))
        .collect())
}

fn version_arguments(role: &str, path: &Path) -> Option<Vec<String>> {
    if matches!(
        role,
        "qtest"
            | "integration-artifact"
            | "mutation"
            | "sanitize"
            | "thread-sanitize"
            | "test-binary"
    ) {
        return None;
    }
    let known = |name: &str| {
        matches!(
            name,
            "ruff"
                | "ty"
                | "mypy"
                | "pytest"
                | "coverage"
                | "cargo"
                | "rustc"
                | "clippy"
                | "cargo-clippy"
                | "eslint"
                | "tsc"
                | "node"
                | "npm"
                | "gcc"
                | "g++"
                | "c++"
                | "cc"
                | "clang"
                | "clang++"
                | "clang-tidy"
                | "clang-format"
                | "clazy"
                | "clazy-standalone"
                | "cmake"
                | "ctest"
                | "make"
                | "ninja"
                | "gcov"
                | "readelf"
                | "addr2line"
                | "objdump"
                | "nm"
                | "ld"
                | "ar"
                | "strip"
                | "pkg-config"
                | "git"
                | "git-describe"
                | "uv"
                | "mutmut"
                | "cosmic-ray"
                | "mutpy"
        ) || name.strip_prefix("python").is_some_and(|suffix| {
            suffix.is_empty() || suffix.chars().all(|c| c.is_ascii_digit() || c == '.')
        }) || ["clang-", "clang++-", "clang-tidy-", "gcc-", "g++-", "gcov-"]
            .iter()
            .any(|prefix| {
                name.strip_prefix(prefix).is_some_and(|suffix| {
                    !suffix.is_empty() && suffix.chars().all(|c| c.is_ascii_digit() || c == '.')
                })
            })
    };
    let basename = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default();
    if matches!(role, "qmake" | "qmake6") || matches!(basename, "qmake" | "qmake6") {
        return Some(vec!["-v".into()]);
    }
    (known(role) || known(basename)).then(|| vec!["--version".into()])
}
fn version_text(outcome: &TaskOutcome) -> Option<String> {
    let bytes = if outcome
        .stdout
        .iter()
        .any(|byte| !byte.is_ascii_whitespace())
    {
        &outcome.stdout
    } else {
        &outcome.stderr
    };
    std::str::from_utf8(bytes)
        .ok()
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .map(|text| {
            text.lines()
                .take(3)
                .collect::<Vec<_>>()
                .join("\n")
                .chars()
                .take(2048)
                .collect()
        })
}
fn probe_complete(outcome: &TaskOutcome) -> bool {
    outcome.state == TaskState::Succeeded
        && outcome.exit_code == Some(0)
        && !outcome.cancelled
        && !outcome.timed_out
        && !outcome.truncated
        && outcome.signal.is_none()
        && outcome.cleanup.is_clean()
        && version_text(outcome).is_some()
}
fn probe_error(outcome: &TaskOutcome, seconds: f64) -> String {
    if outcome.cancelled {
        "version probe was cancelled".into()
    } else if outcome.timed_out {
        format!("version-only probe exceeded {seconds:.1} seconds")
    } else if outcome.truncated {
        format!("version-only probe exceeded the {PROBE_BYTES}-byte output bound")
    } else if outcome.exit_code == Some(0) && version_text(outcome).is_none() {
        "version probe returned no nonempty UTF-8 version evidence".into()
    } else {
        let detail = outcome
            .error
            .clone()
            .or_else(|| {
                String::from_utf8(outcome.stderr.clone())
                    .ok()
                    .map(|text| text.trim().chars().take(1024).collect::<String>())
                    .filter(|text| !text.is_empty())
            })
            .unwrap_or_default();
        format!(
            "version probe did not complete successfully (state {}, {}){suffix}",
            outcome.state_label(),
            outcome.ending(),
            suffix = if detail.is_empty() {
                String::new()
            } else {
                format!(": {detail}")
            }
        )
    }
}

pub fn command(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<CommandOutput> {
    let cancellation = Cancellation::default();
    let _signals = crate::events::SignalGuard::install(&cancellation)
        .map_err(|error| CliError(error.to_string()))?;
    let document = collect(invocation, cwd, environment, &cancellation)?;
    let mut output = if invocation.flag("json_mode") || invocation.flag("as_json") {
        CommandOutput::json(&document)?
    } else {
        CommandOutput::text(
            render(&document, invocation.flag("brief"))
                .chars()
                .filter(|ch| *ch == '\n' || *ch == '\t' || !ch.is_control())
                .collect::<String>(),
        )
    };
    if cancellation.is_cancelled() {
        output.exit_code = 130;
    }
    Ok(output)
}
/// Kept separate from rendering so bounded cancellation/probe evidence can be inspected directly.
pub fn collect(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    cancellation: &Cancellation,
) -> Result<Value> {
    let explicit = invocation.value("config_path").map(|path| under(cwd, path));
    let local = invocation
        .value("local_config")
        .or_else(|| invocation.value("local_path"))
        .map(|path| under(cwd, path));
    let inspection = crate::cutover::inspect(
        invocation,
        cwd,
        explicit.as_deref(),
        local.as_deref(),
        environment,
    )
    .map_err(|error| {
        // Frozen `next doctor` reads the workspace through `_workspace` (`config: `); the
        // historical `doctor` is refused earlier by the stable loader gate.
        crate::commands::classed(
            if invocation.command() == "next doctor" {
                crate::commands::CONFIG_PREFIX
            } else {
                crate::commands::STABLE_CONFIG_PREFIX
            },
            redact_text(&error.to_string()),
        )
    })?;
    let (prepared, scope_error) = match planning::prepare(invocation, cwd, environment, false) {
        Ok(prepared) => (Some(prepared), None),
        Err(error)
            if error
                .to_string()
                .contains("no supported source files discovered") =>
        {
            (None, Some(error.to_string()))
        }
        Err(error) => return Err(CliError(redact_text(&error.to_string()))),
    };
    let legacy_matrix = prepared
        .as_ref()
        .and_then(|prepared| legacy_support(invocation, prepared));
    let mut inventory = Inventory {
        legacy: legacy_matrix.is_some(),
        ..Inventory::default()
    };
    let required = inspection
        .effective
        .pointer("/doctor/required_tools")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect::<BTreeSet<_>>();
    match (&prepared, &legacy_matrix) {
        (_, Some(matrix)) => legacy_tools(
            matrix,
            &required.iter().copied().collect::<Vec<_>>(),
            &inspection.root,
            environment,
            cancellation,
            &mut inventory,
        )?,
        (Some(prepared), None) => gather(prepared, &mut inventory, environment),
        (None, None) => {}
    }
    if legacy_matrix.is_none() && required.len() > MAX_TOOLS {
        return Err(CliError(format!(
            "doctor.required_tools exceeds the {MAX_TOOLS}-tool diagnostic bound"
        )));
    }
    // The legacy policy already holds `doctor.config` (frozen `derive_tool_policy`).
    for name in required.iter().filter(|_| legacy_matrix.is_none()) {
        let explicit = inspection
            .tools
            .get(*name)
            .and_then(|tool| tool.path.as_deref())
            .map(|path| {
                if Path::new(path).is_absolute() {
                    PathBuf::from(path)
                } else {
                    inspection.root.join(path)
                }
            });
        let resolved = ToolRequest {
            id: (*name).into(),
            explicit,
            project_root: inspection.root.clone(),
            pack_root: if name.starts_with("python") {
                None
            } else {
                planning::tool_pack(environment)
            },
            path_env: Some(environment.get("PATH").cloned().unwrap_or_default()),
        }
        .resolve();
        match resolved {
            Ok(tool) => {
                let key = inventory.add(name, Some(&tool), None, "doctor.config", true, true);
                // An explicitly named required tool authorizes exactly its version query.
                inventory
                    .needs
                    .get_mut(&key)
                    .expect("inserted tool")
                    .version_args
                    .get_or_insert_with(|| vec!["--version".into()]);
            }
            Err(error) => {
                inventory.add(
                    name,
                    None,
                    Some(error.to_string()),
                    "doctor.config",
                    true,
                    true,
                );
            }
        }
    }
    inventory.execute(&inspection.root, environment, cancellation)?;
    let rows = inventory.rows();
    let missing = rows
        .iter()
        .filter(|row| row["required"] == true && row["available"] == false)
        .map(|row| row["name"].clone())
        .collect::<Vec<_>>();
    let incomplete = rows
        .iter()
        .filter(|row| {
            row["required"] == true && row["available"] == true && row["complete"] == false
        })
        .map(|row| row["name"].clone())
        .collect::<Vec<_>>();
    let ready = rows.iter().filter(|row| row["complete"] == true).count();
    let healthy = missing.is_empty() && incomplete.is_empty() && scope_error.is_none();
    let mut tools = serde_json::Map::new();
    for row in &rows {
        let name = row["name"].as_str().expect("tool name");
        let mut key = name.to_owned();
        let mut index = 2;
        while tools.contains_key(&key) {
            key = format!("{name}#{index}");
            index += 1;
        }
        tools.insert(key, row.clone());
    }
    // The historical doctor diagnoses frozen's engine/language support policy, not native check
    // rows (`ici next doctor` keeps those).
    let components = prepared
        .as_ref()
        .filter(|_| legacy_matrix.is_none())
        .map(|prepared| component_rows(prepared, &inventory, &rows))
        .unwrap_or_default();
    let blocked_checks = components
        .iter()
        .flat_map(|component| component["checks"].as_array().into_iter().flatten())
        .filter(|check| check["required"] == true && check["status"] == "blocked")
        .count();
    let healthy = healthy && blocked_checks == 0;
    let profile = prepared
        .as_ref()
        .map(|prepared| prepared.plan.profile)
        .unwrap_or(inspection.profile);
    let languages = prepared
        .as_ref()
        .map(|prepared| prepared.plan.languages.clone())
        .unwrap_or_default();
    let mut limitations = inspection.limitations.clone();
    if let Some(prepared) = &prepared {
        limitations.extend(prepared.plan.limitations.iter().cloned());
    }
    limitations.push("tool versions diagnose executable availability only; analysis, project imports, tests, builds and runtime compatibility remain NOT_RUN".into());
    limitations.push("the running product is Rust; historical core-Python candidate and NAS/shared-infrastructure discovery are superseded by the native runtime contract".into());
    let candidates = rows.iter().filter(|row|row["name"].as_str().is_some_and(|name|name.starts_with("python"))&&row["available"]==true).map(|row|json!({"candidate":row["name"],"path":row["path"],"version":row["version"],"complete":row["complete"]})).collect::<Vec<_>>();
    let support_matrix = legacy_matrix.unwrap_or_else(|| {
        json!({"project_languages":languages,"project_frameworks":[],"entries":support_rows(prepared.as_ref(), &components, &rows)})
    });
    let document = json!({"schema_id":"ici.next.doctor","schema_version":1,"root":inspection.root,"profile":profile,"components":components,"system":system(environment),"runtime":{"kind":"rust-native","version":crate::VERSION,"executable":std::env::current_exe().ok(),"target_os":std::env::consts::OS,"target_arch":std::env::consts::ARCH,"requires_project_python":false},"running_python":null,"python_candidates":candidates,"analysis_profile":profile,"required_tools":required,"effective_required_tools":rows.iter().filter(|row|row["required"]==true).filter_map(|row|row["name"].as_str().map(str::to_owned)).collect::<BTreeSet<_>>(),"tools":tools,"capability_inventory":{"schema_version":"ici.capabilities/v1","status":if healthy{"PASS"}else{"WARN"},"healthy":healthy,"counts":{"total":rows.len(),"ready":ready,"required":rows.iter().filter(|row|row["required"]==true).count(),"incomplete":rows.iter().filter(|row|row["available"]==true&&row["complete"]==false).count(),"unavailable":rows.iter().filter(|row|row["available"]==false).count(),"blocked_required_checks":blocked_checks,"version_probes":inventory.needs.values().filter(|need|need.outcome.as_ref().is_some_and(|outcome|outcome.cleanup.group.is_some())).count()},"missing_required":missing,"incomplete_required":incomplete,"tools":rows},"support_matrix":support_matrix,"paths":{"workspace":inspection.root,"configuration_files":inspection.configuration_files,"native_tool_pack":planning::tool_pack(environment)},"scope_error":scope_error,"scope_guidance":if scope_error.is_some(){json!({"config_key":"components","resolve":"declare valid component sources and dependencies; configuration inspection remains available","recheck":"ici next doctor"})}else{Value::Null},"executables":executables(environment),"limitations":limitations,"analysis_executed":false,"cancelled":cancellation.is_cancelled()});
    Ok(redact_value(&document))
}

/// Frozen `engines/verify.py` `prepare_analysis_context`: the historical suite report carries
/// the capability snapshot of the tools its support policy (`derive_tool_policy`) and
/// `doctor.required_tools` name, in frozen `serialize_capability_inventory` shape
/// (`ici.capabilities/v1`, `state` ready/incomplete/unavailable, frozen counts). Only those
/// policy tools are probed, by the same version probes as `ici doctor`; frozen probed its 32
/// registered tools whatever the policy.
pub fn suite_capability_inventory(
    support_matrix: &Value,
    effective: &Value,
    root: &Path,
    environment: &BTreeMap<String, String>,
    cancellation: &Cancellation,
) -> Result<Value> {
    let mut inventory = Inventory {
        legacy: true,
        ..Inventory::default()
    };
    let configured: Vec<&str> = effective
        .pointer("/doctor/required_tools")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    legacy_tools(
        support_matrix,
        &configured,
        root,
        environment,
        cancellation,
        &mut inventory,
    )?;
    inventory.execute(root, environment, cancellation)?;
    let tools: Vec<Value> = inventory
        .rows()
        .into_iter()
        .map(|mut row| {
            row["state"] = json!(if row["available"] != true {
                "unavailable"
            } else if row["complete"] != true {
                "incomplete"
            } else {
                "ready"
            });
            row
        })
        .collect();
    let state = |name: &str| tools.iter().filter(|tool| tool["state"] == name).count();
    let named = |filter: &dyn Fn(&Value) -> bool| {
        tools
            .iter()
            .filter(|tool| filter(tool))
            .map(|tool| tool["name"].clone())
            .collect::<Vec<_>>()
    };
    let missing = named(&|tool| tool["required"] == true && tool["available"] != true);
    let incomplete = named(&|tool| {
        tool["required"] == true && tool["available"] == true && tool["complete"] != true
    });
    let healthy = missing.is_empty() && incomplete.is_empty();
    Ok(redact_value(&json!({
        "schema_version": "ici.capabilities/v1",
        "status": if healthy { "PASS" } else { "WARN" },
        "healthy": healthy,
        "counts": {
            "total": tools.len(),
            "ready": state("ready"),
            "incomplete": state("incomplete"),
            "unavailable": state("unavailable"),
            "required": tools.iter().filter(|tool| tool["required"] == true).count(),
            "optional": tools.iter().filter(|tool| tool["optional"] == true).count(),
        },
        "missing_required": missing,
        "incomplete_required": incomplete,
        "tools": tools,
    })))
}

/// Frozen `ici doctor` (doctor.py `collect_diagnostics`) evaluated `core/support.py` over the
/// legacy policy (a legacy `ici.toml`, or the legacy defaults of a configless directory).
fn legacy_support(invocation: &Invocation, prepared: &PreparedPlan) -> Option<Value> {
    if invocation.command() != "doctor"
        || !matches!(
            prepared.project.schema_family,
            ici_config::SchemaFamily::Legacy | ici_config::SchemaFamily::Automatic
        )
    {
        return None;
    }
    let profile = crate::legacy_support::profile_name(prepared.plan.profile);
    let inputs = crate::legacy_support::inputs(prepared, &profile);
    Some(ici_reporting::legacy_support_matrix(&inputs, &[], None))
}

/// Frozen `derive_tool_policy` and `collect_capability_inventory` over the policy names: each
/// stripped `doctor.required_tools` name is required by `doctor.config`, applicable enabled rows
/// contribute `engine:language` provenance, and a required name absorbs optional use of it. A
/// registered name is probed once through frozen's candidates (a Python module through the
/// project interpreter); an unregistered one is "no registered capability probe".
fn legacy_tools(
    matrix: &Value,
    configured: &[&str],
    root: &Path,
    environment: &BTreeMap<String, String>,
    cancellation: &Cancellation,
    inventory: &mut Inventory,
) -> Result<()> {
    let mut required: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut optional: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for name in configured {
        let name =
            name.trim_matches(|ch: char| ch.is_whitespace() || ('\x1c'..='\x1f').contains(&ch));
        if !name.is_empty() {
            required
                .entry(name.to_owned())
                .or_default()
                .insert("doctor.config".into());
        }
    }
    for entry in matrix["entries"].as_array().into_iter().flatten() {
        if entry["applicable"] != true || entry["enabled"] != true {
            continue;
        }
        let source = format!(
            "{}:{}",
            entry["engine_name"].as_str().unwrap_or("-"),
            entry["language"].as_str().unwrap_or("-")
        );
        for (key, target) in [
            ("required_tools", &mut required),
            ("optional_tools", &mut optional),
        ] {
            for name in entry[key]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
            {
                let name = name
                    .trim_matches(|ch: char| ch.is_whitespace() || ('\x1c'..='\x1f').contains(&ch));
                if !name.is_empty() {
                    target
                        .entry(name.to_owned())
                        .or_default()
                        .insert(source.clone());
                }
            }
        }
    }
    for (name, sources) in &mut required {
        sources.extend(optional.remove(name).unwrap_or_default());
    }
    let policies = required
        .into_iter()
        .map(|entry| (entry, true))
        .chain(optional.into_iter().map(|entry| (entry, false)));
    let mut interpreter = None;
    for ((name, sources), is_required) in policies {
        let Some(registered) = crate::legacy_toolchain::lookup(&name) else {
            for source in &sources {
                inventory.add(
                    &name,
                    None,
                    Some("no registered capability probe".into()),
                    source,
                    is_required,
                    is_required,
                );
            }
            continue;
        };
        if registered.module {
            let interpreter = interpreter.get_or_insert_with(|| {
                crate::validation::legacy_module_interpreter(root, environment)
            });
            for source in &sources {
                match interpreter {
                    Ok(tool) => {
                        inventory.add_module(&name, tool, source, is_required);
                    }
                    Err(error) => {
                        inventory.add(
                            &name,
                            None,
                            Some(format!("{name}: {error}")),
                            source,
                            is_required,
                            is_required,
                        );
                    }
                }
            }
            continue;
        }
        let resolved = policy_tool(&name, registered, root, environment, cancellation)?;
        for source in &sources {
            match &resolved {
                Ok(tool) => {
                    inventory.add(&name, Some(tool), None, source, is_required, is_required);
                }
                Err(error) => {
                    inventory.add(
                        &name,
                        None,
                        Some(error.to_string()),
                        source,
                        is_required,
                        is_required,
                    );
                }
            }
        }
    }
    Ok(())
}

/// Frozen `_probe_registered_candidates`: the distinct (by real path) resolved candidates in
/// priority order; when more than one resolves, the first whose frozen version probe is
/// available and complete is selected, else the first resolved one (a `clazy-standalone` that
/// cannot load its plugin yields to `clazy`). Candidates resolve as the native engines that
/// run them do (project `.venv`, the verified tool pack, then PATH).
fn policy_tool(
    name: &str,
    registered: crate::legacy_toolchain::Registered,
    root: &Path,
    environment: &BTreeMap<String, String>,
    cancellation: &Cancellation,
) -> Result<std::result::Result<ResolvedTool, ici_providers::resolver::ResolutionError>> {
    let mut resolved: Vec<ResolvedTool> = vec![];
    let mut seen = BTreeSet::new();
    let mut last_error = None;
    for id in registered.candidates {
        match (ToolRequest {
            id: (*id).into(),
            explicit: None,
            project_root: root.to_path_buf(),
            pack_root: if id.starts_with("python") {
                None
            } else {
                planning::tool_pack(environment)
            },
            path_env: Some(environment.get("PATH").cloned().unwrap_or_default()),
        })
        .resolve()
        {
            Ok(tool) => {
                let real = std::fs::canonicalize(&tool.path).unwrap_or_else(|_| tool.path.clone());
                if seen.insert(real) {
                    resolved.push(tool);
                }
            }
            Err(error) => last_error = Some(error),
        }
    }
    if resolved.len() < 2 {
        return Ok(resolved
            .pop()
            .ok_or_else(|| last_error.expect("one candidate")));
    }
    let mut probes = Inventory {
        legacy: true,
        ..Inventory::default()
    };
    let keys: Vec<String> = resolved
        .iter()
        .map(|tool| {
            let key = probes.add(name, Some(tool), None, "candidate", true, true);
            // Candidate choice reads only the version probe.
            probes.needs.get_mut(&key).expect("candidate need").detail = None;
            key
        })
        .collect();
    probes.execute(root, environment, cancellation)?;
    let selected = keys
        .iter()
        .position(|key| {
            let need = &probes.needs[key];
            need.outcome.as_ref().is_some_and(|outcome| {
                let row = crate::legacy_toolchain::row(
                    name,
                    &need.alias,
                    None,
                    &legacy_probe(outcome),
                    None,
                );
                !outcome.cancelled && row.available && row.complete
            })
        })
        .unwrap_or(0);
    Ok(Ok(resolved.swap_remove(selected)))
}

fn gather(
    prepared: &PreparedPlan,
    inventory: &mut Inventory,
    environment: &BTreeMap<String, String>,
) {
    let mut known = prepared
        .tools
        .values()
        .chain(prepared.cpp.tools.values())
        .cloned()
        .collect::<Vec<_>>();
    for preparation in &prepared.context_preparations {
        known.extend(preparation.tools.values().cloned());
    }
    for plans in prepared.validations.values() {
        for plan in plans {
            known.push(plan.context.tool.clone());
            known.extend(plan.context.options.auxiliary_tools.values().cloned());
            known.extend(plan.context.options.mutation_tool.iter().cloned());
            known.extend(
                plan.context
                    .options
                    .runtimes
                    .iter()
                    .filter_map(|runtime| runtime.tool.clone()),
            );
        }
    }
    for check in &prepared.plan.checks {
        let component = prepared
            .project
            .components
            .iter()
            .find(|component| component.id == check.component_id)
            .expect("selected component");
        let optional = prepared
            .validations
            .get(&check.task_id)
            .into_iter()
            .flatten()
            .flat_map(|plan| plan.optional_task_ids.iter().cloned())
            .collect::<BTreeSet<_>>();
        let required = check.check.required && !check.advisory();
        if let Some(plans) = prepared.validations.get(&check.task_id) {
            for runtime in plans.iter().flat_map(|plan| &plan.context.options.runtimes) {
                inventory.add(
                    &format!("python:{}", runtime.configured),
                    runtime.tool.as_ref(),
                    runtime.tool.is_none().then(|| {
                        format!(
                            "configured project runtime {:?} is unavailable",
                            runtime.configured
                        )
                    }),
                    &check.task_id,
                    required && runtime.required,
                    runtime.required,
                );
            }
        }
        let tasks = prepared
            .probes
            .get(&check.task_id)
            .into_iter()
            .chain(check.tasks());
        for task in tasks {
            let (Some(path), Some(digest)) = (task.argv.first(), &task.tool_digest) else {
                continue;
            };
            let key = format!("{path}\0{digest}");
            if inventory
                .bindings
                .get(&check.task_id)
                .is_some_and(|keys| keys.contains(&key))
            {
                continue;
            }
            let selected = known
                .iter()
                .find(|tool| tool.path == Path::new(path) && tool.digest == *digest);
            let tool = selected.cloned().unwrap_or_else(|| ResolvedTool {
                id: task.provider.clone(),
                path: PathBuf::from(path),
                source: ici_providers::resolver::ToolSource::Explicit,
                digest: digest.clone(),
                version: None,
                configuration: vec![],
            });
            let name = if selected.is_some() {
                tool.id.clone()
            } else if tool.id == "git-describe" {
                "git".into()
            } else {
                tool.path
                    .file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or(&tool.id)
                    .to_owned()
            };
            let optional = optional.contains(&task.id);
            let key = inventory.add(
                &name,
                Some(&tool),
                None,
                &check.task_id,
                required && !optional,
                !optional,
            );
            if selected.is_none() {
                inventory.needs.get_mut(&key).expect("recorded tool").origin =
                    "recorded-task".into();
            }
            if prepared.cpp.tools.iter().any(|(role, compiler)| {
                role.starts_with("compiler:")
                    && compiler.path == tool.path
                    && compiler.digest == tool.digest
            }) {
                inventory
                    .needs
                    .get_mut(&key)
                    .expect("recorded compiler")
                    .version_args = Some(vec!["--version".into()]);
            }
        }
        // A compiler can resolve before replay rejects a driver/context. Keep that measured
        // identity visible without running the rejected replay command.
        if check.blocked().is_some() && check.check.language == "cpp" {
            let compiler_keys = prepared
                .cpp
                .contexts
                .get(&component.id)
                .into_iter()
                .flat_map(|context| &context.units)
                .map(|unit| {
                    format!(
                        "compiler:{}:{}",
                        unit.directory,
                        unit.argv.first().unwrap_or(&unit.compiler)
                    )
                })
                .collect::<BTreeSet<_>>();
            for (_, tool) in prepared
                .cpp
                .tools
                .iter()
                .filter(|(role, _)| compiler_keys.contains(*role))
            {
                let key = inventory.add(&tool.id, Some(tool), None, &check.task_id, required, true);
                inventory
                    .needs
                    .get_mut(&key)
                    .expect("resolved context compiler")
                    .version_args = Some(vec!["--version".into()]);
            }
        }
        // A failed resolver supplies no task. Preserve its missing capability in the inventory.
        if check.tasks().is_empty()
            && component
                .files
                .iter()
                .any(|path| file_language(path) == Some(check.check.language.as_str()))
        {
            if check.check.id == "python.compat-runtime" {
                let runtimes = component
                    .settings
                    .pointer("/engines/python_compat/interpreters")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .collect::<Vec<_>>();
                let required_runtimes = component
                    .settings
                    .pointer("/engines/python_compat/required_interpreters")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .collect::<BTreeSet<_>>();
                if !runtimes.is_empty() {
                    for runtime in runtimes {
                        let tool = resolve(prepared, component, runtime, true, environment);
                        let is_required = required_runtimes.contains(runtime);
                        match tool {
                            Ok(tool) => {
                                inventory.add(
                                    &format!("python:{runtime}"),
                                    Some(&tool),
                                    None,
                                    &check.task_id,
                                    required && is_required,
                                    is_required,
                                );
                            }
                            Err(reason) => {
                                inventory.add(
                                    &format!("python:{runtime}"),
                                    None,
                                    Some(reason),
                                    &check.task_id,
                                    required && is_required,
                                    is_required,
                                );
                            }
                        }
                    }
                    continue;
                }
            }
            let wanted = match check.check.id.as_str() {
                "python.lint" | "python.format" => Some("ruff"),
                "python.type" => Some(component.python_type_provider.as_deref().unwrap_or("ty")),
                "python.test" | "python.coverage" | "python.compat-runtime" | "python.sanitize" => {
                    Some("python")
                }
                _ => check.check.tool.as_deref(),
            };
            if let Some(name) = wanted {
                let configured = if name == "python" {
                    component.python_executable.as_deref().unwrap_or("python")
                } else {
                    name
                };
                match resolve(
                    prepared,
                    component,
                    configured,
                    name == "python",
                    environment,
                ) {
                    Ok(tool) => {
                        inventory.add(name, Some(&tool), None, &check.task_id, required, true);
                    }
                    Err(reason) => {
                        inventory.add(name, None, Some(reason), &check.task_id, required, true);
                    }
                }
            }
        }
    }
}
fn resolve(
    prepared: &PreparedPlan,
    component: &Component,
    name: &str,
    python: bool,
    environment: &BTreeMap<String, String>,
) -> std::result::Result<ResolvedTool, String> {
    let explicit = if name.contains('/') {
        Some(if Path::new(name).is_absolute() {
            PathBuf::from(name)
        } else {
            prepared.project.root.join(name)
        })
    } else {
        prepared
            .project
            .tools
            .get(name)
            .and_then(|tool| tool.path.as_deref())
            .map(|path| {
                if Path::new(path).is_absolute() {
                    PathBuf::from(path)
                } else {
                    prepared.project.root.join(path)
                }
            })
    };
    // Use the same authority as planning; a missing PATH does not search the host implicitly.
    ToolRequest {
        id: if name.contains('/') {
            "python".into()
        } else {
            name.into()
        },
        explicit,
        project_root: prepared.project.root.join(&component.root),
        pack_root: if python {
            None
        } else {
            planning::tool_pack(environment)
        },
        path_env: Some(environment.get("PATH").cloned().unwrap_or_default()),
    }
    .resolve()
    .map_err(|error| {
        if python {
            format!("project Python {name:?}: {error}")
        } else {
            error.to_string()
        }
    })
}
fn guidance(check: &str, component: &str) -> Vec<String> {
    match check {
        "python.test" | "python.coverage" | "python.sanitize" => vec![
            format!("components.{component}.python.executable"),
            "engines.test.python".into(),
        ],
        "python.compat-runtime" => vec![
            "engines.python_compat.interpreters".into(),
            "engines.python_compat.required_interpreters".into(),
            format!("components.{component}.python.executable"),
        ],
        "python.lint" | "python.format" => vec!["tools.ruff.path".into()],
        "python.type" => vec![
            format!("components.{component}.python.type_provider"),
            "tools.ty.path".into(),
            "tools.mypy.path".into(),
        ],
        check if check.starts_with("cpp.") => vec![
            "project.compile_database".into(),
            format!("components.{component}.build"),
            "tools".into(),
        ],
        "python.artifact" => vec![
            "build.python.entrypoint".into(),
            "build.artifacts".into(),
            "pyproject.toml:project.version".into(),
            "tools.git.path".into(),
        ],
        "integration" => vec![
            format!("components.{component}.integrations"),
            "engines.integration.cases".into(),
        ],
        _ => vec![
            format!("components.{component}.sources"),
            format!("checks.{check}.enabled"),
        ],
    }
}
fn component_rows(prepared: &PreparedPlan, inventory: &Inventory, tools: &[Value]) -> Vec<Value> {
    let mut result = vec![];
    for component in prepared
        .project
        .components
        .iter()
        .filter(|component| prepared.plan.components.contains(&component.id))
    {
        let mut checks = vec![];
        for planned in prepared
            .plan
            .checks
            .iter()
            .filter(|check| check.component_id == component.id)
        {
            let mut reason = prepared
                .graph
                .blocked
                .get(&planned.task_id)
                .cloned()
                .or_else(|| planned.blocked().map(str::to_owned));
            let problems = inventory.problems(&planned.task_id);
            if !problems.is_empty() {
                reason = Some(match reason {
                    Some(reason) => format!("{reason}; {}", problems.join("; ")),
                    None => problems.join("; "),
                });
            }
            let bound = inventory.bindings.get(&planned.task_id);
            let tool_rows = tools
                .iter()
                .filter(|row| {
                    row["required_by"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .chain(row["optional_by"].as_array().into_iter().flatten())
                        .any(|owner| owner == &planned.task_id)
                })
                .cloned()
                .collect::<Vec<_>>();
            let mut row = json!({"check":planned.check.id,"inputs":component.files.iter().filter(|path|file_language(path)==Some(planned.check.language.as_str())).count(),"status":if reason.is_some(){"blocked"}else{"selected"},"required":planned.check.required,"evidence":"NOT_RUN","affected_scope":[planned.task_id],"config_keys":guidance(&planned.check.id,&component.id),"tools":tool_rows,"recheck":format!("ici next doctor --component {}",component.id)});
            if let Some(reason) = reason {
                row["reason"] = json!(reason);
                row["resolve"] = json!(format!(
                    "review {} and prepare the declared project/runtime tools; doctor does not install or build",
                    guidance(&planned.check.id, &component.id).join(", ")
                ));
            } else if bound.is_none_or(BTreeSet::is_empty) && matches!(planned.work, Work::Internal)
            {
                row["tool"] = json!("ici");
                row["version"] = json!(crate::VERSION);
                row["origin"] = json!("native-core");
            } else if let Some(first) = row["tools"]
                .as_array()
                .and_then(|rows| {
                    rows.iter()
                        .find(|row| row["available"] == true)
                        .or_else(|| rows.first())
                })
                .cloned()
            {
                row["tool"] = first["path"].clone();
                row["version"] = first["version"].clone();
                row["origin"] = first["origin"].clone();
            }
            if matches!(planned.work, Work::NativePrepare { .. }) {
                row["execution"] = json!("native preparation is declared; doctor does not run it");
            }
            checks.push(row);
        }
        for omitted in prepared
            .plan
            .omitted
            .iter()
            .filter(|check| check.component_id == component.id)
        {
            checks.push(json!({"check":omitted.check_id,"inputs":component.files.len(),"status":"omitted","reason":omitted.reason,"evidence":"NOT_RUN","affected_scope":[format!("{}.{}",component.id,omitted.check_id)],"config_keys":guidance(&omitted.check_id,&component.id),"recheck":format!("ici next doctor --component {}",component.id)}));
        }
        checks.sort_by_key(|row| row["check"].as_str().unwrap_or_default().to_owned());
        let selected_languages = component
            .languages
            .iter()
            .filter(|language| prepared.plan.languages.contains(language))
            .collect::<Vec<_>>();
        let mut row = json!({"id":component.id,"languages":selected_languages,"checks":checks});
        if component.languages.iter().any(|language| language == "cpp") {
            let context = prepared.cpp.contexts.get(&component.id);
            // Frozen `_doctor_compile`: without a database nothing is covered, every expected
            // translation unit is missing and the origin is null.
            let loaded = context;
            let context = context.filter(|context| context.database_path.is_some());
            let covered = context
                .map(|context| {
                    context
                        .selected_sources
                        .iter()
                        .filter(|source| !context.uncovered_sources.contains(source))
                        .cloned()
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            let missing = context
                .map(|context| context.uncovered_sources.clone())
                .unwrap_or_else(|| {
                    component
                        .files
                        .iter()
                        .filter(|path| {
                            file_language(path) == Some("cpp") && ici_cpp::is_translation_unit(path)
                        })
                        .cloned()
                        .collect()
                });
            let prepare=prepared.context_preparations.iter().filter(|plan|plan.component_id==component.id).map(|plan|json!({"build":plan.build_id,"variant":plan.build.variant,"descriptor":plan.descriptor,"steps":plan.build.tasks.iter().map(|task|&task.id).collect::<Vec<_>>(),"executed":false})).collect::<Vec<_>>();
            // Frozen next `_doctor_compile`: what the linked build's project file reaches.
            let targets = match crate::cpp::next_build_reach(&prepared.project, component) {
                Some((targets, _)) => targets.iter().map(|target| json!({"name":target.name,"project":target.project,"depends":target.depends})).collect::<Vec<_>>(),
                None => prepared.project.builds.iter().filter(|build|component.build.as_deref()==Some(&build.id)).map(|build|json!({"name":build.id,"project":build.project,"depends":[],"evidence":"declared-build"})).collect::<Vec<_>>(),
            };
            row["compile_inputs"] = json!({"database":context.and_then(|context|context.database_path.as_ref()),"origin":context.map(|context|&context.origin),"expected":context.map(|context|context.selected_sources.len()).unwrap_or(missing.len()),"covered":covered,"missing":missing,"generated":prepared.context_preparations.iter().filter(|plan|plan.component_id==component.id).flat_map(|plan|plan.build.generated_roots.iter()).collect::<BTreeSet<_>>(),"targets":targets,"prepare":prepare,"diagnostics":loaded.map(|context|&context.diagnostics),"evidence":"recorded-inputs-only","generated_complete":false,"limitations":["doctor does not generate missing inputs or execute compiler dependency probes"]});
        }
        result.push(row);
    }
    result
}
fn support_rows(
    prepared: Option<&PreparedPlan>,
    components: &[Value],
    tools: &[Value],
) -> Vec<Value> {
    let Some(prepared) = prepared else {
        return vec![];
    };
    let mut result = vec![];
    for component in prepared
        .project
        .components
        .iter()
        .filter(|component| prepared.plan.components.contains(&component.id))
    {
        let check_rows = components
            .iter()
            .find(|row| row["id"] == component.id)
            .and_then(|row| row["checks"].as_array())
            .cloned()
            .unwrap_or_default();
        let mut families = BTreeMap::<(String, String), Vec<&Value>>::new();
        for row in &check_rows {
            let id = row["check"].as_str().unwrap_or_default();
            let language = prepared
                .plan
                .checks
                .iter()
                .find(|check| check.component_id == component.id && check.check.id == id)
                .map(|check| check.check.language.clone())
                .unwrap_or_else(|| id.split('.').next().unwrap_or("integration").to_owned());
            families
                .entry((engine_name(id).unwrap_or(id).into(), language))
                .or_default()
                .push(row);
        }
        for descriptor in ici_domain::builtin_catalog().legacy {
            for language in ["python", "cpp"] {
                families
                    .entry((descriptor.name.clone(), language.into()))
                    .or_default();
            }
        }
        for ((engine, language), rows) in families {
            let applicable = component.languages.contains(&language)
                && prepared.plan.languages.contains(&language)
                || language == "integration" && rows.iter().any(|row| row["status"] != "omitted");
            let enabled = rows.iter().any(|row| row["status"] != "omitted");
            let blocked = rows.iter().filter(|row| row["status"] == "blocked").count();
            let owners = rows
                .iter()
                .filter_map(|row| {
                    row["affected_scope"]
                        .as_array()
                        .and_then(|rows| rows.first())
                        .and_then(Value::as_str)
                })
                .collect::<BTreeSet<_>>();
            let required = tools
                .iter()
                .filter(|tool| {
                    tool["required_by"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_str)
                        .any(|owner| owners.contains(owner))
                })
                .filter_map(|tool| tool["name"].as_str())
                .collect::<BTreeSet<_>>();
            let optional = tools
                .iter()
                .filter(|tool| {
                    tool["optional_by"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_str)
                        .any(|owner| owners.contains(owner))
                })
                .filter_map(|tool| tool["name"].as_str())
                .filter(|name| !required.contains(name))
                .collect::<BTreeSet<_>>();
            let mode = component
                .settings
                .pointer(&format!("/engines/{engine}/mode"))
                .cloned()
                .unwrap_or_else(|| json!("native"));
            result.push(json!({"engine_name":engine,"language":language,"component_id":component.id,"frameworks":[],"applicable":applicable,"enabled":enabled,"mode":mode,"active_mode":null,"evidence":"NOT_RUN","confidence":"low","required_tools":required,"optional_tools":optional,"fallback_mode":null,"reason":if !applicable{"not applicable to selected language scope"}else if !enabled{"disabled or omitted by the selected profile/policy"}else if blocked>0{"selected scope has blocked prerequisites; analysis has not run"}else{"applicable engine has not been executed; version probes do not establish analysis support"},"limitations":["capabilities are declarations and version-only observations; actual rules, builds and project runtime compatibility are not executed by doctor"]}));
        }
    }
    result
}
fn bounded_text(path: &Path) -> Option<String> {
    String::from_utf8(ici_execution::storage::read_bounded_regular(path, 65536).ok()?).ok()
}
fn system(environment: &BTreeMap<String, String>) -> Value {
    let release = bounded_text(Path::new("/etc/os-release")).unwrap_or_default();
    let field = |key: &str| {
        release
            .lines()
            .find_map(|line| line.strip_prefix(&format!("{key}=")))
            .map(|value| value.trim_matches('"').to_owned())
    };
    let kernel =
        bounded_text(Path::new("/proc/sys/kernel/osrelease")).map(|value| value.trim().to_owned());
    json!({"os_id":field("ID").unwrap_or_else(||std::env::consts::OS.into()),"os_version":field("VERSION_ID"),"pretty_name":field("PRETTY_NAME").unwrap_or_else(||std::env::consts::OS.into()),"kernel":kernel,"arch":std::env::consts::ARCH,"glibc":null,"glibc_evidence":"not probed; inspect the native archive target/libc contract","is_wsl":environment.get("WSL_DISTRO_NAME").is_some_and(|value|!value.is_empty())||kernel.as_ref().is_some_and(|kernel|kernel.to_ascii_lowercase().contains("microsoft")),"shell":environment.get("SHELL"),"term":environment.get("TERM"),"lang":environment.get("LANG")})
}
/// Every `ici` on PATH, in lookup order, and the running native executable. A different file
/// found first shadows this product (for example a pyz or `.venv/bin/ici`); a later one is a
/// leftover installation. `ICI_PYTHON` is reported as the historical pyz variable, which only
/// legacy-schema commands still read as a project-interpreter candidate.
fn executables(environment: &BTreeMap<String, String>) -> Value {
    use std::os::unix::fs::PermissionsExt;
    let running = std::env::current_exe().ok();
    let canonical = running.as_ref().and_then(|path| path.canonicalize().ok());
    let mut found: Vec<PathBuf> = vec![];
    for directory in std::env::split_paths(environment.get("PATH").map_or("", String::as_str)) {
        if directory.as_os_str().is_empty() || !directory.is_absolute() {
            continue;
        }
        let candidate = directory.join("ici");
        if candidate.is_file()
            && candidate
                .metadata()
                .is_ok_and(|metadata| metadata.permissions().mode() & 0o111 != 0)
            && !found.contains(&candidate)
        {
            found.push(candidate);
        }
    }
    let is_running = |path: &PathBuf| canonical.is_some() && path.canonicalize().ok() == canonical;
    let conflicts: Vec<&PathBuf> = found.iter().filter(|path| !is_running(path)).collect();
    let first = found.first();
    let shadowed = first.is_some_and(|path| !is_running(path));
    let mut guidance = vec![];
    if shadowed {
        guidance.push("`ici` on PATH resolves to a different executable; put the native installation's bin directory first, then verify `ici --version`".to_owned());
    }
    if !conflicts.is_empty() {
        guidance.push("update tasks and scripts that store an absolute path such as .venv/bin/ici; remove a uv/pip/pyz installation only when it is no longer needed".to_owned());
    }
    if environment.contains_key("ICI_PYTHON") {
        guidance.push(
            "ICI_PYTHON is the historical pyz interpreter variable; the native core never runs on it, and only legacy-schema commands still read it as the frozen launcher's project-interpreter candidate"
                .to_owned(),
        );
    }
    json!({"running":running,"path_ici":found,"first_on_path":first,"shadowed":shadowed,"conflicts":conflicts,"legacy_ici_python":environment.get("ICI_PYTHON"),"guidance":guidance})
}
fn render(document: &Value, brief: bool) -> String {
    let mut text = format!(
        "ici {} {}\nruntime Rust native; project Python is an external tool\nroot    {}\nscope   languages={}  profile={}\n",
        crate::VERSION,
        if brief {
            "brief"
        } else {
            "Environment Diagnostics"
        },
        document["root"].as_str().unwrap_or("-"),
        document["support_matrix"]["project_languages"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .collect::<Vec<_>>()
            .join(", "),
        document["profile"].as_str().unwrap_or("-")
    );
    let counts = &document["capability_inventory"]["counts"];
    text.push_str(&format!(
        "caps    status={}  ready={}/{}  required={}\n",
        document["capability_inventory"]["status"]
            .as_str()
            .unwrap_or("-"),
        counts["ready"],
        counts["total"],
        counts["required"]
    ));
    let executables = &document["executables"];
    for conflict in executables["conflicts"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        let shadows = executables["shadowed"] == true
            && executables["first_on_path"].as_str() == Some(conflict);
        text.push_str(&format!(
            "path    {} ici: {conflict}\n",
            if shadows { "shadowing" } else { "other" }
        ));
    }
    if let Some(value) = executables["legacy_ici_python"].as_str() {
        text.push_str(&format!("legacy  ICI_PYTHON={value}\n"));
    }
    for line in executables["guidance"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        text.push_str(&format!("  resolve: {line}\n"));
    }
    if let Some(error) = document["scope_error"].as_str() {
        text.push_str(&format!("scope   blocked: {error}\n  resolve: declare valid component sources and configuration\n  recheck: ici next doctor\n"));
    }
    for component in document["components"].as_array().into_iter().flatten() {
        if !brief {
            text.push_str(&format!("{}:\n", component["id"].as_str().unwrap_or("-")));
        }
        for check in component["checks"].as_array().into_iter().flatten() {
            if brief && check["status"] != "blocked" {
                continue;
            }
            text.push_str(&format!(
                "  {}: {}",
                check["check"].as_str().unwrap_or("-"),
                check["status"].as_str().unwrap_or("-")
            ));
            if let Some(reason) = check["reason"].as_str() {
                text.push_str(&format!(" — {reason}"));
            } else if let Some(tool) = check["tool"].as_str() {
                text.push_str(&format!(" — {tool}"));
            }
            text.push('\n');
            if let Some(resolve) = check["resolve"].as_str() {
                text.push_str(&format!(
                    "    resolve: {resolve}\n    recheck: {}\n",
                    check["recheck"].as_str().unwrap_or("ici next doctor")
                ));
            }
        }
        if let Some(inputs) = component.get("compile_inputs") {
            text.push_str(&format!(
                "  compile db: {} — {}/{} TU(s) covered; preparation not run\n",
                inputs["database"].as_str().unwrap_or("none"),
                inputs["covered"].as_array().map_or(0, Vec::len),
                inputs["expected"]
            ));
        }
        // Static preparation refusals are plan-time facts; nothing was built.
        let prefix = format!("{} release: ", component["id"].as_str().unwrap_or("-"));
        for limitation in document["limitations"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .filter(|limitation| {
                limitation.starts_with(&prefix)
                    && limitation.contains("declared release build cannot be prepared")
            })
        {
            text.push_str(&format!(
                "  prepare: blocked — {}\n    resolve: fix the build declaration named above; doctor does not build\n",
                &limitation[prefix.len()..]
            ));
        }
    }
    // The historical doctor lists no native component rows; its static preparation refusals
    // are still plan-time facts and keep their component name.
    if document["components"]
        .as_array()
        .is_none_or(|components| components.is_empty())
    {
        for limitation in document["limitations"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .filter(|limitation| limitation.contains("declared release build cannot be prepared"))
        {
            text.push_str(&format!(
                "prepare: blocked — {limitation}\n  resolve: fix the build declaration named above; doctor does not build\n"
            ));
        }
    }
    text.push_str(if brief {
        "tools\n"
    } else {
        "Tool Capability Inventory\n"
    });
    for tool in document["capability_inventory"]["tools"]
        .as_array()
        .into_iter()
        .flatten()
    {
        let state = if tool["complete"] == true {
            "Ready (version only)"
        } else if tool["available"] == true {
            "Incomplete"
        } else {
            "Unavailable"
        };
        text.push_str(&format!(
            "  {}: {state}{} — {}\n",
            tool["name"].as_str().unwrap_or("-"),
            if tool["required"] == true {
                " (required)"
            } else {
                ""
            },
            tool["version"]
                .as_str()
                .or_else(|| tool["error"].as_str())
                .unwrap_or("not probed")
        ));
    }
    if !brief {
        text.push_str(
            "Engine Capability Matrix: NOT_RUN; version-only diagnostics do not execute analysis\n",
        );
        for entry in document["support_matrix"]["entries"]
            .as_array()
            .into_iter()
            .flatten()
        {
            text.push_str(&format!(
                "  {}:{} {} — {}\n",
                entry["engine_name"].as_str().unwrap_or("-"),
                entry["language"].as_str().unwrap_or("-"),
                if entry["applicable"] != true {
                    "not-applicable"
                } else if entry["enabled"] != true {
                    "disabled"
                } else {
                    "applicable"
                },
                entry["reason"].as_str().unwrap_or("-")
            ));
        }
    }
    text
}
