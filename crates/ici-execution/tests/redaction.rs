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
/// A tool that gets the whole environment may print a private value without its variable's
/// name. Remembered sensitive values are masked in any text that crosses a boundary; a short
/// value or a variable whose name is not sensitive is not.
#[test]
fn a_remembered_sensitive_value_is_masked_without_its_name() {
    use ici_execution::redaction::{redact_text, remember_sensitive_environment};
    remember_sensitive_environment(&std::collections::BTreeMap::from([
        (
            "ICI_FIXTURE_DEPLOY_TOKEN".to_owned(),
            "synthetic-value-4c1d".to_owned(),
        ),
        ("ICI_FIXTURE_SHORT_TOKEN".to_owned(), "abc".to_owned()),
        (
            "ICI_FIXTURE_MODE".to_owned(),
            "synthetic-mode-9e2f".to_owned(),
        ),
    ]));
    let text = redact_text("tool said synthetic-value-4c1d, abc and synthetic-mode-9e2f");
    assert_eq!(
        text,
        format!("tool said {REDACTED}, abc and synthetic-mode-9e2f")
    );
    let value = redact_value(&json!({"message": "x synthetic-value-4c1d"}));
    assert_eq!(value["message"], format!("x {REDACTED}"));
}
