use ici_execution::redaction::{REDACTED, redact_value};
use serde_json::json;
#[test]
fn structured_environment_and_headers_hide_values_even_without_secret_prefixes() {
    let value = json!({"env_overlay":{"GITHUB_TOKEN":"opaque-credential","DB_PASSWORD":"opaque-password","CLIENT_SECRET":"opaque-client","token_count":7,"PATH":"/tools"},"headers":{"Authorization":"opaque-auth","Cookie":"session-value"},"message":"Bearer private-token"});
    let safe = redact_value(&value);
    for key in ["GITHUB_TOKEN", "DB_PASSWORD", "CLIENT_SECRET"] {
        assert_eq!(safe["env_overlay"][key], REDACTED);
    }
    assert_eq!(safe["headers"]["Authorization"], REDACTED);
    assert_eq!(safe["headers"]["Cookie"], REDACTED);
    assert_eq!(safe["env_overlay"]["token_count"], 7);
    assert_eq!(safe["env_overlay"]["PATH"], "/tools");
    assert!(!safe.to_string().contains("opaque-"));
    assert!(!safe.to_string().contains("private-token"));
    assert_eq!(value["env_overlay"]["GITHUB_TOKEN"], "opaque-credential");
}
fn environment(pairs: &[(&str, &str)]) -> std::collections::BTreeMap<String, String> {
    pairs
        .iter()
        .map(|(key, value)| ((*key).to_owned(), (*value).to_owned()))
        .collect()
}
/// A tool that gets the whole environment may print a private value without its variable's
/// name. A long sensitive value is masked wherever it appears; a variable whose name is not
/// sensitive is not.
#[test]
fn a_remembered_sensitive_value_is_masked_without_its_name() {
    use ici_execution::redaction::{command_scope, redact_text};
    let _scope = command_scope(&environment(&[
        ("ICI_FIXTURE_DEPLOY_TOKEN", "synthetic-value-4c1d"),
        ("ICI_FIXTURE_MODE", "synthetic-mode-9e2f"),
    ]));
    let text = redact_text(
        "tool said synthetic-value-4c1d, xsynthetic-value-4c1dx and synthetic-mode-9e2f",
    );
    assert_eq!(
        text,
        format!("tool said {REDACTED}, x{REDACTED}x and synthetic-mode-9e2f")
    );
    let value = redact_value(&json!({"message": "x synthetic-value-4c1d"}));
    assert_eq!(value["message"], format!("x {REDACTED}"));
}
/// A short value (4–5 characters here) is masked where it stands as its own token: the whole
/// text, `private=<value>`, a quoted or path-separated word. A word that merely contains its
/// letters stays readable. Structured values under a sensitive key are masked at any length.
#[test]
fn a_short_sensitive_value_is_masked_as_a_token_and_words_stay_intact() {
    use ici_execution::redaction::{command_scope, redact_text};
    let _scope = command_scope(&environment(&[
        ("ICI_FIXTURE_SHORT_TOKEN", "pass"),
        ("ICI_FIXTURE_PIN_SECRET", "q7z2k"),
    ]));
    for (text, expected) in [
        ("pass", REDACTED.to_owned()),
        ("q7z2k\n", format!("{REDACTED}\n")),
        ("private=pass", format!("private={REDACTED}")),
        ("private=q7z2k;", format!("private={REDACTED};")),
        (
            "value 'q7z2k' at /x/pass.json",
            format!("value '{REDACTED}' at /x/{REDACTED}.json"),
        ),
        (
            "passed bypass compass passport",
            "passed bypass compass passport".to_owned(),
        ),
        ("q7z2kq7z2k and xq7z2k", "q7z2kq7z2k and xq7z2k".to_owned()),
        ("pass_through pass2", "pass_through pass2".to_owned()),
    ] {
        assert_eq!(redact_text(text), expected, "{text}");
    }
    let value = redact_value(&json!({"ICI_FIXTURE_SHORT_TOKEN": "pa", "note": "pass"}));
    assert_eq!(value["ICI_FIXTURE_SHORT_TOKEN"], REDACTED);
    assert_eq!(value["note"], REDACTED);
}
/// Text passes several boundaries; masking it again changes nothing, even when a value holds
/// the marker or part of it.
#[test]
fn redaction_is_idempotent_even_for_values_that_hold_the_marker() {
    use ici_execution::redaction::{command_scope, redact_text};
    let _scope = command_scope(&environment(&[
        ("A_TOKEN", "ACTED***"),
        ("B_TOKEN", "REDACTED"),
        ("C_TOKEN", "***"),
        ("D_TOKEN", "*"),
        ("E_SECRET", "synthetic-value-4c1d"),
    ]));
    for text in [
        "ACTED*** REDACTED *** * synthetic-value-4c1d",
        REDACTED,
        "x***REDACTED***y ***REDACTED******REDACTED***",
        "token=\"abc\" password: 'x' Bearer abc.def https://user:pw@host/ ghp_abcdefghijklmnopqrstuvwxyz",
        "-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----",
    ] {
        let once = redact_text(text);
        assert_eq!(redact_text(&once), once, "{text}");
        assert!(!once.contains("synthetic-value-4c1d"), "{once}");
        let value = redact_value(&json!({"message": text}));
        assert_eq!(redact_value(&value), value, "{text}");
    }
}
/// The values belong to one command: they stop being masked when its scope ends (also after an
/// early return), a nested command masks both, another thread masks only its own, and a worker
/// masks what the command that started it masks once it enters that context.
#[test]
fn a_command_scope_is_per_command_and_per_thread() {
    use ici_execution::redaction::{command_scope, current, redact_text};
    let outer = environment(&[("OUTER_TOKEN", "abcd")]);
    let inner = environment(&[("INNER_TOKEN", "wxyz")]);
    let early = |fail: bool| -> Result<String, String> {
        let _scope = command_scope(&outer);
        if fail {
            return Err(redact_text("abcd"));
        }
        Ok(redact_text("abcd"))
    };
    assert_eq!(early(true), Err(REDACTED.to_owned()));
    assert_eq!(redact_text("abcd wxyz"), "abcd wxyz");
    assert_eq!(early(false), Ok(REDACTED.to_owned()));
    assert_eq!(redact_text("abcd wxyz"), "abcd wxyz");
    {
        let _outer = command_scope(&outer);
        {
            let _inner = command_scope(&inner);
            assert_eq!(redact_text("abcd wxyz"), format!("{REDACTED} {REDACTED}"));
        }
        assert_eq!(redact_text("abcd wxyz"), format!("{REDACTED} wxyz"));
        let context = current();
        let unaware = std::thread::spawn(|| redact_text("abcd")).join().unwrap();
        assert_eq!(unaware, "abcd");
        let worker = std::thread::spawn(move || {
            let _scope = context.enter();
            redact_text("abcd")
        })
        .join()
        .unwrap();
        assert_eq!(worker, REDACTED);
        let other = std::thread::spawn(move || {
            let _scope = command_scope(&inner);
            redact_text("abcd wxyz")
        })
        .join()
        .unwrap();
        assert_eq!(other, format!("abcd {REDACTED}"));
    }
    assert_eq!(redact_text("abcd wxyz"), "abcd wxyz");
}
