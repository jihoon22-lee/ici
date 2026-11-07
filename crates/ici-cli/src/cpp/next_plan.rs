//! Frozen `ici next plan` C++ wording (v0.13.0 `application/planning.py::compile_limitations`,
//! `application/gates.py::gate_cpp`/`gate_artifact`, `application/testing.py` and
//! `workspace/compile_units.py::_prepare`).
//!
//! A component with C++ in scope and no compilation database carries frozen's note
//! (`<id>: no compilation database for its C++ scope (<hint>)`) and coverage gap (`<id>: no
//! compilation database captured its C++ scope`; a gate reason only under `--require-full`).
//! A C++ check the native planner blocked carries frozen's reason (human plan, `--json`
//! document, gate reasons and limitations) when the frozen condition behind it holds.
use super::*;

/// Frozen `_prepare`: what would produce the missing database, declared and never invented.
/// Native builds carry no prepare vector, so a linked build always names the configure gap. A
/// schema 2 run that builds prepares a declared build itself, so only the way to get one differs.
pub(super) fn prepare_hint(project: &ResolvedProject, component: &Component) -> String {
    if project.schema_family == ici_config::SchemaFamily::SparseV2
        && let Some(build) = linked_build(project, component)
    {
        let directory = build.directory.as_deref().unwrap_or(".");
        return if build.prepare.as_deref() == Some("explicit") {
            format!(
                "build '{}' is yours to run (prepare = \"explicit\") — generate {directory}/compile_commands.json, or remove that key so `ici verify` builds it",
                build.id
            )
        } else if crate::files::under(&project.root, directory).is_dir()
            && build.directory.is_some()
        {
            format!(
                "build '{}' reads the existing {directory} — generate {directory}/compile_commands.json there, or remove the directory so `ici verify` builds it",
                build.id
            )
        } else {
            format!(
                "build '{}' is built by `ici verify`, not by a static run — run that, or generate {directory}/compile_commands.json",
                build.id
            )
        };
    }
    match linked_build(project, component) {
        Some(build) => format!(
            "build '{}' declares no approved configure command — give it a prepare vector or generate {}/compile_commands.json",
            build.id,
            build.directory.as_deref().unwrap_or(".")
        ),
        None => format!(
            "no build unit is linked to {}; declare [builds.<id>] with a directory that will hold compile_commands.json and set the component's build, or generate the database out of band",
            component.id
        ),
    }
}

/// Frozen `workspace/test_suites.suites_for_build` for the cases it can decide: `Some(false)`
/// when the linked build declares no test suite (no definition, a Make/custom build, or a CMake
/// `add_subdirectory` tree without a literal `add_test`), `Some(true)` for a CMake tree with
/// one, and for qmake whether a SUBDIRS `.pro` names testlib/testcase (`_qmake_suites`).
fn declares_test_suite(root: &Path, build: &ici_config::Build) -> Option<bool> {
    let Some(definition) = build.project.as_deref() else {
        return Some(false);
    };
    match build.system.as_deref() {
        Some("cmake") => Some(cmake_declares_test(root, definition)),
        Some("qmake") => {
            Some(!super::build_reach::qmake_test_binaries(root, definition).is_empty())
        }
        _ => Some(false),
    }
}

/// Frozen `resolve_cmake_project(...).tests`: comments (`#` and `#[[ ]]`) are stripped, literal
/// `add_subdirectory` entries with their own CMakeLists.txt are followed (depth 16, 512 KiB
/// read per file, each file once), and an `add_test` whose (NAME-form or first) name is a
/// literal token declares the build's suite. Nothing is evaluated (`build_reach`).
fn cmake_declares_test(root: &Path, definition: &str) -> bool {
    super::build_reach::cmake_declares_test(root, definition)
}

/// Frozen `cpp_gcno`: instrumentation notes under the linked build directory.
/// One CMakeLists.txt read for the `add_test` scan: frozen read the first 512 KiB + 1 bytes
/// of whatever the path named. The read here is bounded before it happens (frozen read the
/// whole file and then truncated), a FIFO/device is refused instead of blocking, and a file
/// whose resolved path leaves the project root is refused (frozen followed it). A refused file
/// is what frozen did with an unreadable one: it declares nothing.
pub(super) fn read_contained_lists(root: &Path, path: &Path, limit: usize) -> Option<Vec<u8>> {
    let resolved = path.canonicalize().ok()?;
    let root = root.canonicalize().ok()?;
    if !resolved.starts_with(&root) {
        return None;
    }
    ici_execution::storage::read_regular_prefix(&resolved, limit).ok()
}

fn has_gcno(project: &ResolvedProject, component: &Component) -> bool {
    fn walk(directory: &Path, budget: &mut usize) -> bool {
        let Ok(entries) = std::fs::read_dir(directory) else {
            return false;
        };
        for entry in entries.flatten() {
            if *budget == 0 {
                return false;
            }
            *budget -= 1;
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            let path = entry.path();
            if kind.is_dir() {
                if walk(&path, budget) {
                    return true;
                }
            } else if kind.is_file() && path.extension().is_some_and(|ext| ext == "gcno") {
                return true;
            }
        }
        false
    }
    linked_build(project, component)
        .and_then(|build| build.directory.as_deref())
        .is_some_and(|directory| walk(&project.root.join(directory), &mut 200_000))
}

impl CppPlans {
    /// Record frozen's missing-database notes/gaps and the frozen reasons of C++ checks for a
    /// next-path plan (never for the legacy projection, whose engines report their own).
    pub fn frozen_next_plan(
        &mut self,
        project: &ResolvedProject,
        plan: &mut ici_application::plan::Plan,
        require_full: bool,
        environment: &BTreeMap<String, String>,
    ) {
        for component in project.components.iter().filter(|component| {
            plan.components.contains(&component.id)
                && component.languages.iter().any(|language| language == "cpp")
        }) {
            let loaded = match self.contexts.get(&component.id) {
                Some(context) => context.clone(),
                None => context(project, component, environment).0,
            };
            let missing = loaded.database_path.is_none();
            let hint = prepare_hint(project, component);
            let mut gaps = vec![];
            if !missing {
                // Frozen `compile_limitations` with a database: partial coverage is a note and a
                // gap, an error diagnostic a note, and a missing generated input, an unreached
                // root or an absent build definition a gap.
                let recorded: BTreeSet<&String> =
                    loaded.units.iter().map(|unit| &unit.source).collect();
                let expected: BTreeSet<&String> = loaded
                    .selected_sources
                    .iter()
                    .filter(|source| ici_cpp::is_translation_unit(source))
                    .collect();
                let uncovered: Vec<&str> = expected
                    .iter()
                    .filter(|source| !recorded.contains(*source))
                    .map(|source| source.as_str())
                    .collect();
                if !uncovered.is_empty() {
                    plan.limitations.push(format!(
                        "{}: compile database covers {}/{} translation units; missing: {}",
                        component.id,
                        expected.len() - uncovered.len(),
                        expected.len(),
                        uncovered.join(", ")
                    ));
                    gaps.push(format!(
                        "{}: {} translation unit(s) have no compile invocation",
                        component.id,
                        uncovered.len()
                    ));
                }
                for diagnostic in loaded
                    .diagnostics
                    .iter()
                    .filter(|item| item.level == "error")
                {
                    plan.limitations
                        .push(format!("{}: {}", component.id, diagnostic.message));
                }
                if let Some((_, reach)) = next_build_reach(project, component) {
                    gaps.extend(
                        reach
                            .iter()
                            .filter(|item| {
                                item.code.ends_with("-target-missing")
                                    || item.code == "build-definition-missing"
                            })
                            .map(|item| format!("{}: {}", component.id, item.message)),
                    );
                }
                gaps.extend(
                    missing_generated_inputs(project, component, &loaded)
                        .into_iter()
                        .map(|message| format!("{}: {message}", component.id)),
                );
            }
            for gap in gaps {
                if require_full {
                    plan.coverage_gaps.push(gap.clone());
                }
                self.next_scope_gaps.push(gap);
            }
            if missing {
                plan.limitations.push(format!(
                    "{}: no compilation database for its C++ scope ({hint})",
                    component.id
                ));
                let gap = format!(
                    "{}: no compilation database captured its C++ scope",
                    component.id
                );
                if require_full {
                    plan.coverage_gaps.push(gap.clone());
                }
                self.next_scope_gaps.push(gap);
            }
            let database =
                format!("no compilation database for the component's C++ scope — {hint}");
            let build = linked_build(project, component);
            let tested = plan.checks.iter().any(|planned| {
                planned.component_id == component.id && planned.check.id == "cpp.test"
            });
            for planned in plan
                .checks
                .iter_mut()
                .filter(|planned| planned.component_id == component.id)
            {
                let tidy_missing = || {
                    ToolRequest {
                        id: "clang-tidy".into(),
                        explicit: project
                            .tools
                            .get("clang-tidy")
                            .and_then(|tool| tool.path.as_deref())
                            .map(|path| crate::files::under(&project.root, path)),
                        project_root: project.root.clone(),
                        pack_root: None,
                        path_env: environment.get("PATH").cloned(),
                    }
                    .resolve()
                    .is_err()
                };
                let no_contract = build.is_some_and(|build| build.artifacts.is_empty());
                let reason = match planned.check.id.as_str() {
                    "cpp.compile" | "cpp.diagnostics" if missing => Some(database.clone()),
                    "cpp.tidy" if tidy_missing() => Some("clang-tidy is not available".into()),
                    "cpp.tidy" if missing => {
                        Some(format!("{database} — clang-tidy replays it via -p"))
                    }
                    "cpp.test" if build.is_none() => Some(
                        "no build unit linked — declare [builds.<id>] and attach it to the component"
                            .into(),
                    ),
                    "cpp.test"
                        if build.and_then(|build| declares_test_suite(&project.root, build))
                            == Some(false) =>
                    {
                        Some(
                            "no declared test suite — cmake add_test or a testlib/testcase .pro is what declares one"
                                .into(),
                        )
                    }
                    "cpp.coverage" if !tested => {
                        Some("cpp.coverage reads the test run's data — enable cpp.test".into())
                    }
                    "cpp.coverage" if !has_gcno(project, component) => Some(
                        "build is not instrumented — no .gcno under the linked build directories"
                            .into(),
                    ),
                    "cpp.artifact" | "cpp.binary-compat" if build.is_none() => Some(
                        "no build unit linked — artifact declarations live on [builds.<id>]".into(),
                    ),
                    "cpp.artifact" if no_contract => {
                        Some("no artifact contract — declare [builds.<id>] artifacts = [...]".into())
                    }
                    "cpp.binary-compat" if no_contract => Some(
                        "no artifact contract — declare [builds.<id>] artifacts = [...] so the binaries to inspect are named"
                            .into(),
                    ),
                    _ => None,
                };
                if let Some(reason) = reason {
                    // Where frozen's gate condition holds and the native planner blocked the
                    // check too, the run records frozen's reason (gate reasons, limitations).
                    if let ici_application::plan::Work::Blocked { reason: native } =
                        &mut planned.work
                    {
                        // A declared build this run tried to prepare keeps the actual cause, such
                        // as its missing tool.
                        if native.contains(" build cannot be prepared: ") {
                            continue;
                        }
                        native.clone_from(&reason);
                    }
                    self.next_blocked_reasons
                        .insert(planned.task_id.clone(), reason);
                }
            }
        }
    }

    /// Frozen human-plan reason of a natively blocked C++ check, when the frozen condition holds.
    pub fn frozen_blocked_reason(&self, task_id: &str) -> Option<&str> {
        self.next_blocked_reasons.get(task_id).map(String::as_str)
    }
}

#[cfg(test)]
mod tests {
    use super::cmake_declares_test;
    use std::path::Path;

    fn write(root: &Path, relative: &str, text: &str) {
        let path = root.join(relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }

    /// Frozen `_qmake_suites` (probe captured from the oracle, w-cppval-qmake-suites.log):
    /// SUBDIRS `.pro` files naming `QT += testlib` or `CONFIG += testcase` are suites, binary
    /// `TARGET=<name>` or the basename (`TARGET = x` with a space is frozen's non-match); a
    /// plain app is not, and a tree without one declares no suite.
    #[test]
    fn qmake_testlib_and_testcase_projects_declare_qtest_suites_like_frozen() {
        let project = tempfile::tempdir().unwrap();
        write(
            project.path(),
            "root.pro",
            "TEMPLATE = subdirs\nSUBDIRS = app tests check\n",
        );
        write(
            project.path(),
            "app/app.pro",
            "TEMPLATE = app\nQT += core\n",
        );
        write(
            project.path(),
            "tests/tests.pro",
            "QT += testlib\nTARGET=unit_tests\n",
        );
        write(
            project.path(),
            "check/check.pro",
            "CONFIG += testcase\nTARGET = spaced\n",
        );
        assert_eq!(
            crate::cpp::build_reach::qmake_test_binaries(project.path(), "root.pro"),
            [
                ("tests".to_owned(), "unit_tests".to_owned()),
                ("check".to_owned(), "check".to_owned())
            ]
        );
        write(
            project.path(),
            "plain.pro",
            "TEMPLATE = subdirs\nSUBDIRS = app\n",
        );
        assert!(
            crate::cpp::build_reach::qmake_test_binaries(project.path(), "plain.pro").is_empty()
        );
    }

    #[test]
    fn cmake_add_test_scan_reads_a_literal_test_and_follows_subdirectories() {
        let project = tempfile::tempdir().unwrap();
        write(
            project.path(),
            "CMakeLists.txt",
            "add_subdirectory(tests)\n",
        );
        write(
            project.path(),
            "tests/CMakeLists.txt",
            "add_test(NAME unit COMMAND t)\n",
        );
        assert!(cmake_declares_test(project.path(), "CMakeLists.txt"));
        write(
            project.path(),
            "tests/CMakeLists.txt",
            "# add_test(unit t)\n",
        );
        assert!(!cmake_declares_test(project.path(), "CMakeLists.txt"));
    }

    #[test]
    fn cmake_add_test_scan_reads_only_the_frozen_prefix_of_an_oversized_file() {
        let project = tempfile::tempdir().unwrap();
        // Frozen read 512 KiB + 1 bytes ("read truncated"): an add_test inside the prefix is
        // seen, one past it is not.
        let filler = format!("#{}\n", "x".repeat(600 * 1024));
        write(
            project.path(),
            "CMakeLists.txt",
            &format!("add_test(early t)\n{filler}"),
        );
        assert!(cmake_declares_test(project.path(), "CMakeLists.txt"));
        write(
            project.path(),
            "CMakeLists.txt",
            &format!("{}\nadd_test(late t)\n", "set(A B)\n".repeat(70 * 1024)),
        );
        assert!(!cmake_declares_test(project.path(), "CMakeLists.txt"));
    }

    #[test]
    fn cmake_add_test_scan_refuses_a_fifo_without_blocking() {
        let project = tempfile::tempdir().unwrap();
        let fifo = project.path().join("CMakeLists.txt");
        let status = std::process::Command::new("mkfifo")
            .arg(&fifo)
            .status()
            .unwrap();
        assert!(status.success());
        // No writer ever opens the FIFO: a blocking read would hang this test.
        assert!(!cmake_declares_test(project.path(), "CMakeLists.txt"));
    }

    #[test]
    fn cmake_add_test_scan_refuses_a_symlink_leaving_the_project_root() {
        let outside = tempfile::tempdir().unwrap();
        write(outside.path(), "CMakeLists.txt", "add_test(escaped t)\n");
        let project = tempfile::tempdir().unwrap();
        std::os::unix::fs::symlink(
            outside.path().join("CMakeLists.txt"),
            project.path().join("CMakeLists.txt"),
        )
        .unwrap();
        assert!(!cmake_declares_test(project.path(), "CMakeLists.txt"));
        std::fs::create_dir(project.path().join("sub")).unwrap();
        std::os::unix::fs::symlink(outside.path(), project.path().join("sub/out")).unwrap();
        write(project.path(), "inner.txt", "add_test(inside t)\n");
        std::fs::remove_file(project.path().join("CMakeLists.txt")).unwrap();
        write(
            project.path(),
            "CMakeLists.txt",
            "add_subdirectory(sub/out)\n",
        );
        assert!(!cmake_declares_test(project.path(), "CMakeLists.txt"));
        // A symlink that stays inside the root is still followed, as frozen did.
        std::os::unix::fs::symlink(
            project.path().join("inner.txt"),
            project.path().join("sub/CMakeLists.txt"),
        )
        .unwrap();
        write(project.path(), "CMakeLists.txt", "add_subdirectory(sub)\n");
        assert!(cmake_declares_test(project.path(), "CMakeLists.txt"));
    }
}
