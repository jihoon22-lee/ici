//! Native build/setup utility. Only the ici core and pinned tools enter the product archive.
use ici_distribution::{self as distribution, BuildInputs, BundleInput, LicenseSource, ToolFiles};
use serde::de::DeserializeOwned;
use serde_json::Value;
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
    process::ExitCode,
    time::Duration,
};

fn read_json<T: DeserializeOwned>(path: &str) -> Result<T, String> {
    let file = std::fs::File::open(path).map_err(|_| "Build input JSON could not be opened")?;
    if !file
        .metadata()
        .map_err(|_| "Build input metadata could not be read")?
        .is_file()
        || file.metadata().unwrap().len() > 32 * 1024 * 1024
    {
        return Err("Build input JSON is not regular or exceeds its bound".into());
    }
    serde_json::from_reader(file).map_err(|_| "Build input JSON does not match its schema".into())
}
fn arguments() -> Result<(String, BTreeMap<String, String>), String> {
    let mut args = std::env::args().skip(1);
    let command=args.next().ok_or("Expected bundle, tool-pack, notices, inspect, install, activate, rollback, status or download")?;
    let mut values = BTreeMap::new();
    while let Some(key) = args.next() {
        if !key.starts_with("--") || values.contains_key(&key) {
            return Err("Build/setup option is invalid or duplicated".into());
        }
        values.insert(
            key,
            args.next().ok_or("Build/setup option requires a value")?,
        );
    }
    Ok((command, values))
}
fn option<'a>(values: &'a BTreeMap<String, String>, name: &str) -> Result<&'a str, String> {
    values
        .get(name)
        .map(String::as_str)
        .ok_or_else(|| format!("Required option {name} is missing"))
}
fn output_new(path: &Path, bytes: &[u8]) -> Result<(), String> {
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|_| "Output already exists or could not be created")?;
    file.write_all(bytes)
        .and_then(|()| file.sync_all())
        .map_err(|_| "Output could not be committed".into())
}
fn run() -> Result<Value, String> {
    let (command, values) = arguments()?;
    let value = match command.as_str() {
        "bundle" => {
            let inputs: BuildInputs = read_json(option(&values, "--inputs")?)?;
            let files: Vec<BundleInput> = read_json(option(&values, "--files")?)?;
            serde_json::to_value(
                distribution::create_bundle(
                    option(&values, "--version")?,
                    inputs,
                    &files,
                    Path::new(option(&values, "--output")?),
                )
                .map_err(|error| error.to_string())?,
            )
        }
        "tool-pack" => {
            let sources: BTreeMap<String, ToolFiles> = read_json(option(&values, "--sources")?)?;
            let files = distribution::prepare_tool_pack(
                Path::new(option(&values, "--directory")?),
                &sources,
            )
            .map_err(|error| error.to_string())?;
            output_new(
                Path::new(option(&values, "--output")?),
                &serde_json::to_vec_pretty(&files)
                    .map_err(|_| "Tool inputs cannot be serialized")?,
            )?;
            serde_json::to_value(files)
        }
        "notices" => {
            let extras: Vec<LicenseSource> = if let Some(path) = values.get("--extras") {
                read_json(path)?
            } else {
                vec![]
            };
            serde_json::to_value(
                distribution::collect_licenses(
                    Path::new(option(&values, "--metadata")?),
                    option(&values, "--package")?,
                    option(&values, "--target")?,
                    &extras,
                    Path::new(option(&values, "--output")?),
                    Path::new(option(&values, "--inventory")?),
                )
                .map_err(|error| error.to_string())?,
            )
        }
        "inspect" => serde_json::to_value(
            distribution::inspect_bundle(
                Path::new(option(&values, "--archive")?),
                option(&values, "--sha256")?,
            )
            .map_err(|error| error.to_string())?,
        ),
        "install" => serde_json::to_value(
            distribution::install(
                Path::new(option(&values, "--archive")?),
                option(&values, "--sha256")?,
                Path::new(option(&values, "--prefix")?),
            )
            .map_err(|error| error.to_string())?,
        ),
        "activate" => serde_json::to_value(
            distribution::activate(
                Path::new(option(&values, "--prefix")?),
                option(&values, "--version")?,
            )
            .map_err(|error| error.to_string())?,
        ),
        "rollback" => serde_json::to_value(
            distribution::rollback(Path::new(option(&values, "--prefix")?))
                .map_err(|error| error.to_string())?,
        ),
        "status" => serde_json::to_value(
            distribution::inspect_installation(Path::new(option(&values, "--prefix")?))
                .map_err(|error| error.to_string())?,
        ),
        "download" => {
            let maximum = values
                .get("--max-bytes")
                .map(|value| value.parse::<usize>())
                .transpose()
                .map_err(|_| "Download byte limit is invalid")?
                .unwrap_or(256 * 1024 * 1024);
            let bytes = distribution::download(
                option(&values, "--url")?,
                option(&values, "--sha256")?,
                maximum,
                Duration::from_secs(300),
            )
            .map_err(|error| error.to_string())?;
            output_new(&PathBuf::from(option(&values, "--output")?), &bytes)?;
            Ok(
                serde_json::json!({"downloaded_bytes":bytes.len(),"sha256":option(&values,"--sha256")?}),
            )
        }
        _ => return Err("Unknown native build/setup operation".into()),
    };
    value.map_err(|_| "Native build/setup result cannot be serialized".into())
}
fn main() -> ExitCode {
    if std::env::args().nth(1).as_deref() == Some("--version") {
        println!("ici-distribution {}", env!("CARGO_PKG_VERSION"));
        return ExitCode::SUCCESS;
    }
    match run() {
        Ok(value) => {
            println!(
                "{}",
                serde_json::to_string_pretty(&value).expect("serialized JSON")
            );
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("{error}");
            ExitCode::from(2)
        }
    }
}
