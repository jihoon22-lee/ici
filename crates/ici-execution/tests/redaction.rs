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
