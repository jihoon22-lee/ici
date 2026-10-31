//! Explicit legacy flat-source compilation; a missing descriptor never changes modern analysis.
mod run;
use super::*;
use crate::cpp::package_flags::{LegacyFlagsPlan, plan_flags};
use ici_application::snapshot::{InputLimits, InputSnapshot};
use ici_domain::{SourceInput, TaskKind, TaskSpec};
use ici_providers::{ProviderKind, ProviderPlan, ProviderRequest};
pub(super) use run::execute;

#[derive(Clone, Debug)]
pub struct DirectPreparation {
    flags: LegacyFlagsPlan,
    sources: Arc<Vec<SourceInput>>,
    objects: Vec<String>,
    binaries: Vec<DirectBinary>,
}
#[derive(Clone, Debug)]
struct DirectBinary {
    path: String,
    source: Option<String>,
    object_indices: Vec<usize>,
}

fn no_root_descriptor(directory: &Path) -> Result<(), String> {
    let mut count = 0usize;
    for entry in fs::read_dir(directory).map_err(|error| error.to_string())? {
        let entry = entry.map_err(|error| error.to_string())?;
        count += 1;
        if count > 4096 {
            return Err("direct build root inventory exceeds 4096 entries".into());
        }
        let name = entry.file_name();
        let name = name.to_string_lossy();
        let descriptor = ["CMakeLists.txt", "Makefile", "makefile", "GNUmakefile"]
            .contains(&name.as_ref())
            || name.ends_with(".pro")
            || name.starts_with("Makefile.")
            || name.ends_with(".mk")
            || name.ends_with(".make");
        if descriptor
            && (entry
                .file_type()
                .map_err(|error| error.to_string())?
                .is_file()
                || entry
                    .file_type()
                    .map_err(|error| error.to_string())?
                    .is_symlink())
        {
            return Err(format!(
                "root descriptor {name:?} requires its configured adapter; the direct compiler was not selected"
            ));
        }
    }
    Ok(())
}

fn unit(
    plan: &ContextPreparationPlan,
    source: &SourceInput,
    flags: &[String],
) -> ici_cpp::CompilationUnit {
    let direct = plan.direct.as_ref().expect("direct plan");
    let argv = std::iter::once(direct.flags.compiler.path.display().to_string())
        .chain(std::iter::once("-std=c++17".into()))
        .chain(direct.flags.includes.iter().cloned())
        .chain(flags.iter().cloned())
        .chain(std::iter::once(source.path.clone()))
        .collect::<Vec<_>>();
    let configuration = sha256(
        serde_json::to_vec(&ici_cpp::normalized_configuration_arguments(
            &plan.root, &plan.root, &argv,
        ))
        .expect("compiler configuration json"),
    );
    ici_cpp::CompilationUnit {
        source: source.path.clone(),
        directory: ".".into(),
        original_argv: argv.clone(),
        argv,
        compiler: "g++".into(),
        language: "c++".into(),
        standard: "c++17".into(),
        output: String::new(),
        target: String::new(),
        variant: plan.build.variant.clone(),
        defines: vec![],
        include_paths: vec![],
        sysroot: String::new(),
        sysroot_scope: String::new(),
        configuration,
        response_files: vec![],
        diagnostics: vec![],
    }
}
fn compile_providers(
    plan: &ContextPreparationPlan,
    flags: &[String],
) -> Result<Vec<ProviderPlan>, String> {
    let direct = plan.direct.as_ref().expect("direct plan");
    let coordinates = ici_providers::source_coordinates(direct.sources.iter());
    let mut providers = vec![];
    let mut argument_bytes = 0usize;
    for (index, source) in direct.sources.iter().enumerate() {
        let unit = unit(plan, source, flags);
        let object = plan.root.join(&direct.objects[index]);
        let command =
            ici_cpp::owned_object_command(&plan.root, &unit, &direct.flags.compiler.path, &object)
                .map_err(|error| error.to_string())?;
        let mut request = ProviderRequest::new(
            ProviderKind::Compiler,
            direct.flags.compiler.clone(),
            &plan.root,
            &command.cwd,
            vec![source.path.clone()],
            format!(
                "build:{}:{}:compile{index}",
                plan.build.id, plan.build.variant
            ),
        );
        request.component_id = Some(plan.component_id.clone());
        request.options.compiler_argv = command.argv.clone();
        request.options.source_token = Some(plan.root.join(&source.path).display().to_string());
        request.variant = Some(plan.build.variant.clone());
        let mut provider = ici_providers::plan(&request).map_err(|error| error.to_string())?;
        let diagnostic_flags = provider
            .task
            .argv
            .iter()
            .filter(|argument| argument.starts_with("-fdiagnostics-"))
            .cloned()
            .collect::<Vec<_>>();
        provider.task.argv = command.argv;
        provider.task.argv.extend(diagnostic_flags);
        provider.task.argv.extend(
            variant_flags(&plan.build.variant)
                .iter()
                .map(|flag| (*flag).to_owned()),
        );
        provider.task.kind = TaskKind::Prepare;
        provider.task.cwd = command.cwd.display().to_string();
        provider.task.input_refs = vec![plan.root.join(&source.path).display().to_string()];
        provider.task.output_specs = vec![object.display().to_string()];
        provider.task.work_dirs = vec![plan.root.join(&plan.build.shadow).display().to_string()];
        provider.task.resource_keys = vec![format!("build:{}", plan.build.shadow)];
        provider.task.timeout_seconds = Some(120.0);
        provider.task.output_limit_bytes = Some(1024 * 1024);
        argument_bytes += provider.task.argv.iter().map(String::len).sum::<usize>();
        if argument_bytes > 8 * 1024 * 1024 {
            return Err("direct compile argument inventory exceeds 8 MiB".into());
        }
        ici_providers::attach_sources(&mut provider, coordinates.clone());
        providers.push(provider);
    }
    Ok(providers)
}
fn variant_flags(variant: &str) -> &[&str] {
    match variant {
        "coverage" => &["--coverage", "-O0", "-g"],
        "sanitize" => &[
            "-fsanitize=address,undefined",
            "-fno-omit-frame-pointer",
            "-g",
        ],
        "thread-sanitize" => &[
            "-fsanitize=thread",
            "-fno-omit-frame-pointer",
            "-g",
            "-pthread",
        ],
        _ => &[],
    }
}
fn link_task(
    plan: &ContextPreparationPlan,
    providers: &[ProviderPlan],
    binary_index: usize,
) -> TaskSpec {
    let direct = plan.direct.as_ref().expect("direct plan");
    let binary = &direct.binaries[binary_index];
    let first = &providers[0].task;
    let source = plan
        .root
        .join(&direct.sources[0].path)
        .display()
        .to_string();
    let mut argv = vec![];
    let mut index = 0usize;
    while index < first.argv.len() {
        let value = &first.argv[index];
        if value == "-o" {
            index += 2;
            continue;
        }
        if value != "-c" && value != &source {
            argv.push(value.clone());
        }
        index += 1;
    }
    argv.extend(["-x".into(), "none".into()]);
    argv.extend(binary.object_indices.iter().map(|index| {
        plan.root
            .join(&direct.objects[*index])
            .display()
            .to_string()
    }));
    argv.extend([
        "-o".into(),
        plan.root.join(&binary.path).display().to_string(),
    ]);
    let mut task = TaskSpec::command(
        if direct.binaries.len() == 1 {
            format!("build:{}:{}:link", plan.build.id, plan.build.variant)
        } else {
            format!(
                "build:{}:{}:link{binary_index}",
                plan.build.id, plan.build.variant
            )
        },
        "g++",
        argv,
        plan.root.display().to_string(),
    );
    task.kind = TaskKind::Prepare;
    task.tool_digest = Some(direct.flags.compiler.digest.clone());
    task.input_refs = direct
        .objects
        .iter()
        .map(|path| plan.root.join(path).display().to_string())
        .collect();
    task.output_specs = vec![plan.root.join(&binary.path).display().to_string()];
    task.work_dirs = vec![plan.root.join(&plan.build.shadow).display().to_string()];
    task.resource_keys = vec![format!("build:{}", plan.build.shadow)];
    task.timeout_seconds = Some(120.0);
    task.output_limit_bytes = Some(1024 * 1024);
    task.depends_on = providers
        .iter()
        .map(|provider| provider.task.id.clone())
        .collect();
    task
}

pub fn plan_legacy_direct_preparation(
    project: &ResolvedProject,
    component: &Component,
    environment: &BTreeMap<String, String>,
) -> Result<ContextPreparationPlan, String> {
    if crate::cpp::linked_build(project, component).is_some() {
        return Err("a declared build requires its selected adapter".into());
    }
    let variant = component
        .settings
        .get("native_requested_variant")
        .and_then(Value::as_str)
        .unwrap_or("release");
    if !["release", "test", "coverage", "sanitize", "thread-sanitize"].contains(&variant) {
        return Err("direct compiler variant is unsupported".into());
    }
    let is_test = variant != "release";
    let directory = project.root.join(&component.root);
    no_root_descriptor(&directory)?;
    let excluded = crate::cpp::external_build_directories(project, component, environment)?;
    let tests_root = directory.join("tests");
    let test_sources = if is_test && tests_root.is_dir() {
        crate::files::tree(&project.root, &tests_root)
            .map_err(|error| error.to_string())?
            .into_iter()
            .filter(|entry| entry.file && entry.contained && entry.relative.ends_with(".cpp"))
            .map(|entry| entry.relative)
            .collect::<Vec<_>>()
    } else {
        vec![]
    };
    if is_test && (test_sources.is_empty() || test_sources.len() > 64) {
        return Err("direct runtime preparation requires 1..64 tests/**/*.cpp sources".into());
    }
    let tests_prefix = relative(&project.root, &tests_root)?;
    let mut selected_sources = component
        .files
        .iter()
        .filter(|path| ici_cpp::is_translation_unit(path))
        .filter(|path| !path.starts_with(&format!("{tests_prefix}/")))
        .filter(|path| {
            !excluded.iter().any(|directory| {
                directory == "."
                    || path.as_str() == directory
                    || path
                        .strip_prefix(directory)
                        .is_some_and(|tail| tail.starts_with('/'))
            })
        })
        .cloned()
        .collect::<Vec<_>>();
    selected_sources.extend(test_sources.iter().cloned());
    selected_sources.sort();
    selected_sources.dedup();
    if selected_sources.is_empty() || selected_sources.len() > 2048 {
        return Err("direct build requires 1..2048 owned production translation units".into());
    }
    let snapshot = InputSnapshot::take(
        &project.root,
        &selected_sources,
        &[],
        InputLimits {
            file_bytes: 16 * 1024 * 1024,
            total_bytes: 64 * 1024 * 1024,
        },
    )
    .map_err(|error| error.to_string())?;
    let mut sources = selected_sources
        .iter()
        .map(|path| snapshot.files[path].input.clone())
        .collect::<Vec<_>>();
    let mut retained = vec![];
    let mut main_count = 0usize;
    for source in sources {
        let count =
            ici_cpp::legacy_main_definition_count(&source).map_err(|error| error.to_string())?;
        if is_test && !test_sources.contains(&source.path) && count > 0 {
            continue;
        }
        main_count += count;
        retained.push(source);
    }
    sources = retained;
    selected_sources = sources.iter().map(|source| source.path.clone()).collect();
    if !is_test && main_count != 1 {
        return Err(format!(
            "C++ adapter required: expected exactly one int main definition, found {main_count}"
        ));
    }
    let name = crate::artifact_build::source_metadata(project, &directory)?.name;
    let build_id = format!("legacy-direct-{}", &sha256(&component.id)[7..23]);
    let shadow = format!(".ici/rust-v1/build/{build_id}/{variant}");
    let flags = plan_flags(
        project,
        component,
        &format!("build:{build_id}:{variant}"),
        environment,
    )?;
    let objects = selected_sources
        .iter()
        .map(|path| format!("{shadow}/objects/{}.o", &sha256(path)[7..]))
        .collect::<Vec<_>>();
    let binaries = if is_test {
        test_sources
            .iter()
            .map(|source| DirectBinary {
                path: format!("{shadow}/bin/test-{}", &sha256(source)[7..31]),
                source: Some(source.clone()),
                object_indices: sources
                    .iter()
                    .enumerate()
                    .filter(|(_, input)| {
                        !test_sources.contains(&input.path) || input.path == *source
                    })
                    .map(|(index, _)| index)
                    .collect(),
            })
            .collect()
    } else {
        vec![DirectBinary {
            path: format!("{shadow}/bin/{name}"),
            source: None,
            object_indices: (0..sources.len()).collect(),
        }]
    };
    let owner_digest=sha256(ici_domain::canonical_json(&json!({"schema":"ici.rust.build-owner/v1","root":project.root,"id":build_id,"variant":variant,"project":component.root})).map_err(|error|error.to_string())?);
    let build = BuildPlan {
        id: build_id.clone(),
        system: "direct-gcc".into(),
        variant: variant.into(),
        shadow: shadow.clone(),
        owner_digest,
        tasks: vec![],
        compilation_database: format!("{shadow}/compile_commands.json"),
        capture_journal: None,
        generated_roots: vec![shadow.clone()],
        limitations: vec![],
    };
    let mut inputs =
        crate::files::build_inputs(&project.root, &directory).map_err(|error| error.to_string())?;
    inputs.extend(selected_sources.clone());
    inputs.sort();
    inputs.dedup();
    let mut plan = ContextPreparationPlan {
        root: project.root.clone(),
        component_id: component.id.clone(),
        build_id,
        build,
        tools: flags.tools.clone(),
        inputs,
        selected_sources,
        declared_build: None,
        descriptor: component.root.clone(),
        effects: Value::Null,
        direct: Some(DirectPreparation {
            flags,
            sources: Arc::new(sources),
            objects,
            binaries,
        }),
    };
    let mut providers = compile_providers(&plan, &[])?;
    if !plan
        .direct
        .as_ref()
        .expect("direct plan")
        .flags
        .probes
        .is_empty()
    {
        for provider in &mut providers {
            provider
                .task
                .argv
                .push("{observed-pkg-config-cflags}".into());
            provider.task.depends_on = plan
                .direct
                .as_ref()
                .expect("direct plan")
                .flags
                .probes
                .iter()
                .map(|task| task.id.clone())
                .collect();
        }
    }
    let mut links = (0..plan.direct.as_ref().expect("direct plan").binaries.len())
        .map(|index| link_task(&plan, &providers, index))
        .collect::<Vec<_>>();
    if providers.iter().any(|provider| {
        provider
            .task
            .argv
            .iter()
            .any(|argument| argument == "{observed-pkg-config-cflags}")
    }) {
        for link in &mut links {
            link.argv
                .retain(|argument| argument != "{observed-pkg-config-cflags}");
            link.argv.push("{observed-pkg-config-cflags}".into());
        }
    }
    plan.build.tasks = plan
        .direct
        .as_ref()
        .expect("direct plan")
        .flags
        .probes
        .iter()
        .cloned()
        .chain(providers.into_iter().map(|provider| provider.task))
        .chain(links)
        .collect();
    plan.effects = json!({"owned_roots":[shadow],"outputs":[plan.build.compilation_database,plan.direct.as_ref().expect("direct plan").objects,plan.direct.as_ref().expect("direct plan").binaries.iter().map(|binary|&binary.path).collect::<Vec<_>>()],"database":"written from actual successful per-source compile argv only","descriptor":"no root native descriptor; explicit legacy direct application build","tasks":plan.build.tasks});
    Ok(plan)
}
