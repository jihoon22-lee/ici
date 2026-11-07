//! Build-time license evidence from the actual normal/build target closure.
use crate::{
    files,
    model::{Error, digest, error, io_error},
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LicenseFile {
    pub path: String,
    pub sha256: String,
    pub size: u64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PackageLicense {
    pub authors: Vec<String>,
    pub roles: Vec<String>,
    pub evidence_status: String,
    pub upstream_evidence: BTreeMap<String, String>,
    pub name: String,
    pub version: String,
    pub source: String,
    pub declared_license: String,
    pub files: Vec<LicenseFile>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AdditionalLicense {
    pub id: String,
    pub source: String,
    pub declared_license: String,
    pub sha256: String,
    pub size: u64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LicenseInventory {
    pub schema_id: String,
    pub schema_version: u32,
    pub root_package: String,
    pub target: String,
    pub packages: Vec<PackageLicense>,
    pub additional: Vec<AdditionalLicense>,
    pub notices_sha256: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LicenseSource {
    pub id: String,
    pub source: String,
    pub declared_license: String,
    pub path: PathBuf,
}

fn license_name(path: &Path) -> bool {
    let name = path
        .file_name()
        .unwrap_or_default()
        .to_string_lossy()
        .to_ascii_uppercase();
    ["LICENSE", "LICENCE", "COPYING", "COPYRIGHT", "NOTICE"]
        .iter()
        .any(|prefix| name.starts_with(prefix))
}
#[derive(Deserialize)]
struct RetainedNotice {
    name: String,
    version: String,
    vcs_revision: String,
    repository: String,
    files: Vec<RetainedText>,
}
#[derive(Deserialize)]
struct RetainedText {
    path: String,
    source_url: String,
    sha256: String,
    text: String,
}
fn retained_notice(package: &PackageLicense) -> Result<Option<Vec<RetainedText>>, Error> {
    let notices: Vec<RetainedNotice> = crate::json::parse(include_bytes!(
        "../../../packaging/native/upstream-notices.json"
    ))?;
    for notice in notices {
        if notice.name == package.name && notice.version == package.version {
            if package.upstream_evidence.get("package_vcs_revision") != Some(&notice.vcs_revision)
                || package.upstream_evidence.get("repository") != Some(&notice.repository)
            {
                return Err(error(
                    "license-revision",
                    "Retained upstream notice does not match the package's actual source revision",
                ));
            }
            for file in &notice.files {
                if digest(file.text.as_bytes()) != file.sha256
                    || !file.source_url.starts_with("https://")
                {
                    return Err(error(
                        "license-digest",
                        "Retained upstream license text is inconsistent",
                    ));
                }
            }
            return Ok(Some(notice.files));
        }
    }
    Ok(None)
}
fn discover(
    root: &Path,
    files_out: &mut BTreeSet<PathBuf>,
    count: &mut usize,
) -> Result<(), Error> {
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory).map_err(io_error)? {
            *count += 1;
            if *count > 500_000 {
                return Err(error(
                    "license-bound",
                    "Dependency license discovery exceeds its input inventory bound",
                ));
            }
            let entry = entry.map_err(io_error)?;
            let kind = entry.file_type().map_err(io_error)?;
            let path = entry.path();
            if kind.is_dir() {
                if !matches!(
                    entry.file_name().to_str(),
                    Some(".git" | "target" | "node_modules" | ".venv")
                ) {
                    pending.push(path);
                }
            } else if kind.is_file() && license_name(&path) {
                files_out.insert(path);
            }
        }
    }
    Ok(())
}
fn source_root(package: &Value, workspace: &Path) -> Result<PathBuf, Error> {
    let manifest = Path::new(
        package["manifest_path"]
            .as_str()
            .ok_or_else(|| error("license-metadata", "Dependency has no manifest path"))?,
    );
    let package_root = manifest
        .parent()
        .ok_or_else(|| {
            error(
                "license-metadata",
                "Dependency manifest has no package root",
            )
        })?
        .canonicalize()
        .map_err(io_error)?;
    let source = package["source"].as_str().unwrap_or("");
    if source.is_empty() {
        let workspace = workspace.canonicalize().map_err(io_error)?;
        if !package_root.starts_with(&workspace) {
            return Err(error(
                "license-path",
                "Workspace package escapes its source root",
            ));
        }
        return Ok(workspace);
    }
    if source.starts_with("registry+") {
        return Ok(package_root);
    }
    if source.starts_with("git+") {
        return package_root
            .ancestors()
            .find(|path| path.join(".git").exists())
            .map(Path::to_owned)
            .ok_or_else(|| {
                error(
                    "license-path",
                    "Git dependency has no bounded checkout root",
                )
            });
    }
    Err(error(
        "license-source",
        "Unsupported dependency source scheme",
    ))
}
fn package_files(
    package: &Value,
    workspace: &Path,
    count: &mut usize,
) -> Result<(PathBuf, BTreeSet<PathBuf>), Error> {
    let manifest = Path::new(package["manifest_path"].as_str().unwrap());
    let root = manifest
        .parent()
        .unwrap()
        .canonicalize()
        .map_err(io_error)?;
    let allowed = source_root(package, workspace)?;
    let mut paths = BTreeSet::new();
    discover(&root, &mut paths, count)?;
    if let Some(path) = package["license_file"].as_str() {
        let path = root.join(path).canonicalize().map_err(io_error)?;
        if !path.starts_with(&allowed) {
            return Err(error(
                "license-path",
                "Dependency license_file escapes its source checkout",
            ));
        }
        paths.insert(path);
    }
    {
        for ancestor in root.ancestors() {
            if !ancestor.starts_with(&allowed) {
                break;
            }
            for entry in fs::read_dir(ancestor).map_err(io_error)? {
                let entry = entry.map_err(io_error)?;
                if entry.file_type().map_err(io_error)?.is_file() && license_name(&entry.path()) {
                    paths.insert(entry.path());
                }
            }
            if paths.iter().any(|path| path.parent() == Some(ancestor)) {
                break;
            }
        }
    }
    if package["source"].is_null() && package["license"] == "MIT" && allowed == workspace {
        let canonical = workspace.join("packaging/native/LICENSE-ici-MIT.txt");
        if canonical.is_file() {
            paths.insert(canonical);
        }
    }
    if paths.is_empty() {
        return Err(error(
            "license-missing",
            format!(
                "No actual license/notice file found for {} {}",
                package["name"].as_str().unwrap_or("dependency"),
                package["version"].as_str().unwrap_or("")
            ),
        ));
    }
    Ok((allowed, paths))
}
/// `metadata` must come from cargo metadata --locked --filter-platform TARGET.
/// Dev-only edges are excluded. Source license texts, hashes and inventory are retained.
pub fn collect_licenses(
    metadata: &Path,
    root_package: &str,
    target: &str,
    extra: &[LicenseSource],
    notices_output: &Path,
    inventory_output: &Path,
) -> Result<LicenseInventory, Error> {
    let metadata: Value = crate::json::parse(&files::read_bytes(metadata, 32 * 1024 * 1024)?)?;
    let packages = metadata["packages"]
        .as_array()
        .ok_or_else(|| error("license-metadata", "Cargo metadata has no packages"))?;
    let all: BTreeMap<_, _> = packages
        .iter()
        .filter_map(|package| Some((package["id"].as_str()?, package)))
        .collect();
    let root = packages
        .iter()
        .filter(|package| package["name"] == root_package)
        .collect::<Vec<_>>();
    if root.len() != 1 {
        return Err(error(
            "license-metadata",
            "Requested Cargo root package is missing or ambiguous",
        ));
    }
    let nodes: BTreeMap<_, _> = metadata["resolve"]["nodes"]
        .as_array()
        .ok_or_else(|| {
            error(
                "license-metadata",
                "Cargo metadata has no resolved dependency graph",
            )
        })?
        .iter()
        .filter_map(|node| Some((node["id"].as_str()?, node)))
        .collect();
    let mut pending = vec![(root[0]["id"].as_str().unwrap(), "product_root")];
    let mut selected = BTreeSet::new();
    let mut roles: BTreeMap<&str, BTreeSet<String>> = BTreeMap::new();
    while let Some((id, role)) = pending.pop() {
        if !selected.insert((id, role)) {
            continue;
        }
        roles.entry(id).or_default().insert(role.into());
        let node = nodes
            .get(id)
            .ok_or_else(|| error("license-metadata", "Resolved dependency node is missing"))?;
        for dependency in node["deps"]
            .as_array()
            .ok_or_else(|| error("license-metadata", "Resolved dependency edges are missing"))?
        {
            let dependency_id = dependency["pkg"].as_str().ok_or_else(|| {
                error(
                    "license-metadata",
                    "Resolved dependency has no package identity",
                )
            })?;
            if let Some(kinds) = dependency["dep_kinds"].as_array() {
                for kind in kinds {
                    if kind["kind"] == "dev" {
                        continue;
                    }
                    let package = all.get(dependency_id).ok_or_else(|| {
                        error("license-metadata", "Resolved dependency package is missing")
                    })?;
                    let macro_host = package["targets"].as_array().is_some_and(|targets| {
                        targets.iter().any(|target| {
                            target["kind"]
                                .as_array()
                                .is_some_and(|kinds| kinds.iter().any(|kind| kind == "proc-macro"))
                        })
                    });
                    let child_role = if macro_host {
                        "proc_macro_host"
                    } else if kind["kind"] == "build" {
                        "build_dependency"
                    } else if matches!(role, "product_root" | "target_dependency") {
                        "target_dependency"
                    } else {
                        "host_dependency"
                    };
                    pending.push((dependency_id, child_role));
                }
            }
        }
    }
    let workspace = Path::new(
        metadata["workspace_root"]
            .as_str()
            .ok_or_else(|| error("license-metadata", "Cargo metadata has no workspace root"))?,
    );
    let mut notices = String::from(
        "ici native distribution third-party notices\n\nThe following records retain actual license, copyright and notice text for the resolved normal/build target dependency closure. License expressions describe their upstream declarations. Build-only records do not assert that all listed code is linked into the executable.\n\n",
    );
    let mut records = vec![];
    let mut inventory_count = 0;
    for (id, package_roles) in roles {
        let package = all
            .get(id)
            .ok_or_else(|| error("license-metadata", "Resolved package is missing"))?;
        let name = package["name"]
            .as_str()
            .ok_or_else(|| error("license-metadata", "Package has no name"))?
            .to_owned();
        let version = package["version"]
            .as_str()
            .ok_or_else(|| error("license-metadata", "Package has no version"))?
            .to_owned();
        let source = package["source"].as_str().unwrap_or("workspace").to_owned();
        if let Some((_, url)) = source.split_once('+') {
            let url = reqwest::Url::parse(url)
                .map_err(|_| error("license-source", "Dependency source URL cannot be decoded"))?;
            if !url.username().is_empty() || url.password().is_some() {
                return Err(error(
                    "license-source",
                    "Credential-bearing dependency URLs cannot be placed in notices",
                ));
            }
        }
        let declared_license = package["license"]
            .as_str()
            .filter(|value| !value.is_empty())
            .ok_or_else(|| {
                error(
                    "license-missing",
                    format!("Dependency {name} {version} has no declared license"),
                )
            })?
            .to_owned();
        let discovered = package_files(package, workspace, &mut inventory_count);
        let (allowed, paths, missing) = match discovered {
            Ok((allowed, paths)) => (allowed, paths, false),
            Err(error) if error.code == "license-missing" => {
                let root = Path::new(package["manifest_path"].as_str().unwrap())
                    .parent()
                    .unwrap()
                    .to_path_buf();
                let mut paths = BTreeSet::from([root.join("Cargo.toml.orig")]);
                if root.join("README.md").is_file() {
                    paths.insert(root.join("README.md"));
                }
                (root, paths, true)
            }
            Err(error) => return Err(error),
        };
        let mut upstream_evidence = BTreeMap::new();
        if let Some(repository) = package["repository"].as_str() {
            upstream_evidence.insert("repository".into(), repository.into());
        }
        let vcs_path = Path::new(package["manifest_path"].as_str().unwrap())
            .parent()
            .unwrap()
            .join(".cargo_vcs_info.json");
        if vcs_path.is_file() {
            let vcs: Value = crate::json::parse(&files::read_bytes(&vcs_path, 4096)?)?;
            if let Some(revision) = vcs["git"]["sha1"].as_str() {
                upstream_evidence.insert("package_vcs_revision".into(), revision.into());
            }
            if let Some(path) = vcs["path_in_vcs"].as_str() {
                upstream_evidence.insert("path_in_vcs".into(), path.into());
            }
        }
        let mut record = PackageLicense {
            name,
            version,
            source,
            declared_license,
            authors: package["authors"]
                .as_array()
                .map_or_else(Vec::new, |authors| {
                    authors
                        .iter()
                        .filter_map(|author| author.as_str().map(str::to_owned))
                        .collect()
                }),
            roles: package_roles.into_iter().collect(),
            evidence_status: if missing {
                "missing_upstream_notice"
            } else {
                "source_notices_retained"
            }
            .into(),
            upstream_evidence,
            files: vec![],
        };
        let mut texts = vec![];
        if missing && let Some(retained) = retained_notice(&record)? {
            record.evidence_status = "upstream_source_notices_retained".into();
            for file in retained {
                record.upstream_evidence.insert(
                    format!("retained_notice_url.{}", file.path),
                    file.source_url,
                );
                let path = format!("upstream/{}", file.path);
                record.files.push(LicenseFile {
                    path: path.clone(),
                    sha256: file.sha256,
                    size: file.text.len() as u64,
                });
                texts.push((path, file.text));
            }
        }
        for path in paths {
            let text = files::read_bytes(&path, 4 * 1024 * 1024)?;
            let relative = path
                .strip_prefix(&allowed)
                .map_err(|_| error("license-path", "License path escapes its dependency source"))?
                .to_string_lossy()
                .into_owned();
            let text = String::from_utf8(text)
                .map_err(|_| error("license-encoding", "Dependency notice is not UTF-8"))?;
            record.files.push(LicenseFile {
                path: relative.clone(),
                sha256: digest(text.as_bytes()),
                size: text.len() as u64,
            });
            texts.push((relative, text));
        }
        record.files.sort_by(|a, b| a.path.cmp(&b.path));
        texts.sort_by(|a, b| a.0.cmp(&b.0));
        records.push((record, texts));
    }
    records.sort_by(|a, b| {
        (&a.0.name, &a.0.version, &a.0.source).cmp(&(&b.0.name, &b.0.version, &b.0.source))
    });
    let mut packages = vec![];
    for (record, texts) in records {
        notices.push_str(&format!("\n===== {} {} =====\nSource: {}\nDeclared license: {}\nEvidence: {}\nDependency roles: {}\nUpstream authors: {}\n",record.name,record.version,record.source,record.declared_license,record.evidence_status,record.roles.join(", "),if record.authors.is_empty(){"not declared".into()}else{record.authors.join(", ")}));
        for (key, value) in &record.upstream_evidence {
            notices.push_str(&format!("{key}: {value}\n"));
        }
        if record.evidence_status == "missing_upstream_notice" {
            notices.push_str("\nThe pinned upstream package/revision supplies a license declaration but no separate license/copyright/notice file. The actual declaration and README follow. No upstream copyright notice is invented, and this gap is not labeled fully closed.\n");
            if record.declared_license == "MIT" {
                let standard = include_str!("../../../packaging/native/LICENSE-ici-MIT.txt");
                let terms = standard
                    .find("Permission is hereby granted")
                    .map(|offset| &standard[offset..])
                    .ok_or_else(|| {
                        error("license-reference", "Standard MIT reference is unavailable")
                    })?;
                notices.push_str(&format!("\nSPDX MIT standard terms, declaration reference only (no inferred upstream copyright):\n{terms}\n"));
            }
        }
        for (path, text) in texts {
            notices.push_str(&format!("\n--- {path} ---\n{text}\n"));
        }
        packages.push(record);
        if notices.len() > 64 * 1024 * 1024 {
            return Err(error(
                "license-bound",
                "Aggregated dependency notices exceed 64 MiB",
            ));
        }
    }
    let mut additional = vec![];
    let mut extras = extra.iter().collect::<Vec<_>>();
    extras.sort_by_key(|source| &source.id);
    let mut seen = BTreeSet::new();
    for source in extras {
        if !seen.insert(&source.id) {
            return Err(error(
                "license-extra",
                "Additional license identifiers must be unique",
            ));
        }
        let text = files::read_bytes(&source.path, 16 * 1024 * 1024)?;
        let text = String::from_utf8(text)
            .map_err(|_| error("license-encoding", "Additional license is not UTF-8"))?;
        additional.push(AdditionalLicense {
            id: source.id.clone(),
            source: source.source.clone(),
            declared_license: source.declared_license.clone(),
            sha256: digest(text.as_bytes()),
            size: text.len() as u64,
        });
        notices.push_str(&format!(
            "\n===== {} =====\nSource: {}\nDeclared license: {}\n\n{text}\n",
            source.id, source.source, source.declared_license
        ));
        if notices.len() > 64 * 1024 * 1024 {
            return Err(error(
                "license-bound",
                "Aggregated dependency notices exceed 64 MiB",
            ));
        }
    }
    let inventory = LicenseInventory {
        schema_id: "ici.native-licenses".into(),
        schema_version: 1,
        root_package: root_package.into(),
        target: target.into(),
        packages,
        additional,
        notices_sha256: digest(notices.as_bytes()),
    };
    files::write_new(notices_output, notices.as_bytes(), false)?;
    files::write_new(
        inventory_output,
        &serde_json::to_vec_pretty(&inventory).map_err(|_| {
            error(
                "license-inventory",
                "License inventory cannot be serialized",
            )
        })?,
        false,
    )?;
    Ok(inventory)
}
