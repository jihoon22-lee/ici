//! Frozen `ici next plan` C++ wording (v0.13.0 `application/planning.py::compile_limitations`,
//! `application/gates.py::gate_cpp`/`gate_artifact`, `application/testing.py` and
//! `workspace/compile_units.py::_prepare`).
//!
//! A component with C++ in scope and no compilation database carries frozen's note
//! (`<id>: no compilation database for its C++ scope (<hint>)`) and coverage gap (`<id>: no
//! compilation database captured its C++ scope`; a gate reason only under `--require-full`).
//! A C++ check the native planner blocked carries frozen's reason (human plan, `--json`
//! document, gate reasons and limitations) when the frozen condition behind it holds.
use super::*;

/// Frozen `_prepare`: what would produce the missing database, declared and never invented.
/// Native builds carry no prepare vector, so a linked build always names the configure gap.
pub(super) fn prepare_hint(project: &ResolvedProject, component: &Component) -> String {
    match linked_build(project, component) {
        Some(build) => format!(
            "build '{}' declares no approved configure command — give it a prepare vector or generate {}/compile_commands.json",
            build.id,
            build.directory.as_deref().unwrap_or(".")
        ),
        None => format!(
            "no build unit is linked to {}; declare [builds.<id>] with a directory that will hold compile_commands.json and set the component's build, or generate the database out of band",
            component.id
        ),
    }
}

/// Frozen `cpp_gcno`: instrumentation notes under the linked build directory.
fn has_gcno(project: &ResolvedProject, component: &Component) -> bool {
    fn walk(directory: &Path, budget: &mut usize) -> bool {
        let Ok(entries) = std::fs::read_dir(directory) else {
            return false;
        };
        for entry in entries.flatten() {
            if *budget == 0 {
                return false;
            }
            *budget -= 1;
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            let path = entry.path();
            if kind.is_dir() {
                if walk(&path, budget) {
                    return true;
                }
            } else if kind.is_file() && path.extension().is_some_and(|ext| ext == "gcno") {
                return true;
            }
        }
        false
    }
    linked_build(project, component)
        .and_then(|build| build.directory.as_deref())
        .is_some_and(|directory| walk(&project.root.join(directory), &mut 200_000))
}

impl CppPlans {
    /// Record frozen's missing-database notes/gaps and the frozen reasons of C++ checks for a
    /// next-path plan (never for the legacy projection, whose engines report their own).
    pub fn frozen_next_plan(
        &mut self,
        project: &ResolvedProject,
        plan: &mut ici_application::plan::Plan,
        require_full: bool,
        environment: &BTreeMap<String, String>,
    ) {
        for component in project.components.iter().filter(|component| {
            plan.components.contains(&component.id)
                && component.languages.iter().any(|language| language == "cpp")
        }) {
            let missing = match self.contexts.get(&component.id) {
                Some(context) => context.database_path.is_none(),
                None => context(project, component, environment)
                    .0
                    .database_path
                    .is_none(),
            };
            let hint = prepare_hint(project, component);
            if missing {
                plan.limitations.push(format!(
                    "{}: no compilation database for its C++ scope ({hint})",
                    component.id
                ));
                let gap = format!(
                    "{}: no compilation database captured its C++ scope",
                    component.id
                );
                if require_full {
                    plan.coverage_gaps.push(gap.clone());
                }
                self.next_scope_gaps.push(gap);
            }
            let database =
                format!("no compilation database for the component's C++ scope — {hint}");
            let build = linked_build(project, component);
            let tested = plan.checks.iter().any(|planned| {
                planned.component_id == component.id && planned.check.id == "cpp.test"
            });
            for planned in plan
                .checks
                .iter_mut()
                .filter(|planned| planned.component_id == component.id)
            {
                let tidy_missing = || {
                    ToolRequest {
                        id: "clang-tidy".into(),
                        explicit: project
                            .tools
                            .get("clang-tidy")
                            .and_then(|tool| tool.path.as_deref())
                            .map(|path| crate::files::under(&project.root, path)),
                        project_root: project.root.clone(),
                        pack_root: None,
                        path_env: environment.get("PATH").cloned(),
                    }
                    .resolve()
                    .is_err()
                };
                let no_contract = build.is_some_and(|build| build.artifacts.is_empty());
                let reason = match planned.check.id.as_str() {
                    "cpp.compile" | "cpp.diagnostics" if missing => Some(database.clone()),
                    "cpp.tidy" if tidy_missing() => Some("clang-tidy is not available".into()),
                    "cpp.tidy" if missing => {
                        Some(format!("{database} — clang-tidy replays it via -p"))
                    }
                    "cpp.test" if build.is_none() => Some(
                        "no build unit linked — declare [builds.<id>] and attach it to the component"
                            .into(),
                    ),
                    "cpp.coverage" if !tested => {
                        Some("cpp.coverage reads the test run's data — enable cpp.test".into())
                    }
                    "cpp.coverage" if !has_gcno(project, component) => Some(
                        "build is not instrumented — no .gcno under the linked build directories"
                            .into(),
                    ),
                    "cpp.artifact" | "cpp.binary-compat" if build.is_none() => Some(
                        "no build unit linked — artifact declarations live on [builds.<id>]".into(),
                    ),
                    "cpp.artifact" if no_contract => {
                        Some("no artifact contract — declare [builds.<id>] artifacts = [...]".into())
                    }
                    "cpp.binary-compat" if no_contract => Some(
                        "no artifact contract — declare [builds.<id>] artifacts = [...] so the binaries to inspect are named"
                            .into(),
                    ),
                    _ => None,
                };
                if let Some(reason) = reason {
                    // Where frozen's gate condition holds and the native planner blocked the
                    // check too, the run records frozen's reason (gate reasons, limitations).
                    if let ici_application::plan::Work::Blocked { reason: native } =
                        &mut planned.work
                    {
                        native.clone_from(&reason);
                    }
                    self.next_blocked_reasons
                        .insert(planned.task_id.clone(), reason);
                }
            }
        }
    }

    /// Frozen human-plan reason of a natively blocked C++ check, when the frozen condition holds.
    pub fn frozen_blocked_reason(&self, task_id: &str) -> Option<&str> {
        self.next_blocked_reasons.get(task_id).map(String::as_str)
    }
}
