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
    probe_id: Option<String>,
    outcome: Option<TaskOutcome>,
}
#[derive(Default)]
struct Inventory {
    needs: BTreeMap<String, Need>,
    bindings: BTreeMap<String, BTreeSet<String>>,
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
        let key = match tool {
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
            version_args: tool.and_then(|tool| version_arguments(&tool.id, &tool.path)),
            probe_id: None,
            outcome: None,
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
    fn execute(
        &mut self,
        root: &Path,
        environment: &BTreeMap<String, String>,
        cancellation: &Cancellation,
    ) -> Result<()> {
        if self.needs.len() > MAX_TOOLS {
            return Err(CliError(format!(
                "doctor inventory exceeds {MAX_TOOLS} distinct executables; narrow the selected scope or doctor.required_tools"
            )));
        }
        let mut tasks = vec![];
        for (index, need) in self.needs.values_mut().enumerate() {
            let (Some(path), Some(args)) = (&need.path, &need.version_args) else {
                continue;
            };
            let id = format!("doctor.version.{index}");
            let mut task = TaskSpec::command(
                &id,
                "doctor-version",
                std::iter::once(path.to_string_lossy().into_owned())
                    .chain(args.iter().cloned())
                    .collect(),
                root.to_string_lossy(),
            );
            task.kind = TaskKind::Probe;
            task.timeout_seconds = Some(PROBE_TIMEOUT);
            task.output_limit_bytes = Some(PROBE_BYTES);
            task.tool_digest.clone_from(&need.digest);
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
            need.probe_id = Some(id);
            tasks.push(task);
        }
        let result = Scheduler::new(Runner::with_environment(environment.clone()), 2)
            .run(&tasks, cancellation)
            .map_err(|error| CliError(error.to_string()))?;
        let outcomes: BTreeMap<_, _> = result
            .outcomes
            .into_iter()
            .map(|outcome| (outcome.task_id.clone(), outcome))
            .collect();
        for need in self.needs.values_mut() {
            need.outcome = need
                .probe_id
                .as_ref()
                .and_then(|id| outcomes.get(id))
                .cloned();
        }
        Ok(())
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
                    .map(probe_error)
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
                    .map(probe_error)
            });
            for (name, policy) in &need.aliases {
                let mut row = json!({"name":name,"available":need.path.is_some(),"complete":complete,"path":need.path,"version":version,"origin":need.origin,"sha256":need.digest,"required":!policy.required_by.is_empty(),"optional":policy.required_by.is_empty()&&!policy.optional_by.is_empty(),"required_by":policy.required_by,"optional_by":policy.optional_by,"error":error,"details":{"evidence":if complete {"version-only"} else {"NOT_RUN"},"analysis_executed":false}});
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
            (
                row["name"].as_str().unwrap_or_default().to_owned(),
                row["path"].as_str().unwrap_or_default().to_owned(),
            )
        });
        rows
    }
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
fn probe_error(outcome: &TaskOutcome) -> String {
    if outcome.cancelled {
        "version probe was cancelled".into()
    } else if outcome.timed_out {
        format!("version-only probe exceeded {PROBE_TIMEOUT:.1} seconds")
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
            "version probe did not complete successfully (state {:?}, exit {:?}, signal {:?}){suffix}",
            outcome.state,
            outcome.exit_code,
            outcome.signal,
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
    let inspection = ici_config::inspect_configuration_with_environment(
        cwd,
        explicit.as_deref(),
        local.as_deref(),
        environment,
    )
    .map_err(|error| CliError(redact_text(&error.to_string())))?;
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
    let mut inventory = Inventory::default();
    if let Some(prepared) = &prepared {
        gather(prepared, &mut inventory, environment);
    }
    let required = inspection
        .effective
        .pointer("/doctor/required_tools")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect::<BTreeSet<_>>();
    if required.len() > MAX_TOOLS {
        return Err(CliError(format!(
            "doctor.required_tools exceeds the {MAX_TOOLS}-tool diagnostic bound"
        )));
    }
    for name in &required {
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
    let components = prepared
        .as_ref()
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
    let supports = support_rows(prepared.as_ref(), &components, &rows);
    let document = json!({"schema_id":"ici.next.doctor","schema_version":1,"root":inspection.root,"profile":profile,"components":components,"system":system(environment),"runtime":{"kind":"rust-native","version":crate::VERSION,"executable":std::env::current_exe().ok(),"target_os":std::env::consts::OS,"target_arch":std::env::consts::ARCH,"requires_project_python":false},"running_python":null,"python_candidates":candidates,"analysis_profile":profile,"required_tools":required,"effective_required_tools":rows.iter().filter(|row|row["required"]==true).filter_map(|row|row["name"].as_str().map(str::to_owned)).collect::<BTreeSet<_>>(),"tools":tools,"capability_inventory":{"schema_version":"ici.capabilities/v1","status":if healthy{"PASS"}else{"WARN"},"healthy":healthy,"counts":{"total":rows.len(),"ready":ready,"required":rows.iter().filter(|row|row["required"]==true).count(),"incomplete":rows.iter().filter(|row|row["available"]==true&&row["complete"]==false).count(),"unavailable":rows.iter().filter(|row|row["available"]==false).count(),"blocked_required_checks":blocked_checks,"version_probes":inventory.needs.values().filter(|need|need.outcome.as_ref().is_some_and(|outcome|outcome.cleanup.group.is_some())).count()},"missing_required":missing,"incomplete_required":incomplete,"tools":rows},"support_matrix":{"project_languages":languages,"project_frameworks":[],"entries":supports},"paths":{"workspace":inspection.root,"configuration_files":inspection.configuration_files,"native_tool_pack":planning::tool_pack(environment)},"scope_error":scope_error,"scope_guidance":if scope_error.is_some(){json!({"config_key":"components","resolve":"declare valid component sources and dependencies; configuration inspection remains available","recheck":"ici next doctor"})}else{Value::Null},"limitations":limitations,"analysis_executed":false,"cancelled":cancellation.is_cancelled()});
    Ok(redact_value(&document))
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
                        .filter(|path| file_language(path) == Some("cpp"))
                        .cloned()
                        .collect()
                });
            let prepare=prepared.context_preparations.iter().filter(|plan|plan.component_id==component.id).map(|plan|json!({"build":plan.build_id,"variant":plan.build.variant,"descriptor":plan.descriptor,"steps":plan.build.tasks.iter().map(|task|&task.id).collect::<Vec<_>>(),"executed":false})).collect::<Vec<_>>();
            row["compile_inputs"] = json!({"database":context.and_then(|context|context.database_path.as_ref()),"origin":context.map(|context|&context.origin),"expected":context.map(|context|context.selected_sources.len()).unwrap_or(missing.len()),"covered":covered,"missing":missing,"generated":prepared.context_preparations.iter().filter(|plan|plan.component_id==component.id).flat_map(|plan|plan.build.generated_roots.iter()).collect::<BTreeSet<_>>(),"targets":prepared.project.builds.iter().filter(|build|component.build.as_deref()==Some(&build.id)).map(|build|json!({"name":build.id,"project":build.project,"depends":[],"evidence":"declared-build"})).collect::<Vec<_>>(),"prepare":prepare,"diagnostics":context.map(|context|&context.diagnostics),"evidence":"recorded-inputs-only","generated_complete":false,"limitations":["doctor does not generate missing inputs or execute compiler dependency probes"]});
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
