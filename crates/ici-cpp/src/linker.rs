//! Target-local GNU ELF section-GC evidence. This is deliberately narrower than reachability.
use crate::{
    BuildPlan, BuildRequest, CompilationContext, CppError,
    analysis::{fail, finding, measurement},
    input::{read_contained, scoped},
    source,
};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, InspectionTarget, TaskKind, TaskSpec, TaskState,
};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
    sync::LazyLock,
};

static GCC_DRIVER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?:[A-Za-z0-9_+.]+-)*(?:gcc|g\+\+)(?:-[0-9][A-Za-z0-9_.-]*)?$").unwrap()
});
static TARGET: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[A-Za-z0-9_.+-]{1,256}$").unwrap());
static SECTION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\.(?:text|gnu\.linkonce\.t)\.[A-Za-z0-9_.$+-]{1,1024}$").unwrap()
});
static REMOVAL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?:^|:\s+)removing unused section ['`](?P<section>[^'`]+)['`] in file ['`](?P<object>[^'`]+)['`]\s*$").unwrap()
});
static SECTION_HEADER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*\[\s*([0-9]+)\]\s+(\S+)\s+(\S+)\s+").unwrap());
static SYMBOL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^\s*[0-9]+:\s+([0-9A-Fa-f]+)\s+([0-9]+)\s+(\S+)\s+(\S+)\s+(\S+)\s+([0-9]+)\s+(\S+)\s*$",
    )
    .unwrap()
});
static SOURCE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(.+):([1-9][0-9]*)(?:\s+\(discriminator\s+[0-9]+\))?$").unwrap()
});
/// Pure PREPARE plan. Callers must explicitly execute it under the returned build transaction lease.
pub fn plan_linker_build(root: &Path, request: &BuildRequest) -> Result<BuildPlan, CppError> {
    if request.system != "cmake" {
        return Err(CppError::new(
            "linker-build-system",
            "GNU ELF reachability requires an explicit CMake project",
        ));
    }
    let mut request = request.clone();
    request.id = format!("{}-link-reachability", request.id);
    request.variant = "link-reachability".into();
    request.jobs = request.jobs.min(2);
    // Keep target-owned definitions/includes/flags in the CMake project. These diagnostic additions
    // only ask its declared build to expose function sections and a non-PIE GNU GC link.
    request.extra_cmake_args.extend([
        "-DCMAKE_BUILD_TYPE=Release".into(),
        "-DCMAKE_C_FLAGS=-g -ffunction-sections -fno-lto -fno-pie".into(),
        "-DCMAKE_CXX_FLAGS=-g -ffunction-sections -fno-lto -fno-pie".into(),
        "-DCMAKE_EXE_LINKER_FLAGS=-Wl,--gc-sections -Wl,--print-gc-sections -fno-lto -no-pie"
            .into(),
    ]);
    let mut plan = crate::plan_build(root, &request)?;
    plan.tasks[0]
        .argv
        .extend(["-G".into(), "Unix Makefiles".into()]);
    Ok(plan)
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct LinkCommand {
    pub target: String,
    pub argv: Vec<String>,
    pub objects: Vec<String>,
    pub output: String,
    pub shadow: String,
    pub digest: String,
    pub driver_digest: String,
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct DiscardedSection {
    pub target: String,
    pub object_path: String,
    pub section: String,
    pub command_digest: String,
}
fn existing_inside(
    root: &Path,
    shadow: &Path,
    cwd: &Path,
    value: &str,
) -> Result<(String, PathBuf), CppError> {
    let (display, scope, physical) = scoped(root, cwd, value)?;
    if scope != "project" || !physical.starts_with(shadow) {
        return Err(CppError::new(
            "link-input-outside-shadow",
            "Link inputs and output must belong to the owned shadow",
        ));
    }
    let metadata = fs::symlink_metadata(&physical)
        .map_err(|error| CppError::new("link-input-missing", error.to_string()))?;
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.nlink() != 1 {
        return Err(CppError::new(
            "unsafe-link-input",
            "Link input/output must be a single-link regular file",
        ));
    }
    Ok((display, physical))
}
fn safe_flag(value: &str) -> bool {
    value.starts_with("-D")
        || value.starts_with("-U")
        || matches!(
            value,
            "-ffunction-sections"
                | "-fdata-sections"
                | "-fno-lto"
                | "-fno-pie"
                | "-no-pie"
                | "-Wl,--gc-sections"
                | "-Wl,--print-gc-sections"
                | "-pthread"
                | "-s"
                | "-static"
                | "-static-libgcc"
                | "-static-libstdc++"
        )
        || value.starts_with("-O") && value[2..].bytes().all(|byte| byte.is_ascii_alphanumeric())
        || value.starts_with("-g")
            && value[2..]
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        || value.starts_with("-l")
            && value.len() > 2
            && value[2..]
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"_.+-".contains(&byte))
}
pub fn parse_link_command(
    root: &Path,
    plan: &BuildPlan,
    path: &Path,
    approved_drivers: &[PathBuf],
    context: &CompilationContext,
) -> Result<LinkCommand, CppError> {
    let root = root
        .canonicalize()
        .map_err(|error| CppError::new("link-root", error.to_string()))?;
    let shadow = root.join(&plan.shadow);
    if !path.starts_with(&shadow) || path.file_name().is_none_or(|name| name != "link.txt") {
        return Err(CppError::new(
            "invalid-link-path",
            "Link metadata must be an owned CMake link.txt",
        ));
    }
    let parent = path
        .parent()
        .and_then(Path::file_name)
        .and_then(|name| name.to_str())
        .and_then(|name| name.strip_suffix(".dir"))
        .ok_or_else(|| {
            CppError::new(
                "invalid-link-target",
                "Link metadata lacks a CMake target directory",
            )
        })?;
    if !TARGET.is_match(parent) {
        return Err(CppError::new(
            "invalid-link-target",
            "Link target name exceeds bounded contract",
        ));
    }
    let bytes = read_contained(&root, path, 4 * 1024 * 1024)?;
    let text = std::str::from_utf8(&bytes)
        .map_err(|_| CppError::new("link-encoding", "Link metadata must be UTF-8"))?;
    if text.lines().count() != 1 || text.contains('\0') {
        return Err(CppError::new(
            "link-line-count",
            "Link metadata must contain one direct command",
        ));
    }
    let original_argv = crate::split_posix(text)?;
    // CMake 4.x may add a linker dependency-file output. It is irrelevant to a diagnostic
    // relink and could otherwise overwrite a path outside the declared ELF output contract.
    let argv: Vec<_> = original_argv
        .into_iter()
        .filter(|argument| !argument.starts_with("-Wl,--dependency-file="))
        .collect();
    if argv.is_empty()
        || argv.len() > 32_768
        || argv.iter().map(String::len).sum::<usize>() > 1024 * 1024
    {
        return Err(CppError::new(
            "link-argv-bounds",
            "Link argv exceeds argument count/size contract",
        ));
    }
    let driver = Path::new(&argv[0]);
    if !driver.is_absolute()
        || !approved_drivers.iter().any(|approved| approved == driver)
        || driver.starts_with(&root)
    {
        return Err(CppError::new(
            "link-driver-unapproved",
            "Link driver must be an approved absolute GCC driver outside the project",
        ));
    }
    let physical_driver = driver
        .canonicalize()
        .map_err(|error| CppError::new("link-driver-unavailable", error.to_string()))?;
    let name = physical_driver
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("");
    if !GCC_DRIVER.is_match(name) {
        return Err(CppError::new(
            "link-driver-family",
            "Exact GNU ELF evidence requires a GCC driver alias",
        ));
    }
    let driver_digest = ici_execution::process::executable_digest(driver)
        .map_err(|error| CppError::new("link-driver-identity", error.to_string()))?;
    for required in ["-Wl,--gc-sections", "-Wl,--print-gc-sections", "-no-pie"] {
        if !argv.iter().any(|argument| argument == required) {
            return Err(CppError::new(
                "link-policy",
                "Link command lacks required section-GC/non-PIE flags",
            ));
        }
    }
    let mut objects = BTreeSet::new();
    let mut output = None;
    let mut index = 1;
    while index < argv.len() {
        let value = &argv[index];
        if value == "-o" {
            if output.is_some() {
                return Err(CppError::new(
                    "link-output-count",
                    "Link command must have one output",
                ));
            }
            index += 1;
            let value = argv.get(index).ok_or_else(|| {
                CppError::new("link-output-missing", "Link output path is missing")
            })?;
            output = Some(existing_inside(&root, &shadow, &shadow, value)?.0);
        } else if value.ends_with(".o") || value.ends_with(".obj") {
            objects.insert(existing_inside(&root, &shadow, &shadow, value)?.0);
        } else if value == "-L" {
            index += 1;
            let value = argv
                .get(index)
                .ok_or_else(|| CppError::new("link-library-path", "Missing -L path"))?;
            if value.starts_with('-') || value.contains(['\0', '$', '`']) {
                return Err(CppError::new(
                    "unsafe-link-library-path",
                    "Unsupported library path",
                ));
            }
        } else if value.starts_with("-L") && value.len() > 2 && !value.contains(['\0', '$', '`']) {
        } else if !safe_flag(value) {
            return Err(CppError::new(
                "unsupported-link-argument",
                format!("Link argument {value:?} is outside the exact GNU direct-object contract"),
            ));
        }
        index += 1;
    }
    if objects.is_empty() || objects.len() > 4096 {
        return Err(CppError::new(
            "link-object-count",
            "Link command must have 1..4096 direct objects",
        ));
    }
    let mut units = BTreeMap::new();
    for unit in &context.units {
        if !unit.output.is_empty() && units.insert(unit.output.clone(), unit).is_some() {
            return Err(CppError::new(
                "link-object-ambiguous",
                "Object maps to multiple compile configurations",
            ));
        }
    }
    for object in &objects {
        let unit = units.get(object).ok_or_else(|| {
            CppError::new(
                "link-object-uncovered",
                "Direct link object lacks compile evidence",
            )
        })?;
        if !unit_has_function_sections(unit) {
            return Err(CppError::new(
                "link-object-policy",
                "Direct object is not function-section based or enables LTO",
            ));
        }
    }
    let digest = ici_domain::sha256(
        ici_domain::canonical_json(&json!({"target":parent,"argv":argv}))
            .map_err(|error| CppError::new("link-digest", error.to_string()))?,
    );
    Ok(LinkCommand {
        target: parent.into(),
        argv,
        objects: objects.into_iter().collect(),
        output: output
            .ok_or_else(|| CppError::new("link-output-missing", "Link command lacks output"))?,
        shadow: plan.shadow.clone(),
        digest,
        driver_digest,
    })
}
pub fn unit_has_function_sections(unit: &crate::CompilationUnit) -> bool {
    unit.argv
        .iter()
        .rfind(|argument| {
            matches!(
                argument.as_str(),
                "-ffunction-sections" | "-fno-function-sections"
            )
        })
        .is_some_and(|argument| argument == "-ffunction-sections")
        && !unit
            .argv
            .iter()
            .any(|argument| argument == "-flto" || argument.starts_with("-flto="))
}
pub fn discover_link_commands(
    root: &Path,
    plan: &BuildPlan,
    approved_drivers: &[PathBuf],
    context: &CompilationContext,
) -> Result<Vec<LinkCommand>, CppError> {
    let shadow = root.join(&plan.shadow);
    let mut pending = vec![shadow];
    let mut paths = vec![];
    let mut count = 0;
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(directory)
            .map_err(|error| CppError::new("link-discovery", error.to_string()))?
        {
            let entry =
                entry.map_err(|error| CppError::new("link-discovery", error.to_string()))?;
            count += 1;
            if count > 32_768 {
                return Err(CppError::new(
                    "link-discovery-limit",
                    "Owned build tree exceeds bounded entry count",
                ));
            }
            let kind = entry
                .file_type()
                .map_err(|error| CppError::new("link-discovery", error.to_string()))?;
            if kind.is_dir() {
                pending.push(entry.path());
            } else if kind.is_file() && entry.file_name() == "link.txt" {
                paths.push(entry.path());
                if paths.len() > 256 {
                    return Err(CppError::new(
                        "link-target-count",
                        "Link targets exceed 256",
                    ));
                }
            }
        }
    }
    paths.sort();
    paths
        .iter()
        .map(|path| parse_link_command(root, plan, path, approved_drivers, context))
        .collect()
}
pub fn linker_identity_task(root: &Path, command: &LinkCommand) -> TaskSpec {
    let mut task = TaskSpec::command(
        format!("linker-identity:{}", command.target),
        "gnu-linker",
        vec![command.argv[0].clone(), "-Wl,--version".into()],
        root.join(&command.shadow).display().to_string(),
    );
    task.tool_digest = Some(command.driver_digest.clone());
    task.env_overlay = crate::replay_environment();
    task.timeout_seconds = Some(30.0);
    task.output_limit_bytes = Some(65536);
    task
}
pub fn relink_task(root: &Path, command: &LinkCommand) -> TaskSpec {
    let mut task = TaskSpec::command(
        format!("linker-relink:{}", command.target),
        "gnu-linker",
        command.argv.clone(),
        root.join(&command.shadow).display().to_string(),
    );
    task.kind = TaskKind::Prepare;
    task.tool_digest = Some(command.driver_digest.clone());
    task.env_overlay = crate::replay_environment();
    task.resource_keys = vec![format!("build:{}", command.shadow)];
    task.timeout_seconds = Some(180.0);
    task.output_limit_bytes = Some(4 * 1024 * 1024);
    task
}
pub fn parse_discarded_sections(
    root: &Path,
    command: &LinkCommand,
    transcript: &str,
) -> Result<Vec<DiscardedSection>, CppError> {
    if transcript.len() > 4 * 1024 * 1024 || transcript.contains('\0') {
        return Err(CppError::new(
            "link-output-bounds",
            "Link output exceeds bounded contract",
        ));
    }
    let shadow = root.join(&command.shadow);
    let mut result = vec![];
    for line in transcript
        .lines()
        .filter(|line| line.contains("removing unused section"))
    {
        let capture = REMOVAL.captures(line).ok_or_else(|| {
            CppError::new(
                "link-removal-shape",
                "GNU ld discarded-section diagnostic is unrecognized",
            )
        })?;
        let section = &capture["section"];
        let object = &capture["object"];
        if !SECTION.is_match(section) || object.contains(['(', ')']) {
            continue;
        }
        let Ok((object, _)) = existing_inside(root, &shadow, &shadow, object) else {
            continue;
        };
        if !command.objects.contains(&object) {
            continue;
        }
        result.push(DiscardedSection {
            target: command.target.clone(),
            object_path: object,
            section: section.into(),
            command_digest: command.digest.clone(),
        });
        if result.len() > 16_384 {
            return Err(CppError::new(
                "link-removal-count",
                "Discarded sections exceed 16384",
            ));
        }
    }
    Ok(result)
}
/// Intersection across every linked target sharing each object. A union would falsely report
/// functions kept by another executable as removable from the selected program set.
pub fn discarded_by_every_linking_target(
    removals: &[DiscardedSection],
    linked: &[LinkCommand],
) -> (Vec<DiscardedSection>, usize) {
    let mut linking: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
    for command in linked {
        for object in &command.objects {
            linking.entry(object).or_default().insert(&command.target);
        }
    }
    let mut discarding: BTreeMap<(&str, &str), BTreeSet<&str>> = BTreeMap::new();
    for removal in removals {
        discarding
            .entry((&removal.object_path, &removal.section))
            .or_default()
            .insert(&removal.target);
    }
    let mut kept = 0;
    let mut seen = BTreeSet::new();
    let mut result = vec![];
    for removal in removals {
        let key = (removal.object_path.as_str(), removal.section.as_str());
        let required = linking.get(removal.object_path.as_str());
        if required.is_none_or(|required| !required.is_subset(discarding.get(&key).unwrap())) {
            kept += 1;
            continue;
        }
        if seen.insert(key) {
            result.push(removal.clone());
        }
    }
    (result, kept)
}
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ObjectFacts {
    pub sections: BTreeMap<String, String>,
    pub symbols: BTreeMap<String, Vec<String>>,
    pub grouped_sections: BTreeSet<String>,
    pub lto: bool,
}
pub fn parse_object_facts(
    headers: &str,
    symbols: &str,
    groups: &str,
) -> Result<ObjectFacts, CppError> {
    if [headers, symbols, groups]
        .iter()
        .any(|text| text.len() > 2 * 1024 * 1024 || text.contains('\0'))
    {
        return Err(CppError::new(
            "object-output-bounds",
            "Object evidence exceeds bounded text contract",
        ));
    }
    let mut facts = ObjectFacts::default();
    let mut by_index = BTreeMap::new();
    for line in headers.lines() {
        if let Some(capture) = SECTION_HEADER.captures(line) {
            facts.lto |= capture[2].starts_with(".gnu.lto");
            if &capture[3] == "PROGBITS" {
                if facts
                    .sections
                    .insert(capture[2].into(), capture[1].into())
                    .is_some()
                {
                    return Err(CppError::new(
                        "object-section-ambiguous",
                        "Repeated object section name",
                    ));
                }
                by_index.insert(capture[1].to_owned(), capture[2].to_owned());
            }
        }
    }
    for line in symbols.lines() {
        if let Some(capture) = SYMBOL.captures(line) {
            if &capture[3] != "FUNC" || capture[2].parse::<u64>().unwrap_or(0) == 0 {
                continue;
            }
            let Some(section) = by_index.get(&capture[6]) else {
                continue;
            };
            let binding = &capture[4];
            let visibility = &capture[5];
            let name = &capture[7];
            let accepted = (binding == "LOCAL" || ["HIDDEN", "INTERNAL"].contains(&visibility))
                && binding != "WEAK"
                && ![".cold", ".constprop.", ".isra.", ".llvm.", ".part."]
                    .iter()
                    .any(|marker| name.contains(marker));
            facts
                .symbols
                .entry(section.clone())
                .or_default()
                .push(if accepted {
                    name.to_owned()
                } else {
                    String::new()
                });
        }
    }
    for line in groups.lines() {
        for token in line.split_whitespace() {
            if facts.sections.contains_key(token) {
                facts.grouped_sections.insert(token.into());
            }
        }
    }
    Ok(facts)
}
pub fn eligible_discarded_symbol(facts: &ObjectFacts, section: &str) -> Option<String> {
    if facts.lto || facts.grouped_sections.contains(section) {
        return None;
    }
    let symbols = facts.symbols.get(section)?;
    if symbols.len() == 1 && !symbols[0].is_empty() {
        Some(symbols[0].clone())
    } else {
        None
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LinkerTargetEvidence {
    pub command_digest: String,
    pub complete: bool,
    pub linker_banner: String,
    pub elf_header: String,
    pub discarded_output: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LinkerObjectEvidence {
    pub object_path: String,
    pub source: String,
    pub source_digest: String,
    pub headers: String,
    pub symbols: String,
    pub groups: String,
    pub addr2line: BTreeMap<String, String>,
}
fn located_source(
    root: &Path,
    shadow: &Path,
    text: &str,
    request: &AnalysisRequest,
) -> Option<(String, u32)> {
    if text.len() > 16384 || text.lines().filter(|line| !line.trim().is_empty()).count() != 1 {
        return None;
    }
    let capture = SOURCE.captures(text.trim())?;
    let (path, scope, _) = scoped(root, shadow, &capture[1]).ok()?;
    if scope != "project" {
        return None;
    }
    let line = capture[2].parse::<u32>().ok()?;
    let input = request.inputs.iter().find(|input| input.path == path)?;
    if !source::policy_exclusions(request, &path).is_empty()
        || line as usize > std::str::from_utf8(&input.bytes).ok()?.lines().count()
    {
        return None;
    }
    Some((path, line))
}
pub fn analyze_linker_dead(
    request: &AnalysisRequest,
    root: &Path,
    context: &CompilationContext,
    commands: &[LinkCommand],
    targets: &[LinkerTargetEvidence],
    objects: &[LinkerObjectEvidence],
) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(&request.task_id, "gnu-linker.section-gc");
    output.observation.state = TaskState::Succeeded;
    let excluded_sources = source::record_policy_exclusions(request, &mut output);
    if commands.is_empty() || !context.complete() {
        fail(
            &mut output,
            ".",
            "linker-context-incomplete",
            "GNU ELF dead-section analysis requires supported targets and complete function-section compile evidence",
        );
        return output;
    }
    let target_digests: BTreeSet<_> = targets
        .iter()
        .map(|evidence| evidence.command_digest.as_str())
        .collect();
    let command_digests: BTreeSet<_> = commands
        .iter()
        .map(|command| command.digest.as_str())
        .collect();
    if target_digests != command_digests || targets.len() != commands.len() {
        fail(
            &mut output,
            ".",
            "linker-evidence-incomplete",
            "Every selected link target must finish exactly once",
        );
        return output;
    }
    let mut removals = vec![];
    for command in commands {
        let evidence = targets
            .iter()
            .find(|evidence| evidence.command_digest == command.digest)
            .unwrap();
        if !evidence.complete
            || !evidence.linker_banner.contains("GNU ld")
            || evidence.linker_banner.contains("LLD")
        {
            fail(
                &mut output,
                ".",
                "linker-identity-incomplete",
                "Selected linker is not a completed GNU ld probe/relink",
            );
            continue;
        }
        let elf = crate::parse_readelf(&evidence.elf_header);
        if elf.is_err() || elf.as_ref().unwrap().elf_type != "EXEC" {
            fail(
                &mut output,
                &command.output,
                "linker-output-not-executable",
                "Selected GNU link did not produce ELF EXEC",
            );
            continue;
        }
        match parse_discarded_sections(root, command, &evidence.discarded_output) {
            Ok(found) => removals.extend(found),
            Err(error) => fail(&mut output, ".", &error.code, &error.message),
        }
    }
    if output.observation.state == TaskState::Failed {
        return output;
    }
    let (retained, kept) = discarded_by_every_linking_target(&removals, commands);
    let mut excluded = 0;
    let mut found = BTreeSet::new();
    for removal in retained {
        let Some(object) = objects
            .iter()
            .find(|object| object.object_path == removal.object_path)
        else {
            fail(
                &mut output,
                ".",
                "link-object-evidence-missing",
                "Discarded direct object lacks readelf/source evidence",
            );
            continue;
        };
        let Some(input) = request
            .inputs
            .iter()
            .find(|input| input.path == object.source)
        else {
            excluded += 1;
            continue;
        };
        if ici_domain::sha256(&input.bytes) != object.source_digest {
            fail(
                &mut output,
                &object.source,
                "link-source-drift",
                "Discarded object evidence does not match immutable source",
            );
            continue;
        }
        let facts = match parse_object_facts(&object.headers, &object.symbols, &object.groups) {
            Ok(facts) => facts,
            Err(error) => {
                fail(&mut output, &object.source, &error.code, &error.message);
                continue;
            }
        };
        let Some(symbol) = eligible_discarded_symbol(&facts, &removal.section) else {
            excluded += 1;
            continue;
        };
        let Some(location) = object.addr2line.get(&removal.section) else {
            excluded += 1;
            continue;
        };
        let command = commands
            .iter()
            .find(|command| command.digest == removal.command_digest)
            .unwrap();
        let Some((path, line)) =
            located_source(root, &root.join(&command.shadow), location, request)
        else {
            excluded += 1;
            continue;
        };
        let mut target = InspectionTarget::at(&path, line);
        target.target_name = "GNU ld discarded function".into();
        target.status = "WARN".into();
        target.message = format!(
            "GNU ld discarded function section from CMake target {} in the isolated Release reachability link",
            removal.target
        );
        target.metrics = BTreeMap::from([
            ("link_target".into(), json!(removal.target)),
            ("symbol".into(), json!(symbol)),
            ("section".into(), json!(removal.section)),
            ("link_command_digest".into(), json!(removal.command_digest)),
            ("object_path".into(), json!(removal.object_path)),
        ]);
        finding(
            &mut output,
            request,
            &target,
            "dead.cpp-linker-discarded-function",
            "medium",
            false,
        );
        found.insert(path);
        output.targets.push(target);
    }
    for input in &request.inputs {
        if crate::is_translation_unit(&input.path)
            && source::policy_exclusions(request, &input.path).is_empty()
            && !found.contains(&input.path)
        {
            let mut target = InspectionTarget::at(&input.path, 1);
            target.target_name = "LinkerReachability".into();
            target.message="No eligible project-owned discarded function section was observed in the selected links".into();
            output.targets.push(target);
        }
    }
    if output.observation.state == TaskState::Succeeded {
        output.status = if output.observation.findings.is_empty() {
            "PASS"
        } else {
            "WARN"
        }
        .into();
    }
    measurement(
        &mut output,
        "discarded_sections_observed",
        removals.len() as u64,
        false,
    );
    measurement(
        &mut output,
        "link_targets_checked",
        commands.len() as u64,
        false,
    );
    output.extra = json!({"mode":"gnu-elf-section-gc","ambiguous_sections_excluded":excluded,"sections_kept_by_another_target":kept,"excluded_source_counts":excluded_sources});
    output.observation.limitations.push("Target-local GNU ELF section GC is not whole-program reachability. Dynamic lookup, exported/default-visible symbols, archives, shared objects, LTO, linker scripts, COMDAT groups and ambiguous source mappings are excluded".into());
    output
}
