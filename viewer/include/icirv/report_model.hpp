#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <vector>

#include "icirv/json_value.hpp"

namespace icirv {

enum class Status { Pass, Warn, Fail, Error, Skip, Unknown };

Status parseStatus(const std::string& text);
const char* statusName(Status status);
// Ordering used to rank findings worst-first.
int statusSeverity(Status status);

struct Target {
    std::string file_path;
    int start_line = 0;
    int end_line = 0;
    std::string target_name;
    Status status = Status::Unknown;
    std::string message;
    std::string snippet;
};

struct ToolEvidence {
    std::string name;
    std::string path;
    std::string version;
    std::vector<std::string> argv;
    int returncode = 0;
    bool has_returncode = false;
    bool timed_out = false;
    bool truncated = false;
    std::string error;
};

// The support matrix is intentionally represented with the contract's string
// vocabulary. This keeps the viewer lossless when a newer producer adds an
// entry while still validating the values it knows how to display.
struct SupportEntry {
    std::string engine_name;
    std::string language;
    std::string mode;
    std::optional<std::string> active_mode;
    bool applicable = false;
    bool enabled = false;
    std::string evidence;
    std::string confidence;
    std::vector<std::string> frameworks;
    std::vector<std::string> required_tools;
    std::vector<std::string> optional_tools;
    std::optional<std::string> fallback_mode;
    std::vector<std::string> limitations;
    std::string reason;
};

// Names used by the producer model are useful to callers that want to build
// a matrix for tests or another front end without coupling to Python types.
using EngineSupport = SupportEntry;

struct SupportMatrix {
    std::vector<std::string> project_languages;
    std::vector<std::string> project_frameworks;
    std::vector<SupportEntry> entries;
};

struct EngineResult {
    std::string engine_name;
    Status status = Status::Unknown;
    std::string summary;
    double duration = 0.0;
    bool required = true;
    std::string evidence;
    std::vector<Target> targets;
    std::vector<ToolEvidence> tool_evidence;
    std::optional<SupportMatrix> support_matrix;
};

struct Suite {
    Status suite_status = Status::Unknown;
    double duration = 0.0;
    double tem_score = 0.0;
    double max_tem_score = 0.0;
    int passed_count = 0;
    int warned_count = 0;
    int failed_count = 0;
    int error_count = 0;
    int skipped_count = 0;
    int total_count = 0;
    std::vector<EngineResult> results;
    std::optional<SupportMatrix> support_matrix;
};

struct LoadError {
    std::string message;
    std::size_t line = 0;
};

// Current writer schema and the legacy schema retained for report archives.
extern const char* const kSupportedSchema;
extern const char* const kLegacySchema;

// Parses and validates an ici JSON report. A schema mismatch, a missing
// required field, or a wrong value type produces an explicit LoadError rather
// than a silently defaulted value.
std::optional<Suite> loadReport(const std::string& jsonText, LoadError& error);

} // namespace icirv
