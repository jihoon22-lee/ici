use crate::{
    CppError,
    analysis::{finding, measurement},
};
use ici_domain::{AnalysisOutput, AnalysisRequest, InspectionTarget, SourceSpan};
use serde_json::json;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

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
/// Frozen `_find_cycles_tarjan` over `request.files`: components as they complete and each
/// component's members in stack-pop order. Frozen follows a member's several successors in Python
/// set order, which varies by run; native follows them in path order.
fn next_components(
    graph: &BTreeMap<String, BTreeSet<String>>,
    files: &[(String, String)],
) -> Vec<Vec<String>> {
    let (mut counter, mut stack, mut cycles) = (0usize, Vec::new(), Vec::new());
    let mut on_stack = BTreeSet::new();
    let (mut indices, mut lowlink) = (BTreeMap::new(), BTreeMap::new());
    let empty = BTreeSet::new();
    for (root, _) in files {
        if indices.contains_key(root.as_str()) {
            continue;
        }
        indices.insert(root.as_str(), counter);
        lowlink.insert(root.as_str(), counter);
        counter += 1;
        stack.push(root.as_str());
        on_stack.insert(root.as_str());
        let mut work = vec![(root.as_str(), graph.get(root).unwrap_or(&empty).iter())];
        while let Some((node, neighbours)) = work.last_mut() {
            let node = *node;
            let mut descended = None;
            for next in neighbours.by_ref() {
                if !indices.contains_key(next.as_str()) {
                    descended = Some(next.as_str());
                    break;
                }
                if on_stack.contains(next.as_str()) {
                    let index = indices[next.as_str()];
                    let low = lowlink.get_mut(node).unwrap();
                    *low = (*low).min(index);
                }
            }
            if let Some(next) = descended {
                indices.insert(next, counter);
                lowlink.insert(next, counter);
                counter += 1;
                stack.push(next);
                on_stack.insert(next);
                work.push((next, graph.get(next).unwrap_or(&empty).iter()));
                continue;
            }
            work.pop();
            let low = lowlink[node];
            if let Some((parent, _)) = work.last() {
                let parent = lowlink.get_mut(*parent).unwrap();
                *parent = (*parent).min(low);
            }
            if low == indices[node] {
                let mut component = vec![];
                while let Some(member) = stack.pop() {
                    on_stack.remove(member);
                    component.push(member.to_owned());
                    if member == node {
                        break;
                    }
                }
                if component.len() > 1 || graph.get(node).is_some_and(|next| next.contains(node)) {
                    cycles.push(component);
                }
            }
        }
    }
    cycles
}

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
/// Frozen `f.read_text(encoding="utf-8", errors="ignore")`: undecodable bytes are dropped and
/// universal newlines translate CRLF and CR to LF. Neither frozen include scan parses C++.
pub(crate) fn include_scan_text(bytes: &[u8]) -> String {
    let text: String = bytes
        .utf8_chunks()
        .map(|chunk| chunk.valid())
        .collect::<Vec<_>>()
        .concat();
    text.replace("\r\n", "\n").replace('\r', "\n")
}
/// One frozen `_IncludeDiagnostic`: source, line, include, candidates, stripped source line.
type IncludeDiagnostic = (String, usize, String, Vec<String>, String);
/// Frozen `_build_cpp_graph` over `(path, text)` in the given order: unique full-suffix
/// resolution of every `#include "..."` in the raw text (comments and inactive branches too), a
/// non-unique or missing match kept as a located diagnostic, no self edge.
fn include_graph(
    files: &[&(String, String)],
) -> (
    BTreeMap<String, BTreeSet<String>>,
    Vec<IncludeDiagnostic>,
    usize,
) {
    let paths: Vec<Vec<&str>> = files
        .iter()
        .map(|(path, _)| path.split('/').collect())
        .collect();
    let mut graph: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut diagnostics = vec![];
    let mut resolved = 0usize;
    for (path, text) in files {
        graph.entry(path.clone()).or_default();
        for (start, include) in legacy_includes(text) {
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
                let target = &files[candidates[0]].0;
                if target != path {
                    graph.get_mut(path).unwrap().insert(target.clone());
                }
                continue;
            }
            let line = text[..start].matches('\n').count() + 1;
            let line_start = text[..start].rfind('\n').map_or(0, |index| index + 1);
            let line_end = text[start..]
                .find('\n')
                .map_or(text.len(), |index| start + index);
            diagnostics.push((
                path.clone(),
                line,
                include.to_owned(),
                candidates
                    .iter()
                    .map(|index| files[*index].0.clone())
                    .collect::<Vec<_>>(),
                text[line_start..line_end]
                    .trim_matches(crate::legacy_heuristic::py_space)
                    .to_owned(),
            ));
        }
    }
    (graph, diagnostics, resolved)
}
pub(crate) fn analyze(
    request: &AnalysisRequest,
    output: &mut AnalysisOutput,
    files: &[(String, String)],
) -> Result<(), CppError> {
    let limit = report_limit(request)?;
    if request
        .options
        .get("legacy_projection")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false)
    {
        legacy(request, output, files, limit);
        return Ok(());
    }
    // Frozen next `languages/cycles._measure_cpp` over `request.files` in order, unbounded by the
    // legacy `max_reported`: every cycle at line 1 of its members, every diagnostic a limitation.
    let ordered: Vec<_> = files.iter().collect();
    let (graph, diagnostics, _) = include_graph(&ordered);
    let cycles = next_components(&graph, files);
    for members in &cycles {
        let first = &members[0];
        let mut target = InspectionTarget::at(first, 1);
        target.target_name = "IncludeCycle".into();
        target.status = "WARN".into();
        target.message = format!("include cycle: {} -> {}", members.join(" -> "), members[0]);
        finding(output, request, &target, "cycle.include", "medium", true);
        let issue = output.observation.findings.last_mut().unwrap();
        // Frozen `languages/cycles.py`: a path-suffix include cycle is low-confidence evidence.
        issue.confidence = "low".into();
        issue.primary_location.label = String::new();
        issue.related_locations = members
            .iter()
            .skip(1)
            .map(|path| SourceSpan::line(path, 1))
            .collect();
        output.targets.push(target);
    }
    let represented: BTreeSet<_> = output
        .targets
        .iter()
        .map(|target| target.file_path.clone())
        .collect();
    for (path, _) in files {
        if !represented.contains(path) {
            let mut target = InspectionTarget::at(path, 1);
            target.target_name = "IncludeGraph".into();
            target.message = "No include cycle through this file".into();
            output.targets.push(target);
        }
    }
    measurement(output, "cycles_found", cycles.len() as u64, true);
    output
        .observation
        .limitations
        .push(HEURISTIC_LIMITATION.into());
    for (path, line, include, candidates, _) in &diagnostics {
        let kind = if candidates.is_empty() {
            "unresolved"
        } else {
            "ambiguous"
        };
        output.observation.limitations.push(format!(
            "{kind} include {} at {path}:{line}",
            ici_analysis::unicode::python_repr(include)
        ));
    }
    output.extra = json!({"cycles":cycles,"graph":graph,"evidence":"estimated","cycles_reported":cycles.len(),"cycles_truncated":0,"include_diagnostics":diagnostics.len(),"include_diagnostics_truncated":0});
    Ok(())
}

/// Frozen `_measure_cpp` limitation text.
const HEURISTIC_LIMITATION: &str = "include resolution is a path-suffix heuristic — compiler search order and generated headers are not modelled";

const LEGACY_SOURCES: [&str; 4] = ["cpp", "cc", "cxx", "c"];
/// Frozen `#include\s*"([^"]+)"` over raw text (comments and inactive branches included).
fn legacy_includes(text: &str) -> Vec<(usize, &str)> {
    let mut found = vec![];
    let mut offset = 0;
    while let Some(position) = text[offset..].find("#include") {
        let start = offset + position;
        let rest = &text[start + "#include".len()..];
        let trimmed = rest.trim_start_matches(crate::legacy_heuristic::py_space);
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
    files: &[(String, String)],
    limit: usize,
) {
    let source = |path: &str| {
        Path::new(path)
            .extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| LEGACY_SOURCES.contains(&extension))
    };
    let mut ordered: Vec<_> = files.iter().collect();
    ordered.sort_by_key(|(path, _)| (!source(path), path.clone()));
    let (graph, diagnostics, resolved) = include_graph(&ordered);
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
    output
        .observation
        .limitations
        .push(HEURISTIC_LIMITATION.into());
    output.extra = json!({"cycles":cycles,"graph":graph,"evidence":"estimated","cpp_cycles":cycles.len(),"cycles_reported":cycles.len().min(limit),"cycles_truncated":cycles.len().saturating_sub(limit),"cpp_include_resolution":"unique_project_path_suffix","resolved_cpp_includes":resolved,"ambiguous_cpp_includes":ambiguous,"unresolved_cpp_includes":diagnostics.len()-ambiguous,"include_diagnostics":diagnostics.len(),"cpp_include_diagnostics_truncated":diagnostics.len().saturating_sub(limit)});
}
/// Frozen `_find_actual_cycle_path`: follow real edges inside the component back to start.
pub(crate) fn actual_cycle_path(
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
