use ici_cpp::{BuildLease, BuildRequest, ContextRequest, load_compilation_context, plan_build};
use ici_execution::{Cancellation, Runner, Scheduler};
use std::{fs, path::Path, time::Duration};

fn run(root: &Path, request: &BuildRequest) -> (ici_cpp::BuildPlan, ici_cpp::CompilationContext) {
    let plan = plan_build(root, request).unwrap();
    let cancellation = Cancellation::default();
    let lease = BuildLease::acquire(root, &plan, &cancellation, Duration::from_secs(3)).unwrap();
    lease.prepare(&plan).unwrap();
    let mut scheduler = Scheduler::new(Runner::default(), 2);
    scheduler.lock_directory = Some(root.join(".ici/rust-v1/locks/tasks"));
    let result = scheduler.run(&plan.tasks, &cancellation).unwrap();
    for outcome in result.outcomes {
        assert!(
            outcome.prerequisite_succeeded(),
            "{}: {:?}, {:?}\n{}",
            outcome.task_id,
            outcome.state,
            outcome.error,
            String::from_utf8_lossy(&outcome.stderr)
        );
    }
    let context = if request.system == "qmake" {
        lease.finalize_qmake(&plan).unwrap()
    } else {
        load_compilation_context(
            root,
            &ContextRequest {
                database: Some(plan.compilation_database.clone()),
                selected_sources: vec!["main.cpp".into()],
                variant: Some(request.variant.clone()),
                ..Default::default()
            },
        )
    };
    (plan, context)
}
fn capture_helper() -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    let source = dir.path().join("helper.rs");
    fs::write(&source,r#"fn main(){let mut args=std::env::args_os();args.next();let mode=args.next().unwrap();let rest:Vec<_>=args.collect();let code=if mode=="__qmake-record"{ici_cpp::recorder_main(&rest)}else if mode=="__qmake-configure-capture"{ici_cpp::qmake_configure_capture_main(&rest)}else{125};std::process::exit(code)}"#).unwrap();
    let test = std::env::current_exe().unwrap();
    let dependencies = test.parent().unwrap();
    let library = fs::read_dir(dependencies)
        .unwrap()
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| {
            path.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("libici_cpp-")
                && path.extension().is_some_and(|ext| ext == "rlib")
        })
        .max_by_key(|path| path.metadata().unwrap().modified().unwrap())
        .expect("compiled ici-cpp rlib");
    let output =
        std::process::Command::new(std::env::var_os("RUSTC").unwrap_or_else(|| "rustc".into()))
            .arg("--edition=2024")
            .arg(&source)
            .arg("-L")
            .arg(format!("dependency={}", dependencies.display()))
            .arg("--extern")
            .arg(format!("ici_cpp={}", library.display()))
            .arg("-o")
            .arg(dir.path().join("capture helper"))
            .output()
            .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    dir
}

#[test]
fn cmake_planning_is_read_only_and_actual_build_produces_measured_compile_context() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(root.path().join("CMakeLists.txt"),"cmake_minimum_required(VERSION 3.16)\nproject(fixture LANGUAGES CXX)\nadd_executable(fixture main.cpp)\ntarget_compile_features(fixture PRIVATE cxx_std_17)\n").unwrap();
    let request = BuildRequest {
        jobs: 1,
        ..Default::default()
    };
    let plan = plan_build(root.path(), &request).unwrap();
    assert!(!root.path().join(".ici").exists());
    assert!(plan.tasks.iter().all(|task| task.mutating()));
    let (plan, context) = run(root.path(), &request);
    assert!(context.complete(), "{:?}", context.diagnostics);
    assert_eq!(context.units[0].source, "main.cpp");
    assert_eq!(context.units[0].target, "fixture");
    assert!(root.path().join(plan.shadow).join("fixture").is_file());
}

#[test]
fn qt5_and_qt6_capture_observed_compilers_generated_sources_and_space_containing_recorder_path() {
    let helper = capture_helper();
    for qmake in ["/usr/bin/qmake", "/usr/bin/qmake6"] {
        let root = tempfile::Builder::new()
            .prefix("ici cpp fixture ")
            .tempdir()
            .unwrap();
        fs::write(
            root.path().join("main.cpp"),
            "#include \"item.h\"\n#include \"ui_form.h\"\nint main(){Item item;return 0;}\n",
        )
        .unwrap();
        fs::write(root.path().join("item.h"),"#include <QObject>\nclass Item:public QObject {Q_OBJECT public:using QObject::QObject;};\n").unwrap();
        fs::write(root.path().join("data.txt"), "fixture data").unwrap();
        fs::write(
            root.path().join("data.qrc"),
            "<RCC><qresource prefix=\"/\"><file>data.txt</file></qresource></RCC>",
        )
        .unwrap();
        fs::write(root.path().join("form.ui"),"<ui version=\"4.0\"><class>Widget</class><widget class=\"QWidget\" name=\"Widget\"/></ui>").unwrap();
        fs::write(root.path().join("app.pro"),"QT += core widgets\nCONFIG += console c++17\nCONFIG -= app_bundle\nTEMPLATE = app\nSOURCES += main.cpp\nHEADERS += item.h\nRESOURCES += data.qrc\nFORMS += form.ui\n").unwrap();
        let mut request = BuildRequest {
            system: "qmake".into(),
            project: "app.pro".into(),
            ici_binary: Some(helper.path().join("capture helper")),
            jobs: 1,
            ..Default::default()
        };
        request.tools.insert("qmake".into(), qmake.into());
        let (plan, context) = run(root.path(), &request);
        assert!(context.complete(), "{:?}", context.diagnostics);
        assert!(context.units.iter().any(|unit| unit.source == "main.cpp"));
        assert!(
            context
                .units
                .iter()
                .any(|unit| unit.source.ends_with("moc_item.cpp"))
        );
        assert!(
            context
                .units
                .iter()
                .any(|unit| unit.source.ends_with("qrc_data.cpp"))
        );
        assert!(root.path().join(&plan.shadow).join("ui_form.h").is_file());
        assert!(context.units.iter().all(|unit| {
            !unit
                .original_argv
                .iter()
                .any(|arg| arg.contains("__qmake-record"))
        }));
    }
}

#[test]
fn explicit_make_argv_builds_in_owned_shadow_without_running_during_plan() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(
        root.path().join("fixture.mk"),
        format!(
            "all:\n\tg++ '{}' -o fixture\n",
            root.path().join("main.cpp").display()
        ),
    )
    .unwrap();
    let request = BuildRequest {
        system: "make".into(),
        build_argv: vec![
            "make".into(),
            "-f".into(),
            root.path().join("fixture.mk").display().to_string(),
            "-j{jobs}".into(),
        ],
        jobs: 1,
        ..Default::default()
    };
    let plan = plan_build(root.path(), &request).unwrap();
    assert!(!root.path().join(".ici").exists());
    let lease = BuildLease::acquire(
        root.path(),
        &plan,
        &Cancellation::default(),
        Duration::from_secs(1),
    )
    .unwrap();
    lease.prepare(&plan).unwrap();
    let result = Scheduler::new(Runner::default(), 1)
        .run(&plan.tasks, &Cancellation::default())
        .unwrap();
    assert!(result.outcomes[0].prerequisite_succeeded());
    assert!(root.path().join(&plan.shadow).join("fixture").is_file());
    let missing = load_compilation_context(
        root.path(),
        &ContextRequest {
            database: Some(plan.compilation_database),
            ..Default::default()
        },
    );
    assert!(!missing.complete());
}

#[test]
fn build_lease_serializes_whole_transaction_and_unowned_or_hardlinked_state_is_preserved() {
    let root = tempfile::tempdir().unwrap();
    let request = BuildRequest::default();
    let plan = plan_build(root.path(), &request).unwrap();
    let cancellation = Cancellation::default();
    let lease =
        BuildLease::acquire(root.path(), &plan, &cancellation, Duration::from_secs(1)).unwrap();
    assert!(
        BuildLease::acquire(root.path(), &plan, &cancellation, Duration::from_millis(30)).is_err()
    );
    lease.prepare(&plan).unwrap();
    drop(lease);
    assert!(BuildLease::acquire(root.path(), &plan, &cancellation, Duration::from_secs(1)).is_ok());
    let other = plan_build(
        root.path(),
        &BuildRequest {
            id: "unowned".into(),
            ..request
        },
    )
    .unwrap();
    fs::create_dir_all(root.path().join(&other.shadow)).unwrap();
    let user = root.path().join(&other.shadow).join("user-data");
    fs::write(&user, "preserve").unwrap();
    assert!(ici_cpp::prepare_owned_shadow(root.path(), &other).is_err());
    assert_eq!(fs::read_to_string(user).unwrap(), "preserve");
    let mut capture = plan.clone();
    capture.capture_journal = Some(format!("{}/capture.jsonl", capture.shadow));
    ici_cpp::prepare_owned_shadow(root.path(), &capture).unwrap();
    let outside = root.path().join("outside.txt");
    fs::write(&outside, "do not truncate").unwrap();
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(&outside, fs::Permissions::from_mode(0o600)).unwrap();
    let journal = root.path().join(capture.capture_journal.as_ref().unwrap());
    fs::remove_file(&journal).unwrap();
    fs::hard_link(&outside, &journal).unwrap();
    assert!(ici_cpp::prepare_owned_shadow(root.path(), &capture).is_err());
    assert_eq!(fs::read_to_string(outside).unwrap(), "do not truncate");
}

#[test]
fn cancelled_waiter_cannot_reset_an_active_capture_and_drop_releases_transaction() {
    let root = tempfile::tempdir().unwrap();
    let mut plan = plan_build(root.path(), &BuildRequest::default()).unwrap();
    plan.capture_journal = Some(format!("{}/capture.jsonl", plan.shadow));
    let cancellation = Cancellation::default();
    let lease =
        BuildLease::acquire(root.path(), &plan, &cancellation, Duration::from_secs(2)).unwrap();
    lease.prepare(&plan).unwrap();
    let journal = root.path().join(plan.capture_journal.as_ref().unwrap());
    fs::write(&journal, "active transaction evidence\n").unwrap();
    let waiter_cancel = Cancellation::default();
    let thread_cancel = waiter_cancel.clone();
    let wait_root = root.path().to_path_buf();
    let wait_plan = plan.clone();
    let waiter = std::thread::spawn(move || {
        BuildLease::acquire(
            &wait_root,
            &wait_plan,
            &thread_cancel,
            Duration::from_secs(10),
        )
        .err()
        .unwrap()
    });
    std::thread::sleep(Duration::from_millis(25));
    waiter_cancel.cancel();
    assert!(
        waiter
            .join()
            .unwrap()
            .message
            .to_ascii_lowercase()
            .contains("cancel")
    );
    assert_eq!(
        fs::read_to_string(&journal).unwrap(),
        "active transaction evidence\n"
    );
    drop(lease);
    let next =
        BuildLease::acquire(root.path(), &plan, &cancellation, Duration::from_secs(1)).unwrap();
    next.prepare(&plan).unwrap();
    assert!(fs::read(&journal).unwrap().is_empty());
}

#[test]
fn journal_and_database_symlinks_or_hardlinks_never_modify_their_targets() {
    use std::os::unix::fs::symlink;
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    let mut plan = plan_build(root.path(), &BuildRequest::default()).unwrap();
    plan.capture_journal = Some(format!("{}/capture.jsonl", plan.shadow));
    let lease = BuildLease::acquire(
        root.path(),
        &plan,
        &Cancellation::default(),
        Duration::from_secs(1),
    )
    .unwrap();
    lease.prepare(&plan).unwrap();
    let journal = root.path().join(plan.capture_journal.as_ref().unwrap());
    let outside = root.path().join("user-data");
    fs::write(&outside, "preserve original").unwrap();
    fs::remove_file(&journal).unwrap();
    symlink(&outside, &journal).unwrap();
    assert!(lease.prepare(&plan).is_err());
    fs::remove_file(&journal).unwrap();
    lease.prepare(&plan).unwrap();
    ici_cpp::record_compiler_invocation(
        &journal,
        &[
            "g++".into(),
            "-c".into(),
            root.path().join("main.cpp").display().to_string(),
        ],
        &root.path().join(&plan.shadow),
    )
    .unwrap();
    let database = root.path().join(&plan.compilation_database);
    fs::hard_link(&outside, &database).unwrap();
    assert!(lease.finalize_qmake(&plan).is_err());
    assert_eq!(fs::read_to_string(&outside).unwrap(), "preserve original");
    fs::remove_file(&database).unwrap();
    symlink(&outside, &database).unwrap();
    assert!(lease.finalize_qmake(&plan).is_err());
    assert_eq!(fs::read_to_string(outside).unwrap(), "preserve original");
}

fn count_gcno(directory: &Path) -> usize {
    fs::read_dir(directory)
        .unwrap()
        .filter_map(Result::ok)
        .map(|entry| {
            let kind = entry.file_type().unwrap();
            if kind.is_dir() {
                count_gcno(&entry.path())
            } else {
                usize::from(
                    entry
                        .path()
                        .extension()
                        .is_some_and(|extension| extension == "gcno"),
                )
            }
        })
        .sum()
}
#[test]
fn actual_cmake_and_qmake_coverage_variants_preserve_native_flags_through_recorder_reconfigure() {
    let helper = capture_helper();
    for system in ["cmake", "qmake5", "qmake6"] {
        let root = tempfile::tempdir().unwrap();
        fs::write(root.path().join("main.cpp"),"#ifndef CAPTURE_FEATURE\n#error native project flags were lost\n#endif\nint main(){return 0;}\n").unwrap();
        let mut request = BuildRequest {
            variant: "coverage".into(),
            ici_binary: Some(helper.path().join("capture helper")),
            ..Default::default()
        };
        if system == "cmake" {
            fs::write(root.path().join("CMakeLists.txt"),"cmake_minimum_required(VERSION 3.16)\nproject(coverage_fixture LANGUAGES CXX)\nadd_executable(fixture main.cpp)\n").unwrap();
            request.extra_cmake_args = vec!["-DCMAKE_CXX_FLAGS:STRING=-DCAPTURE_FEATURE=1".into()];
        } else {
            fs::write(root.path().join("app.pro"),"QT += core\nQT -= gui\nCONFIG += console\nCONFIG -= app_bundle\nTEMPLATE=app\nSOURCES += main.cpp\n").unwrap();
            request.system = "qmake".into();
            request.project = "app.pro".into();
            request.tools.insert(
                "qmake".into(),
                if system == "qmake5" {
                    "/usr/bin/qmake"
                } else {
                    "/usr/bin/qmake6"
                }
                .into(),
            );
            request.extra_qmake_args = vec!["DEFINES+=CAPTURE_FEATURE=1".into()];
        }
        let (plan, context) = run(root.path(), &request);
        assert!(context.complete(), "{system}: {:?}", context.diagnostics);
        let main = context
            .units
            .iter()
            .find(|unit| unit.source == "main.cpp")
            .unwrap();
        assert!(main.argv.contains(&"--coverage".into()));
        assert!(
            main.defines
                .iter()
                .any(|define| define.name == "CAPTURE_FEATURE")
        );
        assert!(
            count_gcno(&root.path().join(plan.shadow)) > 0,
            "{system} produced no coverage note files"
        );
    }
}
#[test]
fn sanitizer_variant_plans_are_debug_and_keep_output_and_install_hooks_out_of_added_arguments() {
    let root = tempfile::tempdir().unwrap();
    for (variant, flag) in [
        ("sanitize", "-fsanitize=address,undefined"),
        ("thread-sanitize", "-fsanitize=thread"),
    ] {
        let request = BuildRequest {
            variant: variant.into(),
            ..Default::default()
        };
        let plan = plan_build(root.path(), &request).unwrap();
        assert!(
            plan.tasks[0]
                .argv
                .contains(&"-DCMAKE_BUILD_TYPE=Debug".into())
        );
        assert!(
            plan.tasks[0]
                .argv
                .iter()
                .any(|argument| argument.contains(flag))
        );
        assert!(!root.path().join(".ici").exists());
    }
    assert!(
        plan_build(
            root.path(),
            &BuildRequest {
                extra_cxx_flags: vec!["-o/user/data".into()],
                ..Default::default()
            }
        )
        .is_err()
    );
    assert!(
        plan_build(
            root.path(),
            &BuildRequest {
                extra_qmake_args: vec!["-set".into(), "GLOBAL".into(), "value".into()],
                ..Default::default()
            }
        )
        .is_err()
    );
}
