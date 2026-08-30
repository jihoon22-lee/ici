#include "icirv/report_model.hpp"

#include "icirv/json_parser.hpp"

#include <utility>

namespace icirv {

const char* const kSupportedSchema = "ici.result/v3";
const char* const kLegacySchema = "ici.result/v2";

namespace {

struct StatusName {
    const char* text;
    Status status;
};

const StatusName kStatusNames[] = {
    {"PASS", Status::Pass}, {"WARN", Status::Warn},   {"FAIL", Status::Fail},
    {"ERROR", Status::Error}, {"SKIP", Status::Skip},
};

// Reports a missing or wrong-typed field instead of defaulting it away.
bool requireField(const JsonValue& obj, const std::string& key, LoadError& error) {
    if (obj.has(key)) {
        return true;
    }
    error.message = "missing required field '" + key + "'";
    return false;
}

// All the require* helpers share one shape: present, right type, then extract.
// Spelling that out once keeps them from reading as copy-paste.
template <typename Extract>
bool requireTyped(const JsonValue& obj, const std::string& key, const char* typeName,
                  bool (JsonValue::*isType)() const, Extract extract, LoadError& error) {
    if (!requireField(obj, key, error)) {
        return false;
    }
    const JsonValue& value = obj.member(key);
    if (!(value.*isType)()) {
        error.message = "field '" + key + "' must be a " + typeName;
        return false;
    }
    extract(value);
    return true;
}

bool requireString(const JsonValue& obj, const std::string& key, std::string& out,
                   LoadError& error) {
    const auto extract = [&out](const JsonValue& value) { out = value.asString(); };
    return requireTyped(obj, key, "string", &JsonValue::isString, extract, error);
}

bool requireNonEmptyString(const JsonValue& obj, const std::string& key, std::string& out,
                           LoadError& error) {
    if (!requireString(obj, key, out, error)) {
        return false;
    }
    if (out.empty()) {
        error.message = "field '" + key + "' must be a non-empty string";
        return false;
    }
    return true;
}

bool requireBool(const JsonValue& obj, const std::string& key, bool& out, LoadError& error) {
    const auto extract = [&out](const JsonValue& value) { out = value.asBool(); };
    return requireTyped(obj, key, "boolean", &JsonValue::isBool, extract, error);
}

bool requireNumber(const JsonValue& obj, const std::string& key, double& out, LoadError& error) {
    const auto extract = [&out](const JsonValue& value) { out = value.asNumber(); };
    return requireTyped(obj, key, "number", &JsonValue::isNumber, extract, error);
}

bool requireInt(const JsonValue& obj, const std::string& key, int& out, LoadError& error) {
    double value = 0.0;
    if (!requireNumber(obj, key, value, error)) {
        return false;
    }
    out = static_cast<int>(value);
    return true;
}

// Optional fields genuinely may be absent; only these default silently.
std::string optionalString(const JsonValue& obj, const std::string& key) {
    return obj.member(key).asString();
}

int optionalInt(const JsonValue& obj, const std::string& key) {
    return static_cast<int>(obj.member(key).asNumber(0.0));
}

bool enumValue(const std::string& value, const char* const* allowed, std::size_t count) {
    for (std::size_t index = 0; index < count; ++index) {
        if (value == allowed[index]) {
            return true;
        }
    }
    return false;
}

bool readEnumMember(const JsonValue& obj, const std::string& key, const char* const* allowed,
                    std::size_t count, std::string& out, LoadError& error) {
    if (!requireString(obj, key, out, error)) {
        return false;
    }
    if (!enumValue(out, allowed, count)) {
        error.message = "field '" + key + "' has an unsupported value '" + out + "'";
        return false;
    }
    return true;
}

bool readNullableEnumMember(const JsonValue& obj, const std::string& key,
                            const char* const* allowed, std::size_t count,
                            std::optional<std::string>& out, LoadError& error) {
    if (!requireField(obj, key, error)) {
        return false;
    }
    const JsonValue& value = obj.member(key);
    if (value.isNull()) {
        out.reset();
        return true;
    }
    if (!value.isString()) {
        error.message = "field '" + key + "' must be a string or null";
        return false;
    }
    const std::string parsed = value.asString();
    if (!enumValue(parsed, allowed, count)) {
        error.message = "field '" + key + "' has an unsupported value '" + parsed + "'";
        return false;
    }
    out = parsed;
    return true;
}

bool readStringArrayMember(const JsonValue& obj, const std::string& key,
                           std::vector<std::string>& out, bool nonemptyItems,
                           LoadError& error) {
    if (!requireField(obj, key, error)) {
        return false;
    }
    const JsonValue& list = obj.member(key);
    if (!list.isArray()) {
        error.message = "field '" + key + "' must be an array";
        return false;
    }
    out.clear();
    out.reserve(list.size());
    for (std::size_t index = 0; index < list.size(); ++index) {
        const JsonValue& item = list.at(index);
        if (!item.isString()) {
            error.message = "field '" + key + "' items must be strings";
            return false;
        }
        const std::string value = item.asString();
        if (nonemptyItems && value.empty()) {
            error.message = "field '" + key + "' items must be non-empty strings";
            return false;
        }
        out.push_back(value);
    }
    return true;
}

bool readLanguageArrayMember(const JsonValue& obj, const std::string& key,
                             std::vector<std::string>& out, LoadError& error) {
    static const char* const kLanguages[] = {"python", "cpp"};
    if (!readStringArrayMember(obj, key, out, false, error)) {
        return false;
    }
    for (const std::string& language : out) {
        if (!enumValue(language, kLanguages, sizeof(kLanguages) / sizeof(kLanguages[0]))) {
            error.message = "field '" + key + "' has an unsupported language '" + language +
                            "'";
            return false;
        }
    }
    return true;
}

const char* const kModes[] = {"exact", "heuristic", "tool-backed", "unsupported"};
const char* const kEvidence[] = {"MEASURED", "ESTIMATED", "NOT_RUN", "NOT_APPLICABLE"};
const char* const kConfidence[] = {"exact", "high", "medium", "low"};
const char* const kLanguages[] = {"python", "cpp"};

bool readSupportIdentity(const JsonValue& node, SupportEntry& entry, LoadError& error) {
    return requireNonEmptyString(node, "engine_name", entry.engine_name, error) &&
           readEnumMember(node, "language", kLanguages,
                          sizeof(kLanguages) / sizeof(kLanguages[0]), entry.language, error) &&
           readEnumMember(node, "mode", kModes, sizeof(kModes) / sizeof(kModes[0]), entry.mode,
                          error) &&
           readNullableEnumMember(node, "active_mode", kModes,
                                  sizeof(kModes) / sizeof(kModes[0]), entry.active_mode, error) &&
           requireBool(node, "applicable", entry.applicable, error) &&
           requireBool(node, "enabled", entry.enabled, error);
}

bool readSupportEvidence(const JsonValue& node, SupportEntry& entry, LoadError& error) {
    return readEnumMember(node, "evidence", kEvidence,
                          sizeof(kEvidence) / sizeof(kEvidence[0]), entry.evidence, error) &&
           readEnumMember(node, "confidence", kConfidence,
                          sizeof(kConfidence) / sizeof(kConfidence[0]), entry.confidence, error) &&
           readStringArrayMember(node, "frameworks", entry.frameworks, true, error) &&
           readStringArrayMember(node, "required_tools", entry.required_tools, true, error) &&
           readStringArrayMember(node, "optional_tools", entry.optional_tools, true, error) &&
           readNullableEnumMember(node, "fallback_mode", kModes,
                                  sizeof(kModes) / sizeof(kModes[0]), entry.fallback_mode, error) &&
           readStringArrayMember(node, "limitations", entry.limitations, true, error) &&
           requireString(node, "reason", entry.reason, error);
}

bool readSupportEntry(const JsonValue& node, SupportEntry& entry, LoadError& error) {
    if (!node.isObject()) {
        error.message = "each entry of 'support_matrix.entries' must be an object";
        return false;
    }
    return readSupportIdentity(node, entry, error) && readSupportEvidence(node, entry, error);
}

template <typename T>
bool readValidatedArray(const JsonValue& list, const std::string& typeError,
                        std::vector<T>& out, bool (*read)(const JsonValue&, T&, LoadError&),
                        LoadError& error) {
    if (!list.isArray()) {
        error.message = typeError;
        return false;
    }
    out.reserve(list.size());
    for (std::size_t index = 0; index < list.size(); ++index) {
        T item;
        if (!read(list.at(index), item, error)) {
            return false;
        }
        out.push_back(std::move(item));
    }
    return true;
}

bool readSupportMatrixMember(const JsonValue& obj, const std::string& key,
                             std::optional<SupportMatrix>& out, LoadError& error) {
    out.reset();
    if (!obj.has(key)) {
        return true;
    }
    const JsonValue& value = obj.member(key);
    if (value.isNull()) {
        return true;
    }
    if (!value.isObject()) {
        error.message = "field '" + key + "' must be an object or null";
        return false;
    }

    SupportMatrix matrix;
    if (!readLanguageArrayMember(value, "project_languages", matrix.project_languages, error) ||
        !readStringArrayMember(value, "project_frameworks", matrix.project_frameworks, true,
                                error)) {
        return false;
    }
    if (!requireField(value, "entries", error)) {
        return false;
    }
    if (!readValidatedArray(value.member("entries"), "field 'entries' must be an array",
                            matrix.entries, readSupportEntry, error)) {
        return false;
    }
    out = std::move(matrix);
    return true;
}

Target readTarget(const JsonValue& node) {
    Target target;
    target.file_path = optionalString(node, "file_path");
    target.start_line = optionalInt(node, "start_line");
    target.end_line = optionalInt(node, "end_line");
    target.target_name = optionalString(node, "target_name");
    target.status = parseStatus(optionalString(node, "status"));
    target.message = optionalString(node, "message");
    target.snippet = optionalString(node, "snippet");
    return target;
}

// Reads a JSON array member into a vector. Every list in this schema has the
// same shape, so sharing it keeps readArgv/readTargets/readToolEvidenceList
// from reading as three copies of the same loop.
template <typename T, typename Read>
std::vector<T> readList(const JsonValue& node, const std::string& key, Read read) {
    std::vector<T> items;
    const JsonValue& list = node.member(key);
    items.reserve(list.size());
    for (std::size_t i = 0; i < list.size(); ++i) {
        items.push_back(read(list.at(i)));
    }
    return items;
}

std::vector<std::string> readArgv(const JsonValue& node) {
    return readList<std::string>(node, "argv",
                                 [](const JsonValue& item) { return item.asString(); });
}

ToolEvidence readToolEvidence(const JsonValue& node) {
    ToolEvidence tool;
    tool.name = optionalString(node, "name");
    tool.path = optionalString(node, "path");
    tool.version = optionalString(node, "version");
    tool.argv = readArgv(node);
    tool.has_returncode = node.member("returncode").isNumber();
    tool.returncode = optionalInt(node, "returncode");
    tool.timed_out = node.member("timed_out").asBool(false);
    tool.truncated = node.member("truncated").asBool(false);
    tool.error = optionalString(node, "error");
    return tool;
}

bool readEngine(const JsonValue& node, EngineResult& engine, LoadError& error) {
    if (!node.isObject()) {
        error.message = "each entry of 'results' must be an object";
        return false;
    }
    std::string statusText;
    if (!requireString(node, "engine_name", engine.engine_name, error) ||
        !requireString(node, "status", statusText, error)) {
        return false;
    }
    engine.status = parseStatus(statusText);
    engine.summary = optionalString(node, "summary");
    engine.duration = node.member("duration").asNumber(0.0);
    engine.required = node.member("required").asBool(true);
    engine.evidence = optionalString(node, "evidence");
    engine.targets = readList<Target>(node, "targets", readTarget);
    engine.tool_evidence = readList<ToolEvidence>(node, "tool_evidence", readToolEvidence);
    return readSupportMatrixMember(node, "support_matrix", engine.support_matrix, error);
}

bool readCounts(const JsonValue& root, Suite& suite, LoadError& error) {
    return requireInt(root, "passed_count", suite.passed_count, error) &&
           requireInt(root, "warned_count", suite.warned_count, error) &&
           requireInt(root, "failed_count", suite.failed_count, error) &&
           requireInt(root, "error_count", suite.error_count, error) &&
           requireInt(root, "skipped_count", suite.skipped_count, error) &&
           requireInt(root, "total_count", suite.total_count, error);
}

bool readResults(const JsonValue& root, Suite& suite, LoadError& error) {
    const JsonValue& list = root.member("results");
    return readValidatedArray(list, "field 'results' must be an array", suite.results,
                              readEngine, error);
}

bool checkSchema(const JsonValue& root, LoadError& error) {
    std::string schema;
    if (!requireString(root, "schema_version", schema, error)) {
        return false;
    }
    if (schema != kSupportedSchema && schema != kLegacySchema) {
        error.message = "unsupported schema_version '" + schema + "', expected '" +
                        std::string(kSupportedSchema) + "' or '" + std::string(kLegacySchema) +
                        "'";
        return false;
    }
    return true;
}

} // namespace

Status parseStatus(const std::string& text) {
    for (const StatusName& entry : kStatusNames) {
        if (text == entry.text) {
            return entry.status;
        }
    }
    return Status::Unknown;
}

const char* statusName(Status status) {
    for (const StatusName& entry : kStatusNames) {
        if (status == entry.status) {
            return entry.text;
        }
    }
    return "UNKNOWN";
}

int statusSeverity(Status status) {
    switch (status) {
        case Status::Error: return 5;
        case Status::Fail: return 4;
        case Status::Skip: return 3;
        case Status::Warn: return 2;
        case Status::Unknown: return 1;
        case Status::Pass: return 0;
    }
    return 0;
}

std::optional<Suite> loadReport(const std::string& jsonText, LoadError& error) {
    error = LoadError{};
    JsonError jsonError;
    const std::optional<JsonValue> document = parseJson(jsonText, jsonError);
    if (!document) {
        error.message = "invalid JSON: " + jsonError.message;
        error.line = jsonError.line;
        return std::nullopt;
    }
    if (!document->isObject()) {
        error.message = "report root must be an object";
        return std::nullopt;
    }
    if (!checkSchema(*document, error)) {
        return std::nullopt;
    }

    Suite suite;
    std::string statusText;
    if (!requireString(*document, "suite_status", statusText, error) ||
        !requireNumber(*document, "tem_score", suite.tem_score, error) ||
        !readCounts(*document, suite, error) || !readResults(*document, suite, error)) {
        return std::nullopt;
    }
    suite.suite_status = parseStatus(statusText);
    suite.duration = document->member("duration").asNumber(0.0);
    suite.max_tem_score = document->member("max_tem_score").asNumber(0.0);
    if (!readSupportMatrixMember(*document, "support_matrix", suite.support_matrix, error)) {
        return std::nullopt;
    }
    return suite;
}

} // namespace icirv
