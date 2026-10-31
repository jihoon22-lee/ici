//! Bounded declared artifact inventories. Directory symlinks are never traversed.
use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
};
#[derive(Clone, Debug)]
pub struct TreeEntry {
    pub relative: String,
    pub file: bool,
    pub contained: bool,
}
pub fn tree(root: &Path, directory: &Path) -> std::io::Result<Vec<TreeEntry>> {
    inventory(root, directory, false)
}
/// Source preparation records bounded inputs without descending into tool or build state.
pub fn build_inputs(root: &Path, directory: &Path) -> std::io::Result<Vec<String>> {
    Ok(inventory(root, directory, true)?
        .into_iter()
        .filter(|entry| {
            entry.file
                && entry.contained
                && root
                    .join(&entry.relative)
                    .canonicalize()
                    .is_ok_and(|physical| physical == root.join(&entry.relative))
        })
        .map(|entry| entry.relative)
        .collect())
}
fn inventory(
    root: &Path,
    directory: &Path,
    source_inputs: bool,
) -> std::io::Result<Vec<TreeEntry>> {
    let root = root.canonicalize()?;
    let mut pending = vec![(directory.to_owned(), 0usize)];
    let mut visited = BTreeSet::new();
    let mut entries = vec![];
    while let Some((directory, depth)) = pending.pop() {
        if depth > 64 {
            return Err(std::io::Error::other(
                "artifact inventory exceeds 64 directory levels",
            ));
        }
        let canonical = directory.canonicalize()?;
        if !canonical.starts_with(&root) {
            return Err(std::io::Error::other(
                "declared artifact directory leaves workspace",
            ));
        }
        if !visited.insert(canonical) {
            continue;
        }
        let mut children = fs::read_dir(&directory)?.collect::<std::io::Result<Vec<_>>>()?;
        children.sort_by_key(|entry| entry.file_name());
        for child in children {
            if entries.len() >= 100_000 {
                return Err(std::io::Error::other(
                    "artifact inventory exceeds 100000 entries",
                ));
            }
            let path = child.path();
            let kind = child.file_type()?;
            if source_inputs
                && kind.is_dir()
                && [
                    ".git",
                    ".hg",
                    ".svn",
                    ".ici",
                    ".ici-native",
                    ".venv",
                    "venv",
                    "node_modules",
                    "__pycache__",
                    "target",
                    "build",
                    "dist",
                ]
                .iter()
                .any(|name| child.file_name() == *name)
            {
                continue;
            }
            let relative = path
                .strip_prefix(&root)
                .map_err(|_| std::io::Error::other("artifact path leaves workspace"))?
                .to_str()
                .ok_or_else(|| std::io::Error::other("artifact path is not UTF-8"))?
                .replace('\\', "/");
            let contained = path
                .canonicalize()
                .is_ok_and(|path| path.starts_with(&root));
            let file = if kind.is_symlink() {
                contained && path.is_file()
            } else {
                kind.is_file()
            };
            entries.push(TreeEntry {
                relative,
                file,
                contained,
            });
            if kind.is_dir() && contained {
                pending.push((path, depth + 1));
            }
        }
    }
    entries.sort_by(|left, right| left.relative.cmp(&right.relative));
    Ok(entries)
}
pub fn under(root: &Path, relative: &str) -> PathBuf {
    if relative == "." {
        root.to_owned()
    } else {
        root.join(relative)
    }
}
