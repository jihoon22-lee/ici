use ici_distribution::{
    self as distribution, BuildInputs, BundleIdentity, BundleInput, ToolManifest, ToolRecord,
};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::{Cursor, Read},
    path::{Path, PathBuf},
    process::Command,
    sync::OnceLock,
};

fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
struct Executables {
    _temporary: tempfile::TempDir,
    first: PathBuf,
    second: PathBuf,
}
fn executables() -> &'static Executables {
    static BINARIES: OnceLock<Executables> = OnceLock::new();
    BINARIES.get_or_init(||{
        let temporary=tempfile::tempdir().unwrap();
        let mut binaries=vec![];
        for version in ["0.14.0-fixture.1","0.14.0-fixture.2"] {
            let source=temporary.path().join(format!("{version}.rs"));
            fs::write(&source,format!(r#"fn main() {{
let arg=std::env::args().next().unwrap();
let name=std::path::Path::new(&arg).file_name().unwrap().to_str().unwrap();
if name=="ruff" {{println!("ruff 0.16.4");}}else if name=="ty" {{println!("ty 0.0.93");}}else{{println!("ici {version}");}}
}}"#)).unwrap();
            let output=temporary.path().join(version);
            let status=Command::new(std::env::var_os("RUSTC").unwrap_or_else(||"rustc".into())).args(["--crate-name","distribution_fixture","--edition=2024","-C","opt-level=1","-C","strip=symbols","-C","codegen-units=1","-C","target-feature=+crt-static","-C","target-cpu=x86-64"]).arg(&source).arg("-o").arg(&output).status().unwrap();
            assert!(status.success(),"A real static x86_64 fixture is required for installation tests");
            binaries.push(output);
        }
        Executables{first:binaries.remove(0),second:binaries.remove(0),_temporary:temporary}
    })
}
fn inputs() -> BuildInputs {
    BuildInputs {
        source_revision: "0".repeat(40),
        source_tree_sha256: "a".repeat(64),
        cargo_lock_sha256: "b".repeat(64),
        rust_toolchain: "1.99.1".into(),
        target: "x86_64-unknown-linux-gnu".into(),
        profile: "release".into(),
        source_date_epoch: 1234,
        rust_flags: vec!["-Ctarget-cpu=x86-64".into()],
        platform_evidence: BTreeMap::from([(
            "status".into(),
            "synthetic unit fixture; actual product acceptance is separate".into(),
        )]),
    }
}
fn bundle(root: &Path, name: &str, version: &str, binary: &Path) -> BundleIdentity {
    let files = root.join(format!("{name}-inputs"));
    fs::create_dir(&files).unwrap();
    let binary_hash = hash(&fs::read(binary).unwrap());
    let license = b"MIT fixture license; actual upstream licenses are used by the build acceptance";
    let license_hash = hash(license);
    let tools = ToolManifest {
        schema_id: "ici.native-tools".into(),
        schema_version: 1,
        tools: ["ruff", "ty"]
            .into_iter()
            .map(|id| {
                (
                    id.into(),
                    ToolRecord {
                        path: format!("bin/{id}"),
                        sha256: binary_hash.clone(),
                        version: if id == "ruff" { "0.16.4" } else { "0.0.93" }.into(),
                        license: "MIT".into(),
                        target: "x86_64-unknown-linux-musl".into(),
                        archive_sha256: "f".repeat(64),
                        source_url: format!("https://example.invalid/{id}.tar.gz"),
                        license_sha256: license_hash.clone(),
                        notices: vec!["licenses/THIRD-PARTY-NOTICES.txt".into()],
                    },
                )
            })
            .collect(),
    };
    let mut sources = vec![];
    for path in ["bin/ici", "tools/bin/ruff", "tools/bin/ty"] {
        sources.push(BundleInput {
            path: path.into(),
            source: binary.into(),
            executable: true,
        });
    }
    for path in [
        "licenses/ici-MIT.txt",
        "licenses/Unicode-V3.txt",
        "licenses/ruff.txt",
        "licenses/ty.txt",
        "licenses/THIRD-PARTY-NOTICES.txt",
        "README.txt",
    ] {
        let source = files.join(path.replace('/', "_"));
        fs::write(&source, license).unwrap();
        sources.push(BundleInput {
            path: path.into(),
            source,
            executable: false,
        });
    }
    let source = files.join("tools.json");
    fs::write(&source, serde_json::to_vec(&tools).unwrap()).unwrap();
    sources.push(BundleInput {
        path: "tools/manifest.json".into(),
        source,
        executable: false,
    });
    distribution::create_bundle(
        version,
        inputs(),
        &sources,
        &root.join(format!("{name}.tar.gz")),
    )
    .unwrap()
}
#[test]
fn identical_inputs_produce_identical_bytes_and_no_existing_output_is_overwritten() {
    let root = tempfile::tempdir().unwrap();
    let binary = &executables().first;
    let first = bundle(root.path(), "first", "0.14.0-fixture.1", binary);
    let second = bundle(root.path(), "second", "0.14.0-fixture.1", binary);
    assert_eq!(first.sha256, second.sha256);
    assert_eq!(
        fs::read(&first.path).unwrap(),
        fs::read(&second.path).unwrap()
    );
    assert_eq!(
        distribution::inspect_bundle(&first.path, &first.sha256).unwrap(),
        first.manifest
    );
    assert!(distribution::inspect_bundle(&first.path, &"0".repeat(64)).is_err());
    assert!(distribution::create_bundle("0.14.0-fixture.1", inputs(), &[], &first.path).is_err());
    assert_eq!(hash(&fs::read(first.path).unwrap()), first.sha256);
}

#[test]
fn dynamic_elf_inputs_are_rejected_before_a_static_bundle_is_created() {
    let root = tempfile::tempdir().unwrap();
    for (case, program_kind) in [("interpreter", 3u32), ("shared-library", 2u32)] {
        let mut bytes = vec![0u8; 144];
        bytes[..6].copy_from_slice(b"\x7fELF\x02\x01");
        bytes[18..20].copy_from_slice(&62u16.to_le_bytes());
        bytes[32..40].copy_from_slice(&64u64.to_le_bytes());
        bytes[54..56].copy_from_slice(&56u16.to_le_bytes());
        bytes[56..58].copy_from_slice(&1u16.to_le_bytes());
        bytes[64..68].copy_from_slice(&program_kind.to_le_bytes());
        if program_kind == 2 {
            bytes[72..80].copy_from_slice(&128u64.to_le_bytes());
            bytes[96..104].copy_from_slice(&16u64.to_le_bytes());
            bytes[128..136].copy_from_slice(&1u64.to_le_bytes());
        }
        let source = root.path().join(case);
        fs::write(&source, bytes).unwrap();
        for path in ["bin/ici", "tools/bin/ruff", "tools/bin/ty"] {
            let mut build = inputs();
            build.target = "x86_64-unknown-linux-musl".into();
            let output = root
                .path()
                .join(format!("{case}-{}.tar.gz", path.replace('/', "_")));
            let result = distribution::create_bundle(
                "0.14.0-fixture.1",
                build,
                &[BundleInput {
                    path: path.into(),
                    source: source.clone(),
                    executable: true,
                }],
                &output,
            );
            assert_eq!(result.unwrap_err().code, "install-elf", "{case}: {path}");
            assert!(
                !output.exists(),
                "An unusable ELF must not become an archive"
            );
        }
    }
}

#[test]
fn install_update_rollback_activate_are_atomic_and_do_not_rewind_user_data() {
    let root = tempfile::tempdir().unwrap();
    let prefix = root.path().join("native");
    let first = bundle(
        root.path(),
        "first",
        "0.14.0-fixture.1",
        &executables().first,
    );
    let second = bundle(
        root.path(),
        "second",
        "0.14.0-fixture.2",
        &executables().second,
    );
    let old = distribution::install(&first.path, &first.sha256, &prefix).unwrap();
    assert!(old.previous.is_none());
    fs::write(prefix.join("user-results.json"), b"new user observations").unwrap();
    fs::write(prefix.join("user-config.toml"), b"[user]\nvalue = 2\n").unwrap();
    let new = distribution::install(&second.path, &second.sha256, &prefix).unwrap();
    assert_eq!(new.previous.as_deref(), Some(old.selected.as_str()));
    assert_eq!(
        distribution::inspect_installation(&prefix)
            .unwrap()
            .selected,
        new.selected
    );
    assert_eq!(
        distribution::rollback(&prefix).unwrap().selected,
        old.selected
    );
    assert_eq!(
        distribution::rollback(&prefix).unwrap().selected,
        new.selected
    );
    assert_eq!(
        distribution::activate(&prefix, "0.14.0-fixture.1")
            .unwrap()
            .selected,
        old.selected
    );
    assert_eq!(
        fs::read(prefix.join("user-results.json")).unwrap(),
        b"new user observations"
    );
    assert_eq!(
        fs::read(prefix.join("user-config.toml")).unwrap(),
        b"[user]\nvalue = 2\n"
    );
    let output = Command::new(old.executable)
        .arg("--version")
        .env_clear()
        .env("PATH", "")
        .env("ICI_PYTHON", "/missing/legacy/python")
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(output.stdout, b"ici 0.14.0-fixture.1\n");
}

#[test]
fn failed_digest_execution_or_tampered_rollback_preserves_current_selection() {
    let root = tempfile::tempdir().unwrap();
    let prefix = root.path().join("native");
    let first = bundle(
        root.path(),
        "first",
        "0.14.0-fixture.1",
        &executables().first,
    );
    let old = distribution::install(&first.path, &first.sha256, &prefix).unwrap();
    assert!(distribution::install(&first.path, &"0".repeat(64), &prefix).is_err());
    assert_eq!(
        distribution::inspect_installation(&prefix)
            .unwrap()
            .selected,
        old.selected
    );
    let bad = bundle(
        root.path(),
        "wrong-version",
        "0.14.0-fixture.2",
        &executables().first,
    );
    assert_eq!(
        distribution::install(&bad.path, &bad.sha256, &prefix)
            .unwrap_err()
            .code,
        "install-execution"
    );
    assert_eq!(
        distribution::inspect_installation(&prefix)
            .unwrap()
            .selected,
        old.selected
    );
    let second = bundle(
        root.path(),
        "second",
        "0.14.0-fixture.2",
        &executables().second,
    );
    let new = distribution::install(&second.path, &second.sha256, &prefix).unwrap();
    fs::write(
        prefix
            .join("versions")
            .join(&old.selected)
            .join("README.txt"),
        b"tampered",
    )
    .unwrap();
    assert!(distribution::rollback(&prefix).is_err());
    assert_eq!(
        distribution::inspect_installation(&prefix)
            .unwrap()
            .selected,
        new.selected
    );
    assert!(fs::read_dir(prefix.join("versions")).unwrap().all(|entry| {
        !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".install-")
    }));
}

#[test]
fn foreign_prefix_entrypoint_symlinks_and_concurrent_transactions_are_refused() {
    use std::os::unix::fs::symlink;
    let root = tempfile::tempdir().unwrap();
    let first = bundle(
        root.path(),
        "first",
        "0.14.0-fixture.1",
        &executables().first,
    );
    let prefix = root.path().join("foreign");
    fs::create_dir(&prefix).unwrap();
    fs::write(prefix.join("user-config.toml"), b"keep").unwrap();
    assert_eq!(
        distribution::install(&first.path, &first.sha256, &prefix)
            .unwrap_err()
            .code,
        "install-owned-root"
    );
    assert_eq!(fs::read(prefix.join("user-config.toml")).unwrap(), b"keep");
    let alias = root.path().join("alias");
    symlink(&prefix, &alias).unwrap();
    assert!(distribution::install(&first.path, &first.sha256, &alias).is_err());
    let native = root.path().join("native");
    let installed = distribution::install(&first.path, &first.sha256, &native).unwrap();
    let lock = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(native.join("install.lock"))
        .unwrap();
    rustix::fs::flock(&lock, rustix::fs::FlockOperation::NonBlockingLockExclusive).unwrap();
    assert_eq!(
        distribution::rollback(&native).unwrap_err().code,
        "install-busy"
    );
    drop(lock);
    fs::remove_file(&installed.executable).unwrap();
    fs::write(&installed.executable, b"foreign Python entrypoint").unwrap();
    assert_eq!(
        distribution::install(&first.path, &first.sha256, &native)
            .unwrap_err()
            .code,
        "install-path-collision"
    );
    assert_eq!(
        fs::read(installed.executable).unwrap(),
        b"foreign Python entrypoint"
    );
}

fn mutate(root: &Path, source: &Path, kind: &str) -> (PathBuf, String) {
    let mut archive = tar::Archive::new(flate2::read::GzDecoder::new(
        fs::File::open(source).unwrap(),
    ));
    let mut entries = vec![];
    for entry in archive.entries().unwrap() {
        let mut entry = entry.unwrap();
        let mut bytes = vec![];
        entry.read_to_end(&mut bytes).unwrap();
        entries.push((entry.header().clone(), bytes));
    }
    match kind {
        "traversal" | "absolute" => {
            let bytes = entries[1].0.as_mut_bytes();
            bytes[..100].fill(0);
            let name = if kind == "absolute" {
                b"/outside".as_slice()
            } else {
                b"bin/../../outside".as_slice()
            };
            bytes[..name.len()].copy_from_slice(name);
            entries[1].0.set_cksum();
        }
        "symlink" => {
            entries[1].0.set_entry_type(tar::EntryType::Symlink);
            entries[1].0.set_link_name("/outside").unwrap();
            entries[1].0.set_cksum();
        }
        "hardlink" => {
            entries[1].0.set_entry_type(tar::EntryType::Link);
            entries[1].0.set_link_name("bin/ici").unwrap();
            entries[1].0.set_cksum();
        }
        "duplicate" => {
            entries.push(entries[1].clone());
        }
        "payload" => {
            entries[1].1[0] ^= 1;
        }
        "missing" => {
            entries.pop();
        }
        "mode" => {
            entries[1].0.set_mode(0o777);
            entries[1].0.set_cksum();
        }
        "duplicate-json" => {
            let original = String::from_utf8(entries[0].1.clone()).unwrap();
            entries[0].1 = original
                .replacen('{', "{\"schema_version\":99,", 1)
                .into_bytes();
            let size = entries[0].1.len() as u64;
            entries[0].0.set_size(size);
            entries[0].0.set_cksum();
        }
        _ => panic!("unknown mutation"),
    }
    let mut output = tar::Builder::new(flate2::write::GzEncoder::new(
        Vec::new(),
        flate2::Compression::fast(),
    ));
    for (header, bytes) in entries {
        output.append(&header, Cursor::new(bytes)).unwrap();
    }
    let bytes = output.into_inner().unwrap().finish().unwrap();
    let digest = hash(&bytes);
    let path = root.join(format!("{kind}.tar.gz"));
    fs::write(&path, bytes).unwrap();
    (path, digest)
}
#[test]
fn malformed_archives_cannot_escape_or_activate_and_all_payloads_are_hashed() {
    let root = tempfile::tempdir().unwrap();
    let first = bundle(
        root.path(),
        "first",
        "0.14.0-fixture.1",
        &executables().first,
    );
    let prefix = root.path().join("native");
    let installed = distribution::install(&first.path, &first.sha256, &prefix).unwrap();
    for kind in [
        "traversal",
        "absolute",
        "symlink",
        "hardlink",
        "duplicate",
        "payload",
        "missing",
        "mode",
        "duplicate-json",
    ] {
        let (path, digest) = mutate(root.path(), &first.path, kind);
        assert!(
            distribution::inspect_bundle(&path, &digest).is_err(),
            "{kind}"
        );
        assert!(
            distribution::install(&path, &digest, &prefix).is_err(),
            "{kind}"
        );
        assert_eq!(
            distribution::inspect_installation(&prefix)
                .unwrap()
                .selected,
            installed.selected,
            "{kind}"
        );
        assert!(!root.path().join("outside").exists());
    }
    let mut bytes = fs::read(&first.path).unwrap();
    bytes.extend_from_slice(b"trailing compressed content");
    let path = root.path().join("trailing.tar.gz");
    fs::write(&path, &bytes).unwrap();
    assert_eq!(
        distribution::inspect_bundle(&path, &hash(&bytes))
            .unwrap_err()
            .code,
        "archive-trailing"
    );
}
