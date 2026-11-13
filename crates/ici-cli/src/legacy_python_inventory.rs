//! Frozen legacy Python inventory of the `complexity` and `cycle` engines.
//!
//! The native checks report every inspected file (a `Python source` PASS row for a file without
//! functions, a `PythonImportGraph` PASS row per inspected module) and exact function columns.
//! Frozen `engines/complexity.py::_analyze_python_complexity` records one target per Python
//! function with `start_line`/`end_line` only, and `engines/cycle.py` records only the import
//! cycles, so the legacy view keeps exactly those targets (and their findings; the native
//! `ici.native.cycle.import` finding of a Python cycle is not a second one). Frozen also
//! lists the 15 most complex functions in `extra.top_complex_funcs` and, for a project without a
//! C++ complexity check, the empty `cpp_*` boundary extras of `_CppComplexityAnalysis()` (and,
//! for a project without a C++ cycle check, the empty C++ include extras of the cycle engine).
//! Function targets of one file follow frozen `ast.walk` (breadth-first) order.
//! In a hybrid project the resource/security engines also drop the C++-side `NotApplicable`
//! root target while their Python check inspected sources.
use crate::commands::{CliError, Result};
use serde_json::{Value, json};
use std::{collections::BTreeMap, path::Path};

const NO_FUNCTIONS: &str = "No function definitions in this checked file";
const IMPORTS_INSPECTED: &str = "Python imports were inspected within the selected module scope";
const PYTHON_CYCLE_RULE: &str = "ici.native.cycle.import";
const NO_PYTHON: &str = "No applicable Python source files were selected";

fn python(path: &str) -> bool {
    path.ends_with(".py") || path.ends_with(".pyi")
}

/// Whether a native inventory row has no frozen counterpart.
fn native_only(family: &str, target: &Value) -> bool {
    let path = target["file_path"].as_str().unwrap_or_default();
    let (name, message) = match family {
        "complexity" => ("Python source", NO_FUNCTIONS),
        "cycle" => ("PythonImportGraph", IMPORTS_INSPECTED),
        _ => return false,
    };
    python(path)
        && target["status"] == "PASS"
        && target["target_name"] == name
        && target["message"] == message
}

/// A Python function target of the complexity engine (frozen: no columns).
fn python_function(target: &Value) -> bool {
    python(target["file_path"].as_str().unwrap_or_default())
        && target["metrics"].get("complexity").is_some()
}

fn same_place(finding: &Value, target: &Value) -> bool {
    let location = &finding["primary_location"];
    location["path"] == target["file_path"]
        && location["start_line"] == target["start_line"]
        && location["label"] == target["target_name"]
        && finding["message"] == target["message"]
}

/// Frozen `ast.walk` order of each file's Python function targets (breadth first, see
/// `ici_analysis::python_function_walk_order`); every other target keeps its position.
fn walk_order(targets: &[Value], root: &Path) -> Vec<usize> {
    let mut slots: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    for (index, target) in targets.iter().enumerate() {
        if python_function(target) {
            slots
                .entry(target["file_path"].as_str().unwrap_or_default())
                .or_default()
                .push(index);
        }
    }
    let mut order: Vec<usize> = (0..targets.len()).collect();
    for (path, indices) in slots {
        let Some(lines) = std::fs::read(root.join(path))
            .ok()
            .and_then(|bytes| String::from_utf8(bytes).ok())
            .and_then(|text| ici_analysis::python_function_walk_order(&text))
        else {
            continue;
        };
        let rank = |index: &usize| {
            let line = targets[*index]["start_line"].as_u64().unwrap_or(0);
            lines
                .iter()
                .position(|walked| u64::from(*walked) == line)
                .unwrap_or(usize::MAX)
        };
        let mut sorted = indices.clone();
        sorted.sort_by_key(rank);
        for (slot, index) in indices.into_iter().zip(sorted) {
            order[slot] = index;
        }
    }
    order
}

/// Frozen resource/security engines analyse Python sources only; in a project that also has C/C++
/// sources the native `cpp.resource`/`cpp.security` check adds the project-root
/// `<Engine>:NotApplicable` "No applicable Python source files were selected" target, which frozen
/// reports only when no Python source was selected at all. Drop it (and its finding) while the
/// Python check of the same engine inspected sources.
fn hybrid_not_applicable(family: &str, engine: &mut Value) {
    let (name, python_check) = match family {
        "resource" => ("Resource:NotApplicable", "python.resource"),
        "security" => ("Security:NotApplicable", "python.security"),
        _ => return,
    };
    let python_ran = engine["extra"]["native_checks"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|check| {
            check["check_id"] == python_check
                && !check["summary"]
                    .as_str()
                    .is_some_and(|summary| summary.starts_with("no sources in scope"))
        });
    if !python_ran {
        return;
    }
    let stale = |target: &Value| {
        target["file_path"] == "."
            && target["target_name"] == name
            && target["message"] == NO_PYTHON
    };
    let Some(targets) = engine["targets"].as_array().cloned() else {
        return;
    };
    let dropped: Vec<&Value> = targets.iter().filter(|target| stale(target)).collect();
    if dropped.is_empty() {
        return;
    }
    let mut kept = vec![];
    let mut index_map = vec![None; targets.len()];
    for (index, target) in targets.iter().enumerate() {
        if !stale(target) {
            index_map[index] = Some(kept.len());
            kept.push(target.clone());
        }
    }
    if let Some(findings) = engine["findings"].as_array_mut() {
        findings.retain(|finding| !dropped.iter().any(|target| same_place(finding, target)));
    }
    remap_scopes(engine, &index_map);
    engine["targets"] = json!(kept);
}

/// Native target scopes index the targets; keep them pointing at the same rows.
fn remap_scopes(engine: &mut Value, index_map: &[Option<usize>]) {
    if let Some(scopes) = engine["extra"]["target_scopes"].as_array_mut() {
        scopes.retain_mut(|scope| {
            match scope["target_index"]
                .as_u64()
                .and_then(|index| index_map.get(index as usize).copied().flatten())
            {
                Some(index) => {
                    scope["target_index"] = json!(index);
                    true
                }
                None => false,
            }
        });
    }
}

/// `cpp_half`: frozen complexity.py:62 runs its C++ half (`proj_type in ("cpp", "hybrid")` or any
/// `project_root.rglob("*.cpp")` path), whatever C/C++ source the configured scope selected.
pub(crate) fn shape(family: &str, engine: &mut Value, root: &Path, cpp_half: bool) -> Result<()> {
    if matches!(family, "resource" | "security") {
        hybrid_not_applicable(family, engine);
        return Ok(());
    }
    if !matches!(family, "complexity" | "cycle") {
        return Ok(());
    }
    let targets = engine["targets"]
        .as_array()
        .cloned()
        .ok_or_else(|| CliError(format!("{family}: legacy targets must be an array")))?;
    let dropped: Vec<&Value> = targets
        .iter()
        .filter(|target| native_only(family, target))
        .collect();
    let order = if family == "complexity" {
        walk_order(&targets, root)
    } else {
        (0..targets.len()).collect()
    };
    let mut kept = vec![];
    let mut index_map = vec![None; targets.len()];
    for index in order {
        let target = &targets[index];
        if native_only(family, target) {
            continue;
        }
        let mut target = target.clone();
        if family == "complexity" && python_function(&target) {
            target["start_column"] = Value::Null;
            target["end_column"] = Value::Null;
        }
        index_map[index] = Some(kept.len());
        kept.push(target);
    }
    let mut removed = vec![];
    if let Some(findings) = engine["findings"].as_array_mut() {
        findings.retain(|finding| !dropped.iter().any(|target| same_place(finding, target)));
        if family == "cycle" {
            // Frozen reports a Python import cycle only as its `Cycle:N` target finding; the
            // native `ici.native.cycle.import` finding of the same cycle is not a second one.
            findings.retain(|finding| {
                let native = finding["rule_id"] == PYTHON_CYCLE_RULE
                    && python(
                        finding["primary_location"]["path"]
                            .as_str()
                            .unwrap_or_default(),
                    );
                if native {
                    removed.push(finding["fingerprint"].clone());
                }
                !native
            });
        }
        if family == "complexity" {
            for finding in findings.iter_mut() {
                if kept
                    .iter()
                    .any(|target| python_function(target) && same_place(finding, target))
                {
                    finding["primary_location"]["start_column"] = Value::Null;
                    finding["primary_location"]["end_column"] = Value::Null;
                }
            }
        }
    }
    if let Some(mappings) = engine["extra"]["finding_identity_map"].as_array_mut() {
        mappings.retain(|mapping| !removed.contains(&mapping["legacy_fingerprint"]));
    }
    remap_scopes(engine, &index_map);
    if family == "complexity" {
        let mut functions: Vec<&Value> = kept
            .iter()
            .filter(|target| target["metrics"].get("complexity").is_some())
            .collect();
        // Frozen `sorted(..., key=complexity, reverse=True)` is stable.
        functions.sort_by_key(|target| {
            std::cmp::Reverse(target["metrics"]["complexity"].as_i64().unwrap_or(0))
        });
        let top: Vec<Value> = functions
            .iter()
            .take(15)
            .map(|target| {
                json!({"file_path":target["file_path"],"start_line":target["start_line"],
                    "end_line":target["end_line"],"target_name":target["target_name"],
                    "status":target["status"],"message":target["message"],
                    "snippet":target["snippet"],"metrics":target["metrics"]})
            })
            .collect();
        if engine["extra"].get("top_complex_funcs").is_none() {
            engine["extra"]["top_complex_funcs"] = json!(top);
        }
        let cpp = engine["extra"]["native_checks"]
            .as_array()
            .into_iter()
            .flatten()
            .any(|check| check["check_id"] == "cpp.complexity");
        if !cpp && engine["extra"].get("cpp_boundary_mode").is_none() {
            // Frozen `_analyze_cpp_complexity` over no C/C++ source still reports the brace
            // heuristic's mode, and `ComplexityEngine.run` makes a heuristic mode ESTIMATED
            // evidence (an error stays NOT_RUN). Without the C++ half the mode is not applicable.
            let mode = if cpp_half {
                if engine["evidence"] == "MEASURED" {
                    engine["evidence"] = json!("ESTIMATED");
                }
                "heuristic"
            } else {
                "not_applicable"
            };
            for (key, value) in [
                ("cpp_boundary_mode", json!(mode)),
                ("cpp_exact_boundaries", json!(0)),
                ("cpp_estimated_boundaries", json!(0)),
                ("cpp_boundary_configurations_checked", json!(0)),
                ("cpp_boundary_sources_checked", json!(0)),
                ("cpp_boundary_warnings", json!([])),
                ("cpp_boundary_errors", json!([])),
                (
                    "cpp_scope_exclusions",
                    json!({"lambda":0,"macro_generated_function":0}),
                ),
            ] {
                engine["extra"][key] = value;
            }
        }
    }
    let cpp_cycle = engine["extra"]["native_checks"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|check| check["check_id"] == "cpp.cycle");
    if family == "cycle" && !cpp_cycle && engine["extra"].get("cpp_include_resolution").is_none() {
        // Frozen `_analyze_cpp_includes` over no C++ sources.
        for (key, value) in [
            (
                "cpp_include_resolution",
                json!("unique_project_path_suffix"),
            ),
            ("resolved_cpp_includes", json!(0)),
            ("ambiguous_cpp_includes", json!(0)),
            ("unresolved_cpp_includes", json!(0)),
            ("cpp_include_scope_counts", json!({})),
            ("cpp_configurations_checked", json!(0)),
            ("cpp_include_diagnostics_truncated", json!(0)),
        ] {
            engine["extra"][key] = value;
        }
    }
    engine["targets"] = json!(kept);
    Ok(())
}
