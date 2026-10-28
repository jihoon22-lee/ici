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
    let known: BTreeSet<_> = files.iter().map(|file| file.path.clone()).collect();
    let mut graph: BTreeMap<String, BTreeSet<String>> = known
        .iter()
        .map(|path| (path.clone(), BTreeSet::new()))
        .collect();
    let mut edge_lines = BTreeMap::new();
    for file in files {
        let mut includes = 0;
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
            }
            output.targets.push(target);
        }
        if includes == 0 {
            let mut target = InspectionTarget::at(&file.path, 1);
            target.target_name = "IncludeGraph".into();
            target.message = "No literal local include edges".into();
            output.targets.push(target);
        }
    }
    let cycles = components(&graph);
    for members in &cycles {
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
    output.extra = json!({"cycles":cycles,"graph":graph,"evidence":"estimated"});
    Ok(())
}
