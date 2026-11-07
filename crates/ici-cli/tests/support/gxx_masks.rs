//! What a g++ release itself writes into a legacy lint capture, for the CI twins of replays
//! captured with another g++ (`support/host_tools.rs`). Only a compiler diagnostic's own text and
//! columns and the version banner: ici's wording, every target name, status, path and line, the
//! summaries and counters stay compared.
use serde_json::{Value, json};

/// A target or finding label that carries a compiler diagnostic (`C++Syntax:<digest>` and
/// `C++Syntax:fallback` carry ici's own "completed without diagnostics").
fn compiler_diagnostic(label: &Value) -> bool {
    label
        .as_str()
        .is_some_and(|label| label == "C++Syntax" || label.starts_with("Compiler:"))
}

/// Mask the g++ release's text on both captures before comparing them.
pub fn mask_gxx_release(view: &mut Value) {
    for target in view["targets"].as_array_mut().into_iter().flatten() {
        if compiler_diagnostic(&target["target_name"]) {
            target["message"] = json!("<host>");
            target["start_column"] = json!("<host>");
        }
    }
    for finding in view["findings"].as_array_mut().into_iter().flatten() {
        if finding["tool_name"] == "g++" && finding["tool_version"] != "" {
            finding["tool_version"] = json!("<host>");
        }
        if compiler_diagnostic(&finding["primary_location"]["label"]) {
            finding["message"] = json!("<host>");
            // The fingerprint hashes the location's columns.
            finding["fingerprint"] = json!("<host>");
            finding["primary_location"]["start_column"] = json!("<host>");
            finding["primary_location"]["end_column"] = json!("<host>");
        }
    }
    for record in view["tool_evidence"].as_array_mut().into_iter().flatten() {
        if record["name"] == "g++" && record["version"] != "" {
            record["version"] = json!("<host>");
        }
    }
}
