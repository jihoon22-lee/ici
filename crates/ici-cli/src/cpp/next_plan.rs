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
/// Native builds carry no prepare vector, so a linked build always names the configure gap.
pub(super) fn prepare_hint(project: &ResolvedProject, component: &Component) -> String {
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
/// one, `None` (not decided here) for qmake, whose testlib/testcase SUBDIRS reader is not ported.
fn declares_test_suite(root: &Path, build: &ici_config::Build) -> Option<bool> {
    let Some(definition) = build.project.as_deref() else {
        return Some(false);
    };
    match build.system.as_deref() {
        Some("cmake") => Some(cmake_declares_test(root, definition)),
        Some("qmake") => None,
        _ => Some(false),
    }
}

/// Frozen `resolve_cmake_project(...).tests`: comments (`#` and `#[[ ]]`) are stripped, literal
/// `add_subdirectory` entries with their own CMakeLists.txt are followed (depth 16, 512 KiB
/// read per file, each file once), and an `add_test` whose (NAME-form or first) name is a
/// literal token declares the build's suite. Nothing is evaluated.
fn cmake_declares_test(root: &Path, definition: &str) -> bool {
    const MAX_DEPTH: usize = 16;
    const MAX_BYTES: usize = 512 * 1024;
    let literal = |token: &str| {
        !token.is_empty() && !token.contains('$') && !token.contains('(') && !token.contains('"')
    };
    let strip = |text: &str| -> String {
        let mut out = String::with_capacity(text.len());
        let mut rest = text;
        while let Some(index) = rest.find('#') {
            out.push_str(&rest[..index]);
            let tail = &rest[index..];
            rest = if let Some(block) = tail.strip_prefix("#[[") {
                block.find("]]").map_or("", |end| &block[end + 2..])
            } else {
                tail.find('\n').map_or("", |end| &tail[end..])
            };
        }
        out.push_str(rest);
        out
    };
    let commands = |text: &str| -> Vec<(String, Vec<String>)> {
        // Frozen `_commands`: `name(args)` with at most one nested parenthesis level.
        let bytes = text.as_bytes();
        let mut found = vec![];
        let mut index = 0;
        while index < bytes.len() {
            let start = index;
            if bytes[index].is_ascii_alphabetic() || bytes[index] == b'_' {
                while index < bytes.len()
                    && (bytes[index].is_ascii_alphanumeric() || bytes[index] == b'_')
                {
                    index += 1;
                }
                let name = &text[start..index];
                let mut cursor = index;
                while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() {
                    cursor += 1;
                }
                if cursor < bytes.len() && bytes[cursor] == b'(' {
                    let mut depth = 0usize;
                    let mut end = None;
                    for (offset, byte) in bytes[cursor..].iter().enumerate() {
                        match byte {
                            b'(' => {
                                depth += 1;
                                if depth > 2 {
                                    break;
                                }
                            }
                            b')' => {
                                depth -= 1;
                                if depth == 0 {
                                    end = Some(cursor + offset);
                                    break;
                                }
                            }
                            _ => {}
                        }
                    }
                    if let Some(end) = end {
                        let args = text[cursor + 1..end]
                            .split_whitespace()
                            .map(str::to_owned)
                            .collect();
                        found.push((name.to_ascii_lowercase(), args));
                        index = end + 1;
                        continue;
                    }
                }
                continue;
            }
            index += 1;
        }
        found
    };
    let mut seen = std::collections::BTreeSet::new();
    let mut pending = vec![(definition.to_owned(), 0usize)];
    while let Some((lists, depth)) = pending.pop() {
        let path = root.join(&lists);
        let canonical = path.canonicalize().unwrap_or_else(|_| path.clone());
        if !seen.insert(canonical) || depth > MAX_DEPTH {
            continue;
        }
        let Some(bytes) = read_contained_lists(root, &path, MAX_BYTES + 1) else {
            continue;
        };
        let text = String::from_utf8_lossy(&bytes).into_owned();
        let base = Path::new(&lists).parent().unwrap_or(Path::new(""));
        for (name, args) in commands(&strip(&text)) {
            match name.as_str() {
                "add_test" if !args.is_empty() => {
                    let test = args
                        .iter()
                        .position(|arg| arg == "NAME")
                        .and_then(|index| args.get(index + 1))
                        .unwrap_or(&args[0]);
                    if literal(test) {
                        return true;
                    }
                }
                "add_subdirectory" if !args.is_empty() && literal(&args[0]) => {
                    let child = base.join(&args[0]).join("CMakeLists.txt");
                    if root.join(&child).is_file() {
                        pending.push((child.to_string_lossy().into_owned(), depth + 1));
                    }
                }
                _ => {}
            }
        }
    }
    false
}

/// Frozen `cpp_gcno`: instrumentation notes under the linked build directory.
/// One CMakeLists.txt read for the `add_test` scan: frozen read the first 512 KiB + 1 bytes
/// of whatever the path named. The read here is bounded before it happens (frozen read the
/// whole file and then truncated), a FIFO/device is refused instead of blocking, and a file
/// whose resolved path leaves the project root is refused (frozen followed it). A refused file
/// is what frozen did with an unreadable one: it declares nothing.
fn read_contained_lists(root: &Path, path: &Path, limit: usize) -> Option<Vec<u8>> {
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
            let missing = match self.contexts.get(&component.id) {
                Some(context) => context.database_path.is_none(),
                None => context(project, component, environment)
                    .0
                    .database_path
                    .is_none(),
            };
            let hint = prepare_hint(project, component);
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
