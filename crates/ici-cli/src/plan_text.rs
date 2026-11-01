//! Frozen `ici next plan` human output (v0.13.0 `cli/next_render.py::plan_text` and
//! `application/planning.py::describe`), rendered from the native prepared plan.
//!
//! Layout is the frozen one: `<workspace>: N check(s)`, one block per component that planned a
//! check, `  <task>: blocked — <reason>` / `  <task>: <title> (ici)` / `  <task>: <argv joined by
//! spaces>[ [mutating]][ [requires a, b]]`, then the `dependencies:`, `shared:`, `mutating:` and
//! `builds:` sections and the `  coverage gap:` / `  note:` lines. The sections read the same
//! graph/builds/gap/limitation values the `--json` document carries so the two never disagree.
//!
//! Native-only facts with no frozen counterpart are kept and recorded as reviewed differences
//! (runtime.json `command:ici next plan`): a check that runs an ordered step chain prints its
//! steps joined by ` → ` with the conditional-step lines beneath it, ty advisory checks keep
//! their advisory marker, and owned C++ preparations / release effects / source-identity
//! probes keep their lines between `builds:` and the coverage gaps.
use crate::planning::PreparedPlan;
use ici_providers::resolver::ResolutionError;
use serde_json::Value;

/// Frozen next-path tool lookup refusal (`ruff is not available`, `clang-tidy is not available`):
/// a tool found nowhere is named that way in the plan, its JSON `blocked` reason and the gate.
/// Other resolution failures (checksum mismatch, escaping pack, unusable explicit executable)
/// keep their native reason.
pub(crate) fn tool_blocked_reason(error: &ResolutionError) -> String {
    if error.reason.starts_with("not found") {
        format!("{} is not available", error.id)
    } else {
        error.to_string()
    }
}

fn joined(values: Option<&Value>) -> String {
    values
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect::<Vec<_>>()
        .join(", ")
}

impl PreparedPlan {
    /// Frozen `uncovered_scope`: what the request left out of the required scope. The plan names
    /// it whether or not `--require-full` was given; only `--require-full` turns it into gate
    /// reasons (`Plan::coverage_gaps`, frozen `apply_require_full`).
    pub(crate) fn scope_gaps(&self) -> Vec<String> {
        let scope: Vec<_> = self
            .project
            .components
            .iter()
            .filter(|component| self.plan.components.contains(&component.id))
            .collect();
        let mut gaps = self.request.coverage_gaps(&self.project, &scope);
        for gap in &self.plan.coverage_gaps {
            if !gaps.contains(gap) {
                gaps.push(gap.clone());
            }
        }
        gaps
    }

    pub(crate) fn frozen_text(&self) -> String {
        let document = self.document();
        let mut text = format!(
            "{}: {} check(s)\n",
            self.project.workspace_name,
            self.plan.checks.len()
        );
        for component in &self.plan.components {
            let checks: Vec<_> = self
                .plan
                .checks
                .iter()
                .filter(|planned| &planned.component_id == component)
                .collect();
            if checks.is_empty() {
                continue;
            }
            text.push_str(&format!("{component}:\n"));
            for check in checks {
                text.push_str(&format!("  {}: {}\n", check.task_id, self.describe(check)));
                text.push_str(&self.conditional_lines(&check.task_id));
            }
        }
        let graph = &document["graph"];
        let section = |name: &str, verb: &str, empty: &str, text: &mut String| {
            let Some(entries) = graph[name].as_object().filter(|map| !map.is_empty()) else {
                return;
            };
            text.push_str(&format!(
                "{}:\n",
                if name == "edges" {
                    "dependencies"
                } else {
                    name
                }
            ));
            for (unit, values) in entries {
                let values = joined(Some(values));
                text.push_str(&format!(
                    "  {unit} {verb} {}\n",
                    if values.is_empty() { empty } else { &values }
                ));
            }
        };
        section("edges", "needs", "", &mut text);
        section("shared", "runs once for", "", &mut text);
        section("mutating", "writes", "(undeclared)", &mut text);
        if let Some(builds) = document["builds"].as_array().filter(|b| !b.is_empty()) {
            text.push_str("builds:\n");
            for build in builds {
                let linked = joined(build.get("linked_by"));
                text.push_str(&format!(
                    "  {}: {} {} → {} (for {})\n",
                    build["id"].as_str().unwrap_or_default(),
                    build["system"].as_str().unwrap_or_default(),
                    build["variant"].as_str().unwrap_or_default(),
                    build["directory"].as_str().unwrap_or_default(),
                    if linked.is_empty() {
                        "unlinked"
                    } else {
                        &linked
                    }
                ));
            }
        }
        text.push_str(&self.native_preparation_lines());
        for gap in document["coverage_gaps"].as_array().into_iter().flatten() {
            text.push_str(&format!(
                "  coverage gap: {}\n",
                gap.as_str().unwrap_or_default()
            ));
        }
        for limitation in document["limitations"].as_array().into_iter().flatten() {
            text.push_str(&format!(
                "  note: {}\n",
                limitation.as_str().unwrap_or_default()
            ));
        }
        ici_execution::redaction::redact_text(&text)
    }

    /// Frozen `describe` for one planned check.
    fn describe(&self, check: &ici_application::plan::PlannedCheck) -> String {
        if let Some(reason) = self
            .graph
            .blocked
            .get(&check.task_id)
            .map(String::as_str)
            .or_else(|| check.blocked())
        {
            return format!("blocked — {reason}");
        }
        let tasks: Vec<_> = self
            .probes
            .get(&check.task_id)
            .into_iter()
            .chain(check.tasks().iter())
            .collect();
        if tasks.is_empty() {
            return format!("{} (ici)", check.check.title);
        }
        let mut line = tasks
            .iter()
            .map(|task| task.argv.join(" "))
            .collect::<Vec<_>>()
            .join(" → ");
        if tasks.iter().any(|task| task.mutating()) {
            line.push_str(" [mutating]");
        }
        let mut requires: Vec<&str> = vec![];
        for task in &tasks {
            for item in &task.requires {
                if !requires.contains(&item.as_str()) {
                    requires.push(item);
                }
            }
        }
        if !requires.is_empty() {
            line.push_str(&format!(" [requires {}]", requires.join(", ")));
        }
        if check.advisory() {
            line.push_str(" [advisory findings; completion required]");
        }
        line
    }

    fn conditional_lines(&self, task_id: &str) -> String {
        let mut text = String::new();
        for plan in self.validations.get(task_id).into_iter().flatten() {
            for (step, condition) in &plan.conditional_steps {
                let detail = if let Some(format) = condition.gcov_format {
                    format!(
                        "selects gcov {} format",
                        match format {
                            ici_validation::GcovFormat::Json => "JSON",
                            ici_validation::GcovFormat::Text => "text",
                        }
                    )
                } else if let Some(module) = &condition.missing_module {
                    format!("confirms module {module} is absent")
                } else {
                    format!(
                        "confirms coverage is {}",
                        if condition.coverage_available {
                            "available"
                        } else {
                            "absent"
                        }
                    )
                };
                text.push_str(&format!(
                    "    {step}: only when {} {detail}\n",
                    condition.probe_task_id
                ));
            }
        }
        text
    }

    /// Owned C++ preparations, release effects and read-only source identity probes: native
    /// work the frozen plan never had (frozen `prepare = "explicit"` ran nothing).
    fn native_preparation_lines(&self) -> String {
        let mut text = String::new();
        for preparation in &self.context_preparations {
            text.push_str(&format!(
                "Prepare {} {} in {}:\n",
                preparation.component_id, preparation.build.variant, preparation.build.shadow
            ));
            for task in &preparation.build.tasks {
                text.push_str(&format!("  {}\n", task.argv.join(" ")));
            }
            text.push_str("  Effects: configure/build and reset only owned single-link coverage counters in that shadow.\n");
        }
        if !self.context_preparations.is_empty()
            && self
                .plan
                .checks
                .iter()
                .any(|check| check.check.id == "cpp.artifact")
        {
            text.push_str("  Release effects: measure compiled binaries/libraries, copy verified bytes into the configured version directory, and write owned manifests/environment scripts; names resolve after build (256 MiB/file, 512 MiB total input).\n");
        }
        for task in &self.source_identity.tasks {
            text.push_str(&format!(
                "  Source identity (read-only): {}\n",
                task.argv.join(" ")
            ));
        }
        if let Some(reason) = &self.source_identity.limitation {
            text.push_str(&format!("  Source identity: {reason}\n"));
        }
        text
    }
}
