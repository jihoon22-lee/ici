//! Frozen engine `extra` counters of legacy C++ engines that the native checks do not carry
//! themselves: they are recomputed from the engine's own (already shaped) targets.
use serde_json::{Value, json};

fn checks(engine: &Value) -> Vec<Value> {
    engine["extra"]["native_checks"]
        .as_array()
        .cloned()
        .unwrap_or_default()
}

fn has_check(checks: &[Value], id: &str) -> bool {
    checks.iter().any(|check| check["check_id"] == id)
}

fn targets(engine: &Value) -> Vec<Value> {
    engine["targets"].as_array().cloned().unwrap_or_default()
}

fn status_in(target: &Value, statuses: &[&str]) -> bool {
    target["status"]
        .as_str()
        .is_some_and(|status| statuses.contains(&status))
}

fn linker_mode(extra: &Value) -> bool {
    extra["mode"] == "gnu-elf-section-gc"
        || extra.as_object().is_some_and(|fields| {
            fields
                .values()
                .any(|value| value["mode"] == "gnu-elf-section-gc")
        })
}

/// Fill the frozen counters of one legacy engine:
/// * `sanitize` / `thread_sanitize`: `ISSUE_COUNT_KEY`, the WARN/FAIL target count
///   (`_issue_count`), for the C++ sanitizer checks.
/// * `complexity` with `cpp.complexity`: `max_complexity`, `total_functions` (targets with a
///   complexity metric) and `issues_count` (WARN/FAIL/ERROR targets) over every language.
/// * `dead` with `cpp.dead`: `cpp_unused_mode` (`exact`, `error`, `unavailable`, `off`,
///   `not-applicable`), `cpp_unused_functions_count` and the "+"-joined `analysis_provenance`.
pub(crate) fn shape(family: &str, engine: &mut Value) {
    let checks = checks(engine);
    let targets = targets(engine);
    match family {
        "sanitize" | "thread_sanitize" => {
            let check = if family == "sanitize" {
                "cpp.sanitize"
            } else {
                "cpp.tsan"
            };
            if !has_check(&checks, check) {
                return;
            }
            let key = if family == "sanitize" {
                "sanitize_issues"
            } else {
                "thread_sanitize_issues"
            };
            if engine["extra"].get(key).is_none() {
                let issues = targets
                    .iter()
                    .filter(|target| status_in(target, &["WARN", "FAIL"]))
                    .count();
                engine["extra"][key] = json!(issues);
            }
        }
        "complexity" if has_check(&checks, "cpp.complexity") => {
            let functions: Vec<i64> = targets
                .iter()
                .filter_map(|target| target["metrics"].get("complexity"))
                .map(|value| value.as_i64().unwrap_or(0))
                .collect();
            let issues = targets
                .iter()
                .filter(|target| status_in(target, &["WARN", "FAIL", "ERROR"]))
                .count();
            let peak = functions.iter().copied().max().unwrap_or(0).max(0);
            engine["extra"]["max_complexity"] = json!(peak);
            engine["extra"]["total_functions"] = json!(functions.len());
            engine["extra"]["issues_count"] = json!(issues);
            engine["extra"]["metrics_summary"] = json!(format!(
                "Max CC: {peak} ({issues} issues / {} funcs)",
                functions.len()
            ));
        }
        "dead" if has_check(&checks, "cpp.dead") => {
            let named = |name: &str| targets.iter().any(|target| target["target_name"] == name);
            let mode = if named("C++UnusedFunctionError") {
                "error"
            } else if named("C++UnusedFunctionsUnavailable") {
                "unavailable"
            } else if named("C++UnusedFunctionsDisabled") {
                "off"
            } else if named("C++UnusedFunctions") || named("Compiler:-Wunused-function") {
                "exact"
            } else {
                "not-applicable"
            };
            let functions = targets
                .iter()
                .filter(|target| target["target_name"] == "Compiler:-Wunused-function")
                .count();
            engine["extra"]["cpp_unused_mode"] = json!(mode);
            engine["extra"]["cpp_unused_functions_count"] = json!(functions);
            let python = checks.iter().any(|check| {
                check["check_id"] == "python.dead"
                    && check["extra"]["analysis_provenance"]
                        .as_str()
                        .is_some_and(|provenance| provenance.contains("python-ast-heuristic"))
            });
            // The native linker output (direct or merged under its task id) records its
            // completed GNU ELF section-GC mode; a clean run discards nothing.
            let linker = !named("C++LinkerReachabilityError")
                && checks.iter().any(|check| {
                    check["check_id"] == "cpp.dead"
                        && check["state"] == "SUCCEEDED"
                        && linker_mode(&check["extra"])
                });
            let names: Vec<&str> = [
                python.then_some("python-ast-heuristic"),
                (mode == "exact").then_some("cpp-compiler-unused-function"),
                linker.then_some("cpp-gnu-elf-section-gc"),
            ]
            .into_iter()
            .flatten()
            .collect();
            engine["extra"]["analysis_provenance"] = json!(if names.is_empty() {
                "not-run".to_string()
            } else {
                names.join("+")
            });
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::shape;
    use serde_json::json;

    fn target(name: &str, status: &str, complexity: Option<i64>) -> serde_json::Value {
        let metrics = complexity.map_or_else(|| json!({}), |value| json!({"complexity": value}));
        json!({"target_name": name, "status": status, "metrics": metrics})
    }

    #[test]
    fn sanitizer_issue_counts_are_the_warn_and_fail_targets() {
        let mut engine = json!({"extra": {"native_checks": [{"check_id": "cpp.tsan"}]},
            "targets": [target("TSan Error", "FAIL", None), target("x", "PASS", None),
                target("y", "WARN", None), target("z", "ERROR", None)]});
        shape("thread_sanitize", &mut engine);
        assert_eq!(engine["extra"]["thread_sanitize_issues"], 2);
        let mut python = json!({"extra": {"native_checks": [{"check_id": "python.sanitize"}]},
            "targets": [target("x", "FAIL", None)]});
        shape("sanitize", &mut python);
        assert!(python["extra"].get("sanitize_issues").is_none());
    }

    #[test]
    fn complexity_counts_every_function_target_and_issue() {
        let mut engine = json!({"extra": {"native_checks": [{"check_id": "cpp.complexity"},
            {"check_id": "python.complexity"}]},
            "targets": [target("a", "PASS", Some(3)), target("b", "WARN", Some(11)),
                target("e", "ERROR", None)]});
        shape("complexity", &mut engine);
        assert_eq!(engine["extra"]["max_complexity"], 11);
        assert_eq!(engine["extra"]["total_functions"], 2);
        assert_eq!(engine["extra"]["issues_count"], 2);
    }

    #[test]
    fn dead_mode_count_and_provenance_follow_the_targets() {
        let mut engine = json!({"extra": {"native_checks": [{"check_id": "cpp.dead",
                "state": "SUCCEEDED", "extra": {"project.cpp.dead": {"mode": "gnu-elf-section-gc"}}},
            {"check_id": "python.dead", "extra": {"analysis_provenance": "python-ast-heuristic"}}]},
            "targets": [target("C++UnusedFunctions", "PASS", None),
                target("Compiler:-Wunused-function", "WARN", None)]});
        shape("dead", &mut engine);
        assert_eq!(engine["extra"]["cpp_unused_mode"], "exact");
        assert_eq!(engine["extra"]["cpp_unused_functions_count"], 1);
        assert_eq!(
            engine["extra"]["analysis_provenance"],
            "python-ast-heuristic+cpp-compiler-unused-function+cpp-gnu-elf-section-gc"
        );
        let mut error = json!({"extra": {"native_checks": [{"check_id": "cpp.dead"}]},
            "targets": [target("C++UnusedFunctionError", "ERROR", None)]});
        shape("dead", &mut error);
        assert_eq!(error["extra"]["cpp_unused_mode"], "error");
        assert_eq!(error["extra"]["analysis_provenance"], "not-run");
    }
}
