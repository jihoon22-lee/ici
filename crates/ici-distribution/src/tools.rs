use crate::{
    files,
    model::{self, BundleInput, Error, Manifest, error},
};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ToolRecord {
    pub path: String,
    pub sha256: String,
    pub version: String,
    pub license: String,
    pub target: String,
    pub archive_sha256: String,
    pub source_url: String,
    pub license_sha256: String,
    pub notices: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ToolManifest {
    pub schema_id: String,
    pub schema_version: u32,
    pub tools: BTreeMap<String, ToolRecord>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ToolFiles {
    pub binary: PathBuf,
    pub license: PathBuf,
    pub notices: PathBuf,
}

pub(crate) fn validate(bytes: &[u8], manifest: &Manifest) -> Result<(), Error> {
    let tools: ToolManifest = crate::json::parse(bytes)?;
    if tools.schema_id != "ici.native-tools" || tools.schema_version != 1 || tools.tools.len() != 2
    {
        return Err(error(
            "tool-manifest",
            "Unsupported or incomplete native tool pack",
        ));
    }
    for id in ["ruff", "ty"] {
        let tool = tools
            .tools
            .get(id)
            .ok_or_else(|| error("tool-manifest", "Native tool pack omits a required tool"))?;
        let input = &manifest.files[&format!("tools/bin/{id}")];
        let license = &manifest.files[&format!("licenses/{id}.txt")];
        let url = reqwest::Url::parse(&tool.source_url)
            .map_err(|_| error("tool-manifest", "Native tool source URL is invalid"))?;
        if tool.path != format!("bin/{id}")
            || tool.sha256 != input.sha256
            || tool.license_sha256 != license.sha256
            || !model::valid_version(&tool.version)
            || tool.license != "MIT"
            || tool.target != "x86_64-unknown-linux-musl"
            || !model::valid_digest(&tool.archive_sha256)
            || url.scheme() != "https"
            || !url.username().is_empty()
            || url.password().is_some()
            || tool.notices.is_empty()
            || tool.notices.iter().any(|path| {
                !path.starts_with("licenses/")
                    || !model::safe_path(path)
                    || !manifest.files.contains_key(path)
            })
        {
            return Err(error(
                "tool-manifest",
                "Native tool provenance, license or payload disagrees with the distribution manifest",
            ));
        }
    }
    Ok(())
}
/// Build-time only: pin tools to the independently recorded upstream inputs.
/// The setup installer never downloads or installs a Python package.
pub fn prepare_tool_pack(
    directory: &Path,
    sources: &BTreeMap<String, ToolFiles>,
) -> Result<Vec<BundleInput>, Error> {
    #[derive(Deserialize)]
    struct Pins {
        tools: Vec<Pin>,
    }
    #[derive(Deserialize)]
    struct Pin {
        tool: String,
        version: String,
        target: String,
        url: String,
        archive_sha256: String,
        binary_sha256: String,
        binary_size: u64,
        license_sha256: String,
        license: String,
    }
    let locked = include_bytes!("../../../packaging/native/tool-inputs.json");
    let pins: Pins = crate::json::parse(locked)?;
    let mut tools = BTreeMap::new();
    let mut inputs = vec![];
    for pin in pins.tools {
        let source = sources
            .get(&pin.tool)
            .ok_or_else(|| error("tool-input", "A pinned native tool input is missing"))?;
        let binary = files::read_bytes(&source.binary, model::MAX_FILE_BYTES)?;
        let license = files::read_bytes(&source.license, model::MAX_MANIFEST_BYTES)?;
        if binary.len() as u64 != pin.binary_size
            || model::digest(&binary) != pin.binary_sha256
            || model::digest(&license) != pin.license_sha256
        {
            return Err(error(
                "tool-digest",
                "Native tool executable or upstream license differs from the pinned release",
            ));
        }
        // The ordinary required notices file has its own digest in the outer
        // manifest; it must contain actual resolved dependency text at build time.
        if files::read_bytes(&source.notices, 16 * 1024 * 1024)?.is_empty() {
            return Err(error(
                "tool-license",
                "Native tool third-party notices are empty",
            ));
        }
        let notices = format!("licenses/{}-third-party.txt", pin.tool);
        tools.insert(
            pin.tool.clone(),
            ToolRecord {
                path: format!("bin/{}", pin.tool),
                sha256: pin.binary_sha256,
                version: pin.version,
                license: pin.license,
                target: pin.target,
                archive_sha256: pin.archive_sha256,
                source_url: pin.url,
                license_sha256: pin.license_sha256,
                notices: vec![format!("licenses/{}.txt", pin.tool), notices.clone()],
            },
        );
        inputs.extend([
            BundleInput {
                path: format!("tools/bin/{}", pin.tool),
                source: source.binary.clone(),
                executable: true,
            },
            BundleInput {
                path: format!("licenses/{}.txt", pin.tool),
                source: source.license.clone(),
                executable: false,
            },
            BundleInput {
                path: notices,
                source: source.notices.clone(),
                executable: false,
            },
        ]);
    }
    let manifest = ToolManifest {
        schema_id: "ici.native-tools".into(),
        schema_version: 1,
        tools,
    };
    let path = directory.join("tool-manifest.json");
    files::write_new(
        &path,
        &serde_json::to_vec_pretty(&manifest)
            .map_err(|_| error("tool-manifest", "Native tool manifest cannot be serialized"))?,
        false,
    )?;
    inputs.push(BundleInput {
        path: "tools/manifest.json".into(),
        source: path,
        executable: false,
    });
    let path = directory.join("tool-inputs.json");
    files::write_new(&path, locked, false)?;
    inputs.push(BundleInput {
        path: "licenses/tool-inputs.json".into(),
        source: path,
        executable: false,
    });
    Ok(inputs)
}
