//! Each `dispatch` masks its own environment's sensitive values and forgets them when it
//! returns: a later command in the same process, or one running at the same time on another
//! thread, is not changed by them. The command's error leaves it already masked.
use ici_cli::{commands::dispatch, registry::parse};
use ici_execution::redaction::{REDACTED, redact_text};
use std::{collections::BTreeMap, path::Path};

fn environment(value: Option<&str>) -> BTreeMap<String, String> {
    let mut environment = BTreeMap::from([("PATH".to_owned(), "/usr/bin:/bin".to_owned())]);
    if let Some(value) = value {
        environment.insert("ICI_FIXTURE_DEPLOY_TOKEN".to_owned(), value.to_owned());
    }
    environment
}

/// `next diff` names the baseline it cannot read in its error.
fn refused(root: &Path, name: &str, value: Option<&str>) -> String {
    let baseline = format!("{name}.json");
    let invocation = parse([
        "ici",
        "next",
        "diff",
        baseline.as_str(),
        "--result",
        "x.json",
    ])
    .unwrap();
    let error = dispatch(&invocation, root, &environment(value)).unwrap_err();
    ici_cli::commands::diagnostic(&error)
}

#[test]
fn a_command_masks_its_own_values_and_forgets_them_when_it_returns() {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path();
    let masked = refused(root, "abcd", Some("abcd"));
    assert!(masked.contains(&format!("/{REDACTED}.json")), "{masked}");
    assert!(!masked.contains("abcd"), "{masked}");
    // An ordinary word holding the short value's letters is left readable.
    let word = refused(root, "abcdef", Some("abcd"));
    assert!(word.contains("/abcdef.json"), "{word}");
    // After the error, nothing of the command stays: the next command shows the name.
    assert_eq!(redact_text("abcd"), "abcd");
    let plain = refused(root, "abcd", None);
    assert!(plain.contains("/abcd.json"), "{plain}");
    // The same after a command that succeeds.
    let preview = parse(["ici", "init", "--preview"]).unwrap();
    std::fs::write(root.join("app.py"), "x = 1\n").unwrap();
    let output = dispatch(&preview, root, &environment(Some("abcd"))).unwrap();
    assert_eq!(output.exit_code, 0, "{}", output.stderr);
    assert_eq!(redact_text("abcd"), "abcd");
    assert!(refused(root, "abcd", None).contains("/abcd.json"));
}

#[test]
fn commands_running_at_the_same_time_keep_their_own_values() {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().to_path_buf();
    let barrier = std::sync::Barrier::new(2);
    std::thread::scope(|scope| {
        let runs: Vec<_> = [("wxyz", "abcd"), ("abcd", "wxyz")]
            .into_iter()
            .map(|(own, other)| {
                let (root, barrier) = (&root, &barrier);
                scope.spawn(move || {
                    for _ in 0..20 {
                        barrier.wait();
                        let own_error = refused(root, own, Some(own));
                        let other_error = refused(root, other, Some(own));
                        assert!(!own_error.contains(own), "{own_error}");
                        assert!(
                            other_error.contains(&format!("/{other}.json")),
                            "{other_error}"
                        );
                    }
                })
            })
            .collect();
        for run in runs {
            run.join().unwrap();
        }
    });
}
