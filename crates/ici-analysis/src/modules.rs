//! Module naming from declared, ordered source roots; never from a runtime import.
use crate::parser::ParsedFile;
use ici_domain::AnalysisRequest;
use std::collections::BTreeMap;
use std::sync::Arc;

pub struct ModuleIndex {
    pub by_file: BTreeMap<String, String>,
    pub by_module: BTreeMap<String, String>,
    pub shadowed: Vec<(String, String, String)>,
}
fn normalized_root(value: &str) -> String {
    value
        .split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .collect::<Vec<_>>()
        .join("/")
}

pub fn roots(request: &AnalysisRequest) -> Vec<String> {
    let mut roots: Vec<_> = request
        .options
        .get("source_roots")
        .and_then(|value| value.as_array())
        .map_or_else(Vec::new, |values| {
            values
                .iter()
                .filter_map(|value| value.as_str())
                .map(normalized_root)
                .map(|value| if value == "." { String::new() } else { value })
                .collect()
        });
    if roots.is_empty() {
        let root = request
            .options
            .get("component_root")
            .and_then(|value| value.as_str())
            .unwrap_or("");
        roots.push(if root == "." {
            String::new()
        } else {
            normalized_root(root)
        });
    }
    roots
}
fn name(path: &str, roots: &[String]) -> (usize, String) {
    let (precedence, relative) = roots
        .iter()
        .enumerate()
        .find_map(|(index, root)| {
            if root.is_empty() {
                Some((index, path))
            } else {
                path.strip_prefix(&format!("{root}/"))
                    .map(|relative| (index, relative))
            }
        })
        .unwrap_or((roots.len(), path.rsplit('/').next().unwrap_or(path)));
    let stem = relative
        .strip_suffix(".pyi")
        .or_else(|| relative.strip_suffix(".py"))
        .unwrap_or(relative);
    let stem = stem.strip_suffix("/__init__").unwrap_or(stem);
    (precedence, stem.replace('/', "."))
}
impl ModuleIndex {
    pub fn new(request: &AnalysisRequest, files: &[Arc<ParsedFile>]) -> Self {
        let roots = roots(request);
        let mut ordered: Vec<_> = files
            .iter()
            .map(|file| {
                let (precedence, module) = name(&file.source.path, &roots);
                (precedence, file.source.path.clone(), module)
            })
            .collect();
        ordered.sort();
        let mut index = Self {
            by_file: BTreeMap::new(),
            by_module: BTreeMap::new(),
            shadowed: vec![],
        };
        for (_, path, module) in ordered {
            index.by_file.insert(path.clone(), module.clone());
            if let Some(first) = index.by_module.get(&module) {
                index.shadowed.push((module, first.clone(), path));
            } else {
                index.by_module.insert(module, path);
            }
        }
        index
    }
}
pub fn relative_import(
    current: &str,
    module: Option<&str>,
    level: u32,
    is_package: bool,
) -> Option<String> {
    if level == 0 {
        return Some(module.unwrap_or("").to_owned());
    }
    let mut package: Vec<_> = if current == "__init__" {
        vec![]
    } else {
        current.split('.').collect()
    };
    if !is_package {
        package.pop();
    }
    let ascend = level.saturating_sub(1) as usize;
    if (package.is_empty() && level > 1) || (!package.is_empty() && level as usize > package.len())
    {
        return None;
    }
    package.truncate(package.len() - ascend);
    if let Some(module) = module
        && !module.is_empty()
    {
        package.extend(module.split('.'));
    }
    Some(package.join("."))
}
