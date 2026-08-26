#pragma once

// Shared report fixtures. Keeping them here rather than inline in each test
// avoids the copy-paste that ici's dup engine flags as a Type-2 clone.

#include <fstream>
#include <sstream>
#include <string>
#include <vector>

// Loads a fixture without assuming a working directory.
//
// The CWD a C++ test binary sees depends on which ici engine launched it:
//   test      -> build/tests when gcov is available, the project root when not
//   sanitize  -> a temporary directory entirely outside the project
// So no relative path works everywhere. Deriving the location from __FILE__ does,
// because ici passes absolute source paths to the compiler. The relative
// candidates remain as a fallback for hand-compiling during development, where
// __FILE__ may be relative.
inline std::string fixtureDirFromSource() {
    const std::string source = __FILE__;
    const std::size_t slash = source.find_last_of('/');
    if (slash == std::string::npos) {
        return std::string();
    }
    return source.substr(0, slash + 1) + "data/";
}

inline std::string readFixture(const std::string& name) {
    std::vector<std::string> candidates;
    const std::string fromSource = fixtureDirFromSource();
    if (!fromSource.empty()) {
        candidates.push_back(fromSource);
    }
    candidates.push_back("tests/data/");
    candidates.push_back("../../tests/data/");
    candidates.push_back("../tests/data/");

    for (const std::string& prefix : candidates) {
        std::ifstream input(prefix + name);
        if (input) {
            std::stringstream buffer;
            buffer << input.rdbuf();
            return buffer.str();
        }
    }
    return std::string();
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
