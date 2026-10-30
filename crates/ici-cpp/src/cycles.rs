use crate::{
    CppError,
    analysis::{finding, measurement},
    source::{self, ParsedSource},
};
use ici_domain::{AnalysisOutput, AnalysisRequest, InspectionTarget, SourceSpan};
use serde_json::json;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Component, Path, PathBuf},
    sync::Arc,
};

fn normalized(path: &Path) -> Option<String> {
    let mut result = PathBuf::new();
    for part in path.components() {
        match part {
            Component::Normal(value) => result.push(value),
            Component::CurDir => {}
            Component::ParentDir => {
                if !result.pop() {
                    return None;
                }
            }
            _ => return None,
        }
    }
    result.to_str().map(str::to_owned)
}

pub(crate) fn report_limit(request: &AnalysisRequest) -> Result<usize, CppError> {
    request.options.get("max_reported").map_or(Ok(20), |value| {
        value
            .as_u64()
            .filter(|value| *value > 0)
            .and_then(|value| usize::try_from(value).ok())
            .ok_or_else(|| {
                CppError::new(
                    "invalid-report-limit",
                    "max_reported must be a positive integer",
                )
            })
    })
}
/// Deterministic iterative strongly connected components, including explicit self edges.
pub(crate) fn components(graph: &BTreeMap<String, BTreeSet<String>>) -> Vec<Vec<String>> {
    let mut visited = BTreeSet::new();
    let mut order = vec![];
    for start in graph.keys() {
        if visited.contains(start) {
            continue;
        }
        let mut pending = vec![(start.clone(), false)];
        while let Some((node, exit)) = pending.pop() {
            if exit {
                order.push(node);
                continue;
            }
            if !visited.insert(node.clone()) {
                continue;
            }
            pending.push((node.clone(), true));
            if let Some(edges) = graph.get(&node) {
                for edge in edges.iter().rev() {
                    if !visited.contains(edge) {
                        pending.push((edge.clone(), false));
                    }
                }
            }
        }
    }
    let mut reverse: BTreeMap<String, BTreeSet<String>> = graph
        .keys()
        .map(|key| (key.clone(), BTreeSet::new()))
        .collect();
    for (source, edges) in graph {
        for edge in edges {
            reverse
                .entry(edge.clone())
                .or_default()
                .insert(source.clone());
        }
    }
    visited.clear();
    let mut result = vec![];
    for start in order.into_iter().rev() {
        if visited.contains(&start) {
            continue;
        }
        let mut group = vec![];
        let mut pending = vec![start];
        while let Some(node) = pending.pop() {
            if !visited.insert(node.clone()) {
                continue;
            }
            group.push(node.clone());
            if let Some(edges) = reverse.get(&node) {
                pending.extend(edges.iter().rev().cloned());
            }
        }
        group.sort();
        if group.len() > 1
            || graph
                .get(&group[0])
                .is_some_and(|edges| edges.contains(&group[0]))
        {
            result.push(group);
        }
    }
    result.sort();
    result
}
pub(crate) fn analyze(
    request: &AnalysisRequest,
    output: &mut AnalysisOutput,
    files: &[Arc<ParsedSource>],
) -> Result<(), CppError> {
    let limit = report_limit(request)?;
    if request.options["legacy_projection"]
        .as_bool()
        .unwrap_or(false)
    {
        legacy(request, output, files, limit);
        return Ok(());
    }
    let known: BTreeSet<_> = files.iter().map(|file| file.path.clone()).collect();
    let mut graph: BTreeMap<String, BTreeSet<String>> = known
        .iter()
        .map(|path| (path.clone(), BTreeSet::new()))
        .collect();
    let mut edge_lines = BTreeMap::new();
    let mut diagnostics = 0usize;
    for file in files {
        let mut includes = 0;
        let target_start = output.targets.len();
        for node in source::nodes(file.tree.root_node())?
            .into_iter()
            .filter(|node| node.kind() == "preproc_include")
        {
            let Some(path) = node.child_by_field_name("path") else {
                continue;
            };
            let value = file.text(path);
            // System includes and macro-computed operands have no suffix-based local claim.
            if !value.starts_with('"') || !value.ends_with('"') {
                continue;
            }
            let value = &value[1..value.len() - 1];
            includes += 1;
            let relative = Path::new(&file.path)
                .parent()
                .unwrap_or(Path::new(""))
                .join(value);
            let direct = normalized(&relative).filter(|path| known.contains(path));
            let candidates = if let Some(direct) = direct {
                vec![direct]
            } else {
                known
                    .iter()
                    .filter(|path| path.as_str() == value || path.ends_with(&format!("/{value}")))
                    .cloned()
                    .collect::<Vec<_>>()
            };
            let mut target = file.target(node, format!("include {value}"));
            if candidates.len() == 1 {
                let destination = candidates[0].clone();
                graph
                    .get_mut(&file.path)
                    .unwrap()
                    .insert(destination.clone());
                edge_lines.insert((file.path.clone(), destination.clone()), target.start_line);
                target.metrics.insert("resolved".into(), json!(destination));
            } else {
                target.status = "SKIP".into();
                let reason = if candidates.is_empty() {
                    "unresolved"
                } else {
                    "ambiguous"
                };
                target.message = format!("{reason} local include {value}");
                output.observation.limitations.push(format!(
                    "{}:{}: {}",
                    file.path, target.start_line, target.message
                ));
                diagnostics += 1;
                if diagnostics > limit {
                    continue;
                }
            }
            output.targets.push(target);
        }
        if output.targets.len() == target_start {
            let mut target = InspectionTarget::at(&file.path, 1);
            target.target_name = "IncludeGraph".into();
            target.message = if includes == 0 {
                "No literal local include edges"
            } else {
                "Additional include diagnostics omitted by max_reported; full graph counts are retained"
            }.into();
            output.targets.push(target);
        }
    }
    let cycles = components(&graph);
    for members in cycles.iter().take(limit) {
        let first = &members[0];
        let line = graph[first]
            .iter()
            .filter(|edge| members.contains(edge))
            .find_map(|edge| edge_lines.get(&(first.clone(), edge.clone())))
            .copied()
            .unwrap_or(1);
        let mut target = InspectionTarget::at(first, line);
        target.target_name = "IncludeCycle".into();
        target.status = "WARN".into();
        target.message = format!("include cycle: {} -> {}", members.join(" -> "), members[0]);
        finding(output, request, &target, "cycle.include", "medium", true);
        output
            .observation
            .findings
            .last_mut()
            .unwrap()
            .related_locations = members
            .iter()
            .skip(1)
            .map(|path| SourceSpan::line(path, 1))
            .collect();
        output.targets.push(target);
    }
    measurement(output, "cycles_found", cycles.len() as u64, true);
    output.observation.limitations.push("Include resolution is a path-suffix heuristic; compiler search order, conditional preprocessing and generated headers are not established".into());
    output.extra = json!({"cycles":cycles,"graph":graph,"evidence":"estimated","cycles_reported":cycles.len().min(limit),"cycles_truncated":cycles.len().saturating_sub(limit),"include_diagnostics":diagnostics,"include_diagnostics_truncated":diagnostics.saturating_sub(limit)});
    Ok(())
}

const LEGACY_SOURCES: [&str; 4] = ["cpp", "cc", "cxx", "c"];
/// Frozen `#include\s*"([^"]+)"` over raw text (comments and inactive branches included).
fn legacy_includes(text: &str) -> Vec<(usize, &str)> {
    let mut found = vec![];
    let mut offset = 0;
    while let Some(position) = text[offset..].find("#include") {
        let start = offset + position;
        let rest = &text[start + "#include".len()..];
        let trimmed = rest.trim_start();
        offset = start + "#include".len();
        if let Some(quoted) = trimmed.strip_prefix('"')
            && let Some(end) = quoted.find('"')
            && end > 0
        {
            found.push((start, &quoted[..end]));
            offset = start + "#include".len() + (rest.len() - trimmed.len()) + end + 2;
        }
    }
    found
}
fn legacy_include_parts(include: &str) -> Vec<String> {
    let normalized = include.replace('\\', "/");
    let parts: Vec<_> = normalized
        .split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .map(str::to_owned)
        .collect();
    if parts.is_empty() || normalized.starts_with('/') || parts.iter().any(|part| part == "..") {
        return vec![];
    }
    parts
}
/// Frozen heuristic include cycles: unique full-suffix resolution over every source and
/// header, CppCycle/CppIncludeAmbiguous/CppIncludeUnresolved targets only.
fn legacy(
    request: &AnalysisRequest,
    output: &mut AnalysisOutput,
    files: &[Arc<ParsedSource>],
    limit: usize,
) {
    let source = |path: &str| {
        Path::new(path)
            .extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| LEGACY_SOURCES.contains(&extension))
    };
    let mut ordered: Vec<_> = files.iter().collect();
    ordered.sort_by_key(|file| (!source(&file.path), file.path.clone()));
    let paths: Vec<Vec<&str>> = ordered
        .iter()
        .map(|file| file.path.split('/').collect())
        .collect();
    let mut graph: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut diagnostics = vec![];
    let mut resolved = 0usize;
    for file in &ordered {
        graph.entry(file.path.clone()).or_default();
        for (start, include) in legacy_includes(&file.text) {
            let wanted = legacy_include_parts(include);
            let candidates: Vec<usize> = if wanted.is_empty() {
                vec![]
            } else {
                paths
                    .iter()
                    .enumerate()
                    .filter(|(_, parts)| {
                        parts.len() >= wanted.len()
                            && parts[parts.len() - wanted.len()..]
                                .iter()
                                .zip(&wanted)
                                .all(|(left, right)| left == right)
                    })
                    .map(|(index, _)| index)
                    .collect()
            };
            if candidates.len() == 1 {
                resolved += 1;
                let target = &ordered[candidates[0]].path;
                if target != &file.path {
                    graph.get_mut(&file.path).unwrap().insert(target.clone());
                }
                continue;
            }
            let line = file.text[..start].matches('\n').count() + 1;
            let line_start = file.text[..start].rfind('\n').map_or(0, |index| index + 1);
            let line_end = file.text[start..]
                .find('\n')
                .map_or(file.text.len(), |index| start + index);
            diagnostics.push((
                file.path.clone(),
                line,
                include.to_owned(),
                candidates
                    .iter()
                    .map(|index| ordered[*index].path.clone())
                    .collect::<Vec<_>>(),
                file.text[line_start..line_end].trim().to_owned(),
            ));
        }
    }
    let cycles = components(&graph);
    for members in cycles.iter().take(limit) {
        let start = members[0].clone();
        let path = actual_cycle_path(&start, members, &graph);
        let mut names: Vec<_> = path
            .iter()
            .map(|path| path.rsplit('/').next().unwrap_or(path))
            .collect();
        names.push(names[0]);
        let mut target = InspectionTarget::at(&path[0], 1);
        target.target_name = format!("CppCycle:{}", path.len());
        target.status = "WARN".into();
        target.message = format!(
            "C++ include cycle ({} files): {}",
            path.len(),
            names.join(" -> ")
        );
        target.metrics.insert("files".into(), json!(path));
        finding(output, request, &target, "cycle.include", "medium", true);
        output.targets.push(target);
    }
    for (path, line, include, candidates, snippet) in diagnostics.iter().take(limit) {
        let ambiguous = !candidates.is_empty();
        let mut target = InspectionTarget::at(path, *line as u32);
        target.target_name = if ambiguous {
            "CppIncludeAmbiguous"
        } else {
            "CppIncludeUnresolved"
        }
        .into();
        target.status = "WARN".into();
        target.message = if ambiguous {
            format!(
                "Quoted include \"{include}\" matches {} project files; no dependency edge was guessed",
                candidates.len()
            )
        } else {
            format!(
                "Quoted include \"{include}\" has no project-file match; it may be generated or supplied by compiler include paths"
            )
        };
        target.snippet = snippet.clone();
        target.metrics.insert("include".into(), json!(include));
        target
            .metrics
            .insert("candidates".into(), json!(candidates));
        target
            .metrics
            .insert("resolution".into(), json!("unique_project_path_suffix"));
        finding(
            output,
            request,
            &target,
            "cycle.include-resolution",
            "medium",
            true,
        );
        output.targets.push(target);
    }
    let ambiguous = diagnostics.iter().filter(|item| !item.3.is_empty()).count();
    measurement(output, "cycles_found", cycles.len() as u64, true);
    output.observation.limitations.push("Include resolution is a path-suffix heuristic; compiler search order, conditional preprocessing and generated headers are not established".into());
    output.extra = json!({"cycles":cycles,"graph":graph,"evidence":"estimated","cpp_cycles":cycles.len(),"cycles_reported":cycles.len().min(limit),"cycles_truncated":cycles.len().saturating_sub(limit),"cpp_include_resolution":"unique_project_path_suffix","resolved_cpp_includes":resolved,"ambiguous_cpp_includes":ambiguous,"unresolved_cpp_includes":diagnostics.len()-ambiguous,"include_diagnostics":diagnostics.len(),"cpp_include_diagnostics_truncated":diagnostics.len().saturating_sub(limit)});
}
/// Frozen `_find_actual_cycle_path`: follow real edges inside the component back to start.
fn actual_cycle_path(
    start: &str,
    members: &[String],
    graph: &BTreeMap<String, BTreeSet<String>>,
) -> Vec<String> {
    let inside = |node: &str| -> Vec<String> {
        graph
            .get(node)
            .into_iter()
            .flatten()
            .filter(|next| members.contains(next))
            .cloned()
            .collect()
    };
    let mut path = vec![start.to_owned()];
    let mut visiting = BTreeSet::from([start.to_owned()]);
    let mut work = vec![(start.to_owned(), inside(start), 0usize)];
    while let Some((node, neighbors, index)) = work.last_mut() {
        if *index >= neighbors.len() {
            visiting.remove(node.as_str());
            work.pop();
            path.pop();
            continue;
        }
        let next = neighbors[*index].clone();
        *index += 1;
        if next == start {
            return path;
        }
        if visiting.insert(next.clone()) {
            path.push(next.clone());
            let edges = inside(&next);
            work.push((next, edges, 0));
        }
    }
    members.to_vec()
}
