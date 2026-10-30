//! Deterministic, iterative SCC analysis over the selected Python module graph.
use crate::{
    cancelled, failure, finding, finish_status,
    modules::{ModuleIndex, relative_import},
    number_option,
    parser::ParsedFile,
    source::SourceError,
};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, EvidenceLevel, Finding, InspectionTarget, Measurement,
    SourceSpan,
};
use ruff_python_ast::{
    Stmt,
    visitor::{self, Visitor},
};
use ruff_text_size::Ranged;
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, LazyLock, atomic::AtomicBool};

static STDLIB: LazyLock<BTreeMap<String, BTreeSet<String>>> = LazyLock::new(|| {
    serde_json::from_str::<serde_json::Value>(include_str!("stdlib.json"))
        .expect("frozen stdlib data")["modules"]
        .as_object()
        .expect("module map")
        .iter()
        .map(|(version, names)| {
            (
                version.clone(),
                names
                    .as_array()
                    .expect("module list")
                    .iter()
                    .map(|name| name.as_str().expect("module string").to_owned())
                    .collect(),
            )
        })
        .collect()
});
struct Import {
    module: Option<String>,
    level: u32,
    names: Vec<String>,
    from: bool,
    line: u32,
}
struct Imports<'a> {
    file: &'a ParsedFile,
    imports: Vec<Import>,
    cancellation: Option<&'a AtomicBool>,
}
impl<'a> Visitor<'a> for Imports<'_> {
    fn visit_stmt(&mut self, node: &'a Stmt) {
        if crate::cancellation_requested(self.cancellation) {
            return;
        }
        match node {
            Stmt::Import(import) => {
                for alias in &import.names {
                    self.imports.push(Import {
                        module: Some(alias.name.to_string()),
                        level: 0,
                        names: vec![],
                        from: false,
                        line: self.file.source.location(node.range().start().to_usize()).0,
                    });
                }
            }
            Stmt::ImportFrom(import) => self.imports.push(Import {
                module: import.module.as_ref().map(ToString::to_string),
                level: import.level,
                names: import
                    .names
                    .iter()
                    .map(|name| name.name.to_string())
                    .collect(),
                from: true,
                line: self.file.source.location(node.range().start().to_usize()).0,
            }),
            _ => {}
        }
        visitor::walk_stmt(self, node);
    }
    fn visit_expr(&mut self, _: &'a ruff_python_ast::Expr) {
        // Import statements cannot occur inside expressions. Dynamic import calls are outside
        // this bounded static graph and do not justify traversing arbitrarily large literals.
    }
}
pub(crate) fn components(graph: &[BTreeSet<usize>]) -> Vec<Vec<usize>> {
    let mut next = 0usize;
    let mut indices = vec![None; graph.len()];
    let mut low = vec![0usize; graph.len()];
    let mut on_stack = vec![false; graph.len()];
    let mut stack = vec![];
    let mut cycles = vec![];
    for root in 0..graph.len() {
        if indices[root].is_some() {
            continue;
        }
        indices[root] = Some(next);
        low[root] = next;
        next += 1;
        stack.push(root);
        on_stack[root] = true;
        let mut work = vec![(
            root,
            graph[root].iter().copied().collect::<Vec<_>>(),
            0usize,
        )];
        while let Some((node, neighbors, position)) = work.last_mut() {
            let current = *node;
            if *position < neighbors.len() {
                let target = neighbors[*position];
                *position += 1;
                if indices[target].is_none() {
                    indices[target] = Some(next);
                    low[target] = next;
                    next += 1;
                    stack.push(target);
                    on_stack[target] = true;
                    work.push((target, graph[target].iter().copied().collect(), 0));
                } else if on_stack[target] {
                    low[current] = low[current].min(indices[target].unwrap());
                }
                continue;
            }
            work.pop();
            if let Some((parent, _, _)) = work.last() {
                low[*parent] = low[*parent].min(low[current]);
            }
            if low[current] == indices[current].unwrap() {
                let mut component = vec![];
                while let Some(member) = stack.pop() {
                    on_stack[member] = false;
                    component.push(member);
                    if member == current {
                        break;
                    }
                }
                if component.len() > 1 || graph[current].contains(&current) {
                    cycles.push(component);
                }
            }
        }
    }
    cycles
}
fn actual_path(start: usize, members: &BTreeSet<usize>, graph: &[BTreeSet<usize>]) -> Vec<usize> {
    let mut path = vec![start];
    let mut visiting = BTreeSet::from([start]);
    let mut work = vec![(
        start,
        graph[start]
            .intersection(members)
            .copied()
            .collect::<Vec<_>>(),
        0usize,
    )];
    while let Some((node, neighbors, position)) = work.last_mut() {
        if *position < neighbors.len() {
            let next = neighbors[*position];
            *position += 1;
            if next == start {
                return path;
            }
            if visiting.insert(next) {
                path.push(next);
                work.push((
                    next,
                    graph[next].intersection(members).copied().collect(),
                    0,
                ));
            }
        } else {
            let current = *node;
            work.pop();
            visiting.remove(&current);
            path.pop();
        }
    }
    vec![]
}
pub fn analyze(
    request: &AnalysisRequest,
    files: &[Arc<ParsedFile>],
    output: &mut AnalysisOutput,
    cancellation: Option<&AtomicBool>,
) {
    let index = ModuleIndex::new(request, files);
    let names: Vec<_> = index.by_module.keys().cloned().collect();
    let positions: BTreeMap<_, _> = names
        .iter()
        .enumerate()
        .map(|(position, name)| (name.clone(), position))
        .collect();
    let mut graph = vec![BTreeSet::new(); names.len()];
    let mut heuristic_edges = BTreeSet::new();
    let mut edges = vec![];
    for (module, first, other) in &index.shadowed {
        output.observation.limitations.push(format!("module {module} follows declared source-root precedence: {first}; shadowed file {other}"));
    }
    for file in files {
        if cancelled(output, cancellation) {
            break;
        }
        let Some(module) = index.by_file.get(&file.source.path) else {
            continue;
        };
        if index.by_module.get(module) != Some(&file.source.path) {
            let mut target = InspectionTarget::at(&file.source.path, 1);
            target.target_name = "PythonImportGraph:Shadowed".into();
            target.status = "SKIP".into();
            target.message = "Module shadowed by a preceding declared source root".into();
            output.targets.push(target);
            continue;
        }
        let importer = positions[module];
        let mut collector = Imports {
            file,
            imports: vec![],
            cancellation,
        };
        collector.visit_body(file.body());
        let stdlib = STDLIB
            .get(&file.target.to_string())
            .expect("tested target stdlib inventory");
        for import in collector.imports {
            if cancelled(output, cancellation) {
                break;
            }
            let Some(base) = relative_import(
                module,
                import.module.as_deref(),
                import.level,
                file.source.path.ends_with("/__init__.py") || file.source.path == "__init__.py",
            ) else {
                failure(
                    output,
                    &file.source.path,
                    &SourceError {
                        code: "relative-import-context",
                        message:
                            "Relative import leaves the selected package scope; no edge was guessed"
                                .into(),
                        line: import.line,
                        column: 1,
                    },
                );
                continue;
            };
            let mut candidates = vec![];
            if !import.from {
                if !base.is_empty() {
                    candidates.push(base.clone());
                }
            } else {
                let owner = index.by_module.get(&base).and_then(|path| {
                    files
                        .iter()
                        .find(|candidate| &candidate.source.path == path)
                });
                for name in import.names {
                    let candidate = if base.is_empty() {
                        name.clone()
                    } else {
                        format!("{base}.{name}")
                    };
                    let attribute = owner.is_some_and(|owner| top_level_binding(owner, &name));
                    if name != "*" && positions.contains_key(&candidate) && !attribute {
                        candidates.push(candidate);
                    } else if !base.is_empty() && !(import.level > 0 && &base == module) {
                        candidates.push(base.clone());
                    } else if base.is_empty()
                        && module != "__init__"
                        && positions.contains_key("__init__")
                    {
                        candidates.push("__init__".into());
                    }
                }
            }
            candidates.sort();
            candidates.dedup();
            for candidate in candidates {
                let (targets, heuristic) = if let Some(target) = positions.get(&candidate) {
                    (vec![*target], false)
                } else if import.level > 0
                    || stdlib.contains(candidate.split('.').next().unwrap_or(""))
                {
                    (vec![], false)
                } else {
                    (
                        positions
                            .iter()
                            .filter(|(name, _)| name.ends_with(&format!(".{candidate}")))
                            .map(|(_, position)| *position)
                            .collect::<Vec<_>>(),
                        true,
                    )
                };
                if targets.len() > 1 {
                    failure(
                        output,
                        &file.source.path,
                        &SourceError {
                            code: "ambiguous-import",
                            message: format!(
                                "Imported module {candidate:?} matches multiple selected modules; no edge was guessed"
                            ),
                            line: import.line,
                            column: 1,
                        },
                    );
                    continue;
                }
                for target in targets {
                    graph[importer].insert(target);
                    if heuristic {
                        heuristic_edges.insert((importer, target));
                    }
                    edges.push(json!({"from":module,"to":names[target],"path":file.source.path,"line":import.line,"resolution":if heuristic{"unique-module-suffix"}else{"declared-module"}}));
                }
            }
        }
        if cancelled(output, cancellation) {
            break;
        }
        let mut target = InspectionTarget::at(&file.source.path, 1);
        target.target_name = "PythonImportGraph".into();
        target.message = "Python imports were inspected within the selected module scope".into();
        output.targets.push(target);
    }
    if output.observation.state == ici_domain::TaskState::Cancelled {
        return;
    }
    let cycles = components(&graph);
    let max_reported =
        usize::try_from(number_option(request, "max_reported", 20)).unwrap_or(usize::MAX);
    for (number, members) in cycles.iter().enumerate() {
        if cancelled(output, cancellation) {
            break;
        }
        let member_set: BTreeSet<_> = members.iter().copied().collect();
        // The legacy target starts at the smallest member like the original
        // engine; the modern finding starts at its first SCC member (also its
        // primary location), as the original observation did. Both follow real
        // import edges instead of joining SCC member order.
        let start = *member_set.first().unwrap();
        let path = actual_path(start, &member_set, &graph);
        let modern_path = actual_path(members[0], &member_set, &graph);
        if path.is_empty() {
            failure(
                output,
                ".",
                &SourceError::new(
                    "cycle-path",
                    "A cyclic component had no reproducible edge-following path",
                ),
            );
            continue;
        }
        let closed_chain = |path: &[usize]| {
            let mut closed: Vec<_> = path.iter().map(|member| names[*member].clone()).collect();
            closed.push(names[path[0]].clone());
            closed.join(" -> ")
        };
        let path_names: Vec<_> = path.iter().map(|member| names[*member].clone()).collect();
        let chain = closed_chain(&path);
        let modern_chain = if modern_path.is_empty() {
            chain.clone()
        } else {
            closed_chain(&modern_path)
        };
        let heuristic = heuristic_edges
            .iter()
            .any(|(from, to)| member_set.contains(from) && member_set.contains(to));
        let member_names: Vec<_> = members
            .iter()
            .map(|member| names[*member].clone())
            .collect();
        let spans: Vec<_> = members
            .iter()
            .map(|member| SourceSpan::line(&index.by_module[&names[*member]], 1))
            .collect();
        let mut finding = Finding::new(
            "cycle.import",
            format!("import cycle: {modern_chain}"),
            spans[0].clone(),
            "ici.cycles",
        );
        finding.fingerprint =
            finding::sha1_short("cycle-", &format!("python:{}", member_names.join(":")));
        finding.severity = "medium".into();
        finding.related_locations = spans[1..].to_vec();
        finding.component_id = Some(request.component_id.clone());
        finding.task_id = Some(request.task_id.clone());
        if heuristic {
            finding.evidence = EvidenceLevel::Estimated;
            finding.confidence = "low".into();
            finding.limitations.push("Import resolution includes a unique module-suffix heuristic; runtime search paths were not proven".into());
        }
        output.observation.findings.push(finding);
        if number < max_reported {
            let mut target = InspectionTarget::at(&index.by_module[&path_names[0]], 1);
            target.target_name = format!("Cycle:{}", path.len());
            target.status = "WARN".into();
            target.message = format!("Python import cycle ({} modules): {chain}", path.len());
            target.metrics.insert("modules".into(), json!(path_names));
            output.targets.push(target);
        }
    }
    if !heuristic_edges.is_empty() {
        output.observation.limitations.push("Some absolute imports used the legacy unique project-module suffix heuristic; affected cycles are estimated".into());
    }
    output.observation.measurements.push(Measurement {
        unit: "cycles".into(),
        ..Measurement::measured("cycles_found", cycles.len() as f64)
    });
    output.extra = json!({"total_cycles":cycles.len(),"py_cycles":cycles.len(),"cpp_cycles":0,"module_index":index.by_module,"import_edges":edges,"heuristic_edges":heuristic_edges.len(),"reported_cycles":cycles.len().min(max_reported)});
    output.summary = if cycles.is_empty() {
        "No cyclic dependencies detected".into()
    } else {
        format!("Dependency cycles: {} found", cycles.len())
    };
    finish_status(output, request);
}

fn top_level_binding(file: &ParsedFile, name: &str) -> bool {
    file.body().iter().any(|statement| match statement {
        Stmt::FunctionDef(function) => function.name.as_str() == name,
        Stmt::ClassDef(class) => class.name.as_str() == name,
        Stmt::Assign(assignment) => assignment.targets.iter().any(|target| {
            crate::ast_util::bound_names(target)
                .iter()
                .any(|bound| bound == name)
        }),
        Stmt::AnnAssign(assignment) => {
            assignment.value.is_some()
                && crate::ast_util::bound_names(&assignment.target)
                    .iter()
                    .any(|bound| bound == name)
        }
        Stmt::Import(import) => import.names.iter().any(|alias| {
            alias.asname.as_ref().map_or_else(
                || alias.name.as_str().split('.').next().unwrap_or(""),
                |alias| alias.as_str(),
            ) == name
        }),
        Stmt::ImportFrom(import) => import.names.iter().any(|alias| {
            let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
            local == name
                && !(import.level > 0
                    && import.module.is_none()
                    && alias
                        .asname
                        .as_ref()
                        .is_none_or(|local| local == &alias.name))
        }),
        _ => false,
    })
}
