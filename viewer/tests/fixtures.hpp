#pragma once

// Shared report fixtures. Keeping them here rather than inline in each test
// avoids the copy-paste that ici's dup engine flags as a Type-2 clone.

#include <fstream>
#include <sstream>
#include <string>

// Loads a fixture from tests/data/.
//
// The path is relative to the project root, which is where ici runs C++ test
// binaries. That used to be untrue — the working directory depended on which
// engine launched the test and on whether gcov was installed — and this
// function carried a list of candidate prefixes and a __FILE__ fallback to
// work around it. ici 0.5.5 fixed the engines, so the workaround is gone.
inline std::string readFixture(const std::string& name) {
    std::ifstream input("tests/data/" + name);
    if (!input) {
        return std::string();
    }
    std::stringstream buffer;
    buffer << input.rdbuf();
    return buffer.str();
}

// A complete, valid v2 report with one required engine that SKIPs — the shape
// that makes ici escalate the whole suite to ERROR while printing "Error: 0".
inline std::string skipEscalationReport() {
    return R"({
  "schema_version": "ici.result/v2",
  "suite_status": "ERROR",
  "duration": 1.5,
  "passed_count": 1, "warned_count": 0, "failed_count": 0,
  "error_count": 0, "skipped_count": 1, "total_count": 2,
  "tem_score": 4.5, "max_tem_score": 5.0,
  "results": [
    {"engine_name": "lint", "status": "PASS", "summary": "clean", "duration": 0.5,
     "required": true, "evidence": "MEASURED", "targets": [], "tool_evidence": []},
    {"engine_name": "dead", "status": "SKIP", "summary": "no Python sources", "duration": 0.1,
     "required": true, "evidence": "ESTIMATED", "targets": [], "tool_evidence": []}
  ]
})";
}

// Minimal valid report used where only the happy path matters.
inline std::string minimalReport() {
    return R"({
  "schema_version": "ici.result/v2",
  "suite_status": "PASS",
  "passed_count": 1, "warned_count": 0, "failed_count": 0,
  "error_count": 0, "skipped_count": 0, "total_count": 1,
  "tem_score": 5.0, "max_tem_score": 5.0,
  "results": [
    {"engine_name": "lint", "status": "PASS", "summary": "clean",
     "required": true, "evidence": "MEASURED",
     "targets": [
       {"file_path": "src/a.py", "start_line": 3, "end_line": 4, "target_name": "f",
        "status": "WARN", "message": "warned", "snippet": "code"},
       {"file_path": "src/b.py", "start_line": 1, "target_name": "g",
        "status": "PASS", "message": "ok"}
     ],
     "tool_evidence": [
       {"name": "ruff", "path": "/usr/bin/ruff", "version": "0.16",
        "argv": ["ruff", "check"], "returncode": 0, "timed_out": false,
        "truncated": false, "error": ""}
     ]}
  ]
})";
}

// The v3 writer keeps the v2 target inventory and adds canonical findings.
// The current viewer renders targets, but it must accept and safely ignore the
// richer field until its issues-first UI consumes findings natively.
inline std::string minimalV3Report() {
    return R"({
  "schema_version": "ici.result/v3",
  "suite_status": "WARN",
  "passed_count": 0, "warned_count": 1, "failed_count": 0,
  "error_count": 0, "skipped_count": 0, "total_count": 1,
  "tem_score": 4.5, "max_tem_score": 5.0,
  "results": [
    {"schema_version": "ici.result/v3", "engine_name": "security", "status": "WARN",
     "summary": "one finding", "required": true, "evidence": "MEASURED",
     "targets": [
       {"file_path": "src/a.py", "start_line": 8, "target_name": "secret",
        "status": "WARN", "message": "credential", "snippet": "masked"}
     ],
     "tool_evidence": [],
     "findings": [
       {"rule_id": "ici.security.secret", "category": "security", "severity": "medium",
        "confidence": "exact", "fingerprint": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "primary_location": {"path": "src/a.py", "start_line": 8},
        "related_locations": [], "message": "credential", "explanation": "",
        "remediation": "use environment", "tool_rule_id": "", "tool_name": "",
        "tool_version": "", "suppression": {"suppressed": false, "kind": "none", "reason": ""},
        "metrics": {}, "snippet": "masked"}
     ]}
  ]
})";
}

// A v3 suite carrying the evaluated project scope and two representative
// capability rows. The producer contract requires every support entry field;
// keeping that shape in one fixture lets the model and GUI tests share it.
inline std::string supportMatrixReport(const std::string& matrix) {
    return std::string(R"({
  "schema_version": "ici.result/v3",
  "suite_status": "PASS",
  "passed_count": 1, "warned_count": 0, "failed_count": 0,
  "error_count": 0, "skipped_count": 0, "total_count": 1,
  "tem_score": 5.0, "max_tem_score": 5.0,
  "results": [
    {"engine_name": "lint", "status": "PASS", "summary": "clean",
     "required": true, "evidence": "MEASURED", "targets": [], "tool_evidence": []}
  ],
  "producer_extension": {"kept": true},
  "support_matrix": )") + matrix + R"(
})";
}

inline std::string validSupportMatrixReport() {
    return supportMatrixReport(R"({
    "project_languages": ["python", "cpp"],
    "project_frameworks": ["qt"],
    "entries": [
      {
        "engine_name": "lint", "language": "python", "mode": "tool-backed",
        "active_mode": "heuristic", "applicable": true, "enabled": true,
        "evidence": "ESTIMATED", "confidence": "medium",
        "frameworks": [], "required_tools": [], "optional_tools": ["ruff"],
        "fallback_mode": "heuristic", "limitations": ["AST fallback"],
        "reason": "Ruff was unavailable"
      },
      {
        "engine_name": "type", "language": "cpp", "mode": "unsupported",
        "active_mode": null, "applicable": false, "enabled": true,
        "evidence": "NOT_APPLICABLE", "confidence": "low",
        "frameworks": ["qt"], "required_tools": [], "optional_tools": [],
        "fallback_mode": null, "limitations": ["No C++ type analyzer"],
        "reason": "type does not support cpp"
      }
    ],
    "future_matrix_field": "ignored"
  })");
}

inline std::string nullSupportMatrixReport() { return supportMatrixReport("null"); }

inline std::string malformedSupportMatrixReport() {
    return supportMatrixReport(R"({
    "project_languages": ["python", 7],
    "project_frameworks": [],
    "entries": []
  })");
}
