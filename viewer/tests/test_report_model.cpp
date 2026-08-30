#include "check.hpp"
#include "fixtures.hpp"

#include "icirv/report_model.hpp"

#include <string>
#include <vector>

using icirv::LoadError;
using icirv::loadReport;
using icirv::Status;
using icirv::Suite;

namespace {

void expectRejected(const std::string& text, const std::string& expectFragment) {
    LoadError error;
    const auto suite = loadReport(text, error);
    CHECK(!suite.has_value());
    CHECK(error.message.find(expectFragment) != std::string::npos);
    if (error.message.find(expectFragment) == std::string::npos) {
        std::fprintf(stderr, "  actual message: %s\n", error.message.c_str());
    }
}

void testStatusMapping() {
    CHECK(icirv::parseStatus("PASS") == Status::Pass);
    CHECK(icirv::parseStatus("WARN") == Status::Warn);
    CHECK(icirv::parseStatus("FAIL") == Status::Fail);
    CHECK(icirv::parseStatus("ERROR") == Status::Error);
    CHECK(icirv::parseStatus("SKIP") == Status::Skip);
    CHECK(icirv::parseStatus("nonsense") == Status::Unknown);
    CHECK_EQ(std::string(icirv::statusName(Status::Fail)), std::string("FAIL"));
    CHECK_EQ(std::string(icirv::statusName(Status::Unknown)), std::string("UNKNOWN"));
    CHECK(icirv::statusSeverity(Status::Error) > icirv::statusSeverity(Status::Fail));
    CHECK(icirv::statusSeverity(Status::Skip) > icirv::statusSeverity(Status::Warn));
    CHECK(icirv::statusSeverity(Status::Warn) > icirv::statusSeverity(Status::Unknown));
    CHECK(icirv::statusSeverity(Status::Unknown) > icirv::statusSeverity(Status::Pass));
}

void testMinimalReport() {
    LoadError error;
    const auto suite = loadReport(minimalReport(), error);
    CHECK(suite.has_value());
    if (!suite) {
        return;
    }
    CHECK(suite->suite_status == Status::Pass);
    CHECK_NEAR(suite->tem_score, 5.0, 1e-9);
    CHECK_NEAR(suite->max_tem_score, 5.0, 1e-9);
    CHECK_EQ(suite->total_count, 1);
    CHECK_EQ(suite->results.size(), static_cast<std::size_t>(1));

    const icirv::EngineResult& engine = suite->results.front();
    CHECK_EQ(engine.engine_name, std::string("lint"));
    CHECK(engine.status == Status::Pass);
    CHECK_EQ(engine.required, true);
    CHECK_EQ(engine.evidence, std::string("MEASURED"));
    CHECK_EQ(engine.targets.size(), static_cast<std::size_t>(2));
    CHECK_EQ(engine.targets[0].start_line, 3);
    CHECK_EQ(engine.targets[0].end_line, 4);
    CHECK(engine.targets[0].status == Status::Warn);
    CHECK_EQ(engine.targets[0].snippet, std::string("code"));
    // Absent optional field defaults rather than failing.
    CHECK_EQ(engine.targets[1].end_line, 0);

    CHECK_EQ(engine.tool_evidence.size(), static_cast<std::size_t>(1));
    const icirv::ToolEvidence& tool = engine.tool_evidence.front();
    CHECK_EQ(tool.name, std::string("ruff"));
    CHECK_EQ(tool.argv.size(), static_cast<std::size_t>(2));
    CHECK_EQ(tool.has_returncode, true);
    CHECK_EQ(tool.returncode, 0);
    CHECK_EQ(tool.timed_out, false);
}

void testRealIciReport() {
    const std::string text = readFixture("ici_self_report.json");
    CHECK(!text.empty());
    LoadError error;
    const auto suite = loadReport(text, error);
    CHECK(suite.has_value());
    if (!suite) {
        std::fprintf(stderr, "  real report rejected: %s\n", error.message.c_str());
        return;
    }
    CHECK_EQ(suite->results.size(), static_cast<std::size_t>(12));
    CHECK(suite->tem_score > 0.0);
    std::size_t targets = 0;
    for (const icirv::EngineResult& engine : suite->results) {
        CHECK(!engine.engine_name.empty());
        targets += engine.targets.size();
    }
    CHECK(targets > 1000);
}

void testV3ReportRetainsLegacyRendering() {
    LoadError error;
    const auto suite = loadReport(minimalV3Report(), error);
    CHECK(suite.has_value());
    if (!suite) {
        std::fprintf(stderr, "  v3 report rejected: %s\n", error.message.c_str());
        return;
    }
    CHECK(suite->suite_status == Status::Warn);
    CHECK_EQ(suite->results.size(), static_cast<std::size_t>(1));
    CHECK_EQ(suite->results.front().targets.size(), static_cast<std::size_t>(1));
    CHECK_EQ(suite->results.front().targets.front().start_line, 8);
}

void testSupportMatrixIsParsedLosslessly() {
    LoadError error;
    const auto suite = loadReport(validSupportMatrixReport(), error);
    CHECK(suite.has_value());
    if (!suite) {
        std::fprintf(stderr, "  support matrix report rejected: %s\n", error.message.c_str());
        return;
    }
    CHECK(suite->support_matrix.has_value());
    if (!suite->support_matrix) {
        return;
    }
    const icirv::SupportMatrix& matrix = suite->support_matrix.value();
    CHECK_EQ(matrix.project_languages.size(), static_cast<std::size_t>(2));
    CHECK_EQ(matrix.project_languages[0], std::string("python"));
    CHECK_EQ(matrix.project_languages[1], std::string("cpp"));
    CHECK_EQ(matrix.project_frameworks.size(), static_cast<std::size_t>(1));
    CHECK_EQ(matrix.project_frameworks.front(), std::string("qt"));
    CHECK_EQ(matrix.entries.size(), static_cast<std::size_t>(2));

    const icirv::SupportEntry& fallback = matrix.entries.front();
    CHECK_EQ(fallback.engine_name, std::string("lint"));
    CHECK_EQ(fallback.language, std::string("python"));
    CHECK_EQ(fallback.mode, std::string("tool-backed"));
    CHECK(fallback.active_mode.has_value());
    CHECK_EQ(fallback.active_mode.value(), std::string("heuristic"));
    CHECK(fallback.applicable);
    CHECK(fallback.enabled);
    CHECK_EQ(fallback.evidence, std::string("ESTIMATED"));
    CHECK_EQ(fallback.confidence, std::string("medium"));
    CHECK_EQ(fallback.optional_tools.front(), std::string("ruff"));
    CHECK_EQ(fallback.fallback_mode.value(), std::string("heuristic"));

    const icirv::SupportEntry& unsupported = matrix.entries.back();
    CHECK(!unsupported.active_mode.has_value());
    CHECK(!unsupported.applicable);
    CHECK(unsupported.fallback_mode == std::nullopt);
    CHECK_EQ(unsupported.frameworks.front(), std::string("qt"));
}

void testSupportMatrixMayBeOmittedOrNull() {
    LoadError error;
    const auto omitted = loadReport(minimalV3Report(), error);
    CHECK(omitted.has_value());
    if (omitted) {
        CHECK(!omitted->support_matrix.has_value());
    }

    const auto nullMatrix = loadReport(nullSupportMatrixReport(), error);
    CHECK(nullMatrix.has_value());
    if (nullMatrix) {
        CHECK(!nullMatrix->support_matrix.has_value());
    }
}

std::string replaceFirst(std::string text, const std::string& from, const std::string& to) {
    const std::size_t position = text.find(from);
    CHECK(position != std::string::npos);
    if (position != std::string::npos) {
        text.replace(position, from.size(), to);
    }
    return text;
}

std::string validSupportEntryBody() {
    return R"({
      "engine_name": "lint", "language": "python", "mode": "tool-backed",
      "active_mode": "heuristic", "applicable": true, "enabled": true,
      "evidence": "ESTIMATED", "confidence": "medium",
      "frameworks": ["qt"], "required_tools": [], "optional_tools": ["ruff"],
      "fallback_mode": "heuristic", "limitations": ["AST fallback"],
      "reason": "Ruff was unavailable", "future_entry_field": "ignored"
    })";
}

std::string validSupportMatrixBody() {
    return std::string(R"({
    "project_languages": ["python", "cpp"],
    "project_frameworks": ["qt"],
    "entries": [)") +
           validSupportEntryBody() + R"(],
    "future_matrix_field": "ignored"
  })";
}

std::string makeSupportMatrix(const std::string& languages, const std::string& frameworks,
                              const std::string& entries) {
    return std::string(R"({
    "project_languages": )") +
           languages + R"(,
    "project_frameworks": )" +
           frameworks + R"(,
    "entries": )" +
           entries + R"(
  })";
}

std::string engineSupportMatrixReport(const std::string& matrix) {
    return std::string(R"({
  "schema_version": "ici.result/v3",
  "suite_status": "PASS",
  "passed_count": 1, "warned_count": 0, "failed_count": 0,
  "error_count": 0, "skipped_count": 0, "total_count": 1,
  "tem_score": 5.0, "max_tem_score": 5.0,
  "results": [
    {"engine_name": "lint", "status": "PASS", "targets": [],
     "tool_evidence": [], "support_matrix": )") +
           matrix + R"(}
  ],
  "support_matrix": null
})";
}

void expectRejectedCase(const char* name, const std::string& text,
                        const std::string& expectFragment) {
    LoadError error;
    const auto suite = loadReport(text, error);
    if (suite.has_value()) {
        std::fprintf(stderr, "  %s: report was unexpectedly accepted\n", name);
    }
    CHECK(!suite.has_value());
    if (error.message.find(expectFragment) == std::string::npos) {
        std::fprintf(stderr, "  %s: actual message: %s\n", name, error.message.c_str());
    }
    CHECK(error.message.find(expectFragment) != std::string::npos);
}

void testSupportMatrixAllowsEmptyCollections() {
    LoadError error;
    const auto suite = loadReport(supportMatrixReport(makeSupportMatrix("[]", "[]", "[]")), error);
    CHECK(suite.has_value());
    if (!suite) {
        std::fprintf(stderr, "  empty support matrix rejected: %s\n", error.message.c_str());
        return;
    }
    CHECK(suite->support_matrix.has_value());
    if (!suite->support_matrix) {
        return;
    }
    CHECK(suite->support_matrix->project_languages.empty());
    CHECK(suite->support_matrix->project_frameworks.empty());
    CHECK(suite->support_matrix->entries.empty());
}

void testEngineSupportMatrixIsParsed() {
    LoadError error;
    const auto suite = loadReport(engineSupportMatrixReport(validSupportMatrixBody()), error);
    CHECK(suite.has_value());
    if (!suite) {
        std::fprintf(stderr, "  engine support matrix rejected: %s\n", error.message.c_str());
        return;
    }
    CHECK(!suite->support_matrix.has_value());
    CHECK_EQ(suite->results.size(), static_cast<std::size_t>(1));
    if (suite->results.empty()) {
        return;
    }
    const auto& matrix = suite->results.front().support_matrix;
    CHECK(matrix.has_value());
    if (matrix) {
        CHECK_EQ(matrix->entries.size(), static_cast<std::size_t>(1));
        CHECK_EQ(matrix->entries.front().engine_name, std::string("lint"));
    }

    expectRejectedCase("engine-level wrong support matrix type",
                       engineSupportMatrixReport("7"), "support_matrix");
}

void testSupportMatrixValidationBranches() {
    const std::string valid = validSupportMatrixBody();
    struct MatrixCase {
        const char* name;
        std::string matrix;
        const char* expected;
    };

    const std::vector<MatrixCase> cases = {
        {"missing project languages",
         replaceFirst(valid, R"("project_languages": ["python", "cpp"],)",
                      R"("future_project_languages": ["python", "cpp"],)"),
         "project_languages"},
        {"project languages wrong type",
         replaceFirst(valid, R"("project_languages": ["python", "cpp"],)",
                      R"("project_languages": 7,)"),
         "project_languages"},
        {"project language item wrong type",
         replaceFirst(valid, R"("project_languages": ["python", "cpp"],)",
                      R"("project_languages": ["python", 7],)"),
         "project_languages' items must be strings"},
        {"unsupported project language",
         replaceFirst(valid, R"("project_languages": ["python", "cpp"],)",
                      R"("project_languages": ["rust"],)"),
         "unsupported language"},
        {"missing project frameworks",
         replaceFirst(valid, R"("project_frameworks": ["qt"],)",
                      R"("future_project_frameworks": ["qt"],)"),
         "project_frameworks"},
        {"project frameworks wrong type",
         replaceFirst(valid, R"("project_frameworks": ["qt"],)",
                      R"("project_frameworks": 7,)"),
         "project_frameworks"},
        {"project framework item wrong type",
         replaceFirst(valid, R"("project_frameworks": ["qt"],)",
                      R"("project_frameworks": ["qt", 7],)"),
         "project_frameworks' items must be strings"},
        {"empty project framework item",
         replaceFirst(valid, R"("project_frameworks": ["qt"],)",
                      R"("project_frameworks": [""],)"),
         "project_frameworks' items must be non-empty strings"},
        {"missing entries",
         replaceFirst(valid, R"("entries": [)", R"("future_entries": [)"), "entries"},
        {"entries wrong type", makeSupportMatrix("[\"python\"]", "[]", "7"), "entries"},
        {"entry wrong shape", makeSupportMatrix("[\"python\"]", "[]", "[7]"),
         "each entry of 'support_matrix.entries' must be an object"},

        {"missing engine name",
         replaceFirst(valid, R"("engine_name": "lint",)",
                      R"("future_engine_name": "lint",)"),
         "engine_name"},
        {"engine name wrong type",
         replaceFirst(valid, R"("engine_name": "lint",)", R"("engine_name": 7,)"),
         "engine_name"},
        {"empty engine name",
         replaceFirst(valid, R"("engine_name": "lint",)", R"("engine_name": "",)"),
         "non-empty string"},
        {"missing entry language",
         replaceFirst(valid, R"("language": "python",)",
                      R"("future_language": "python",)"),
         "language"},
        {"entry language wrong type",
         replaceFirst(valid, R"("language": "python",)", R"("language": 7,)"), "language"},
        {"unsupported entry language",
         replaceFirst(valid, R"("language": "python",)", R"("language": "rust",)"),
         "unsupported value"},
        {"missing mode",
         replaceFirst(valid, R"("mode": "tool-backed",)",
                      R"("future_mode": "tool-backed",)"),
         "mode"},
        {"mode wrong type",
         replaceFirst(valid, R"("mode": "tool-backed",)", R"("mode": 7,)"), "mode"},
        {"unsupported mode",
         replaceFirst(valid, R"("mode": "tool-backed",)", R"("mode": "magic",)"),
         "unsupported value"},
        {"missing active mode",
         replaceFirst(valid, R"("active_mode": "heuristic",)",
                      R"("future_active_mode": "heuristic",)"),
         "active_mode"},
        {"active mode wrong type",
         replaceFirst(valid, R"("active_mode": "heuristic",)", R"("active_mode": 7,)"),
         "string or null"},
        {"unsupported active mode",
         replaceFirst(valid, R"("active_mode": "heuristic",)",
                      R"("active_mode": "magic",)"),
         "unsupported value"},
        {"missing applicable",
         replaceFirst(valid, R"("applicable": true,)", R"("future_applicable": true,)"),
         "applicable"},
        {"applicable wrong type",
         replaceFirst(valid, R"("applicable": true,)", R"("applicable": "true",)"),
         "applicable"},
        {"missing enabled",
         replaceFirst(valid, R"("enabled": true,)", R"("future_enabled": true,)"),
         "enabled"},
        {"enabled wrong type",
         replaceFirst(valid, R"("enabled": true,)", R"("enabled": 0,)"), "enabled"},
        {"missing evidence",
         replaceFirst(valid, R"("evidence": "ESTIMATED",)",
                      R"("future_evidence": "ESTIMATED",)"),
         "evidence"},
        {"evidence wrong type",
         replaceFirst(valid, R"("evidence": "ESTIMATED",)", R"("evidence": 7,)"),
         "evidence"},
        {"unsupported evidence",
         replaceFirst(valid, R"("evidence": "ESTIMATED",)",
                      R"("evidence": "UNKNOWN",)"),
         "unsupported value"},
        {"missing confidence",
         replaceFirst(valid, R"("confidence": "medium",)",
                      R"("future_confidence": "medium",)"),
         "confidence"},
        {"confidence wrong type",
         replaceFirst(valid, R"("confidence": "medium",)", R"("confidence": 7,)"),
         "confidence"},
        {"unsupported confidence",
         replaceFirst(valid, R"("confidence": "medium",)",
                      R"("confidence": "certain",)"),
         "unsupported value"},
        {"missing frameworks",
         replaceFirst(valid, R"("frameworks": ["qt"],)",
                      R"("future_frameworks": ["qt"],)"),
         "frameworks"},
        {"frameworks wrong type",
         replaceFirst(valid, R"("frameworks": ["qt"],)", R"("frameworks": 7,)"),
         "frameworks"},
        {"framework item wrong type",
         replaceFirst(valid, R"("frameworks": ["qt"],)",
                      R"("frameworks": ["qt", 7],)"),
         "frameworks' items must be strings"},
        {"empty framework item",
         replaceFirst(valid, R"("frameworks": ["qt"],)", R"("frameworks": [""],)"),
         "frameworks' items must be non-empty strings"},
        {"missing required tools",
         replaceFirst(valid, R"("required_tools": [],)",
                      R"("future_required_tools": [],)"),
         "required_tools"},
        {"required tools wrong type",
         replaceFirst(valid, R"("required_tools": [],)", R"("required_tools": 7,)"),
         "required_tools"},
        {"required tool item wrong type",
         replaceFirst(valid, R"("required_tools": [],)", R"("required_tools": [7],)"),
         "required_tools' items must be strings"},
        {"empty required tool item",
         replaceFirst(valid, R"("required_tools": [],)", R"("required_tools": [""],)"),
         "required_tools' items must be non-empty strings"},
        {"missing optional tools",
         replaceFirst(valid, R"("optional_tools": ["ruff"],)",
                      R"("future_optional_tools": ["ruff"],)"),
         "optional_tools"},
        {"optional tools wrong type",
         replaceFirst(valid, R"("optional_tools": ["ruff"],)",
                      R"("optional_tools": 7,)"),
         "optional_tools"},
        {"optional tool item wrong type",
         replaceFirst(valid, R"("optional_tools": ["ruff"],)",
                      R"("optional_tools": ["ruff", 7],)"),
         "optional_tools' items must be strings"},
        {"empty optional tool item",
         replaceFirst(valid, R"("optional_tools": ["ruff"],)",
                      R"("optional_tools": [""],)"),
         "optional_tools' items must be non-empty strings"},
        {"missing fallback mode",
         replaceFirst(valid, R"("fallback_mode": "heuristic",)",
                      R"("future_fallback_mode": "heuristic",)"),
         "fallback_mode"},
        {"fallback mode wrong type",
         replaceFirst(valid, R"("fallback_mode": "heuristic",)",
                      R"("fallback_mode": 7,)"),
         "string or null"},
        {"unsupported fallback mode",
         replaceFirst(valid, R"("fallback_mode": "heuristic",)",
                      R"("fallback_mode": "magic",)"),
         "unsupported value"},
        {"missing limitations",
         replaceFirst(valid, R"("limitations": ["AST fallback"],)",
                      R"("future_limitations": ["AST fallback"],)"),
         "limitations"},
        {"limitations wrong type",
         replaceFirst(valid, R"("limitations": ["AST fallback"],)",
                      R"("limitations": 7,)"),
         "limitations"},
        {"limitation item wrong type",
         replaceFirst(valid, R"("limitations": ["AST fallback"],)",
                      R"("limitations": ["AST fallback", 7],)"),
         "limitations' items must be strings"},
        {"empty limitation item",
         replaceFirst(valid, R"("limitations": ["AST fallback"],)",
                      R"("limitations": [""],)"),
         "limitations' items must be non-empty strings"},
        {"missing reason",
         replaceFirst(valid, R"("reason": "Ruff was unavailable",)",
                      R"("future_reason": "Ruff was unavailable",)"),
         "reason"},
        {"reason wrong type",
         replaceFirst(valid, R"("reason": "Ruff was unavailable",)", R"("reason": 7,)"),
         "reason"},
    };

    for (const MatrixCase& testCase : cases) {
        expectRejectedCase(testCase.name, supportMatrixReport(testCase.matrix), testCase.expected);
    }
}

void testMalformedSupportMatrixIsRejected() {
    expectRejected(malformedSupportMatrixReport(), "project_languages");
    expectRejected(supportMatrixReport("7"), "support_matrix");
    expectRejected(
        supportMatrixReport(R"({"project_languages":[],"project_frameworks":[],"entries":
          [{"engine_name":"x"}]})"),
        "language");
}

void testValidationErrors() {
    expectRejected("not json at all", "invalid JSON");
    expectRejected("[1,2]", "must be an object");
    expectRejected(R"({"suite_status":"PASS"})", "schema_version");
    expectRejected(R"({"schema_version":"ici.result/v1"})", "unsupported schema_version");
    expectRejected(R"({"schema_version": 2})", "must be a string");
    expectRejected(R"({"schema_version":"ici.result/v2"})", "suite_status");
    expectRejected(
        R"({"schema_version":"ici.result/v2","suite_status":"PASS","tem_score":"x"})",
        "must be a number");
    expectRejected(
        R"({"schema_version":"ici.result/v2","suite_status":"PASS","tem_score":1})",
        "passed_count");
    expectRejected(
        R"({"schema_version":"ici.result/v2","suite_status":"PASS","tem_score":1,
            "passed_count":0,"warned_count":0,"failed_count":0,"error_count":0,
            "skipped_count":0,"total_count":0})",
        "'results' must be an array");
    expectRejected(
        R"({"schema_version":"ici.result/v2","suite_status":"PASS","tem_score":1,
            "passed_count":0,"warned_count":0,"failed_count":0,"error_count":0,
            "skipped_count":0,"total_count":0,"results":[3]})",
        "must be an object");
    expectRejected(
        R"({"schema_version":"ici.result/v2","suite_status":"PASS","tem_score":1,
            "passed_count":0,"warned_count":0,"failed_count":0,"error_count":0,
            "skipped_count":0,"total_count":0,"results":[{"status":"PASS"}]})",
        "engine_name");
    expectRejected(
        R"({"schema_version":"ici.result/v2","suite_status":"PASS","tem_score":1,
            "passed_count":0,"warned_count":0,"failed_count":0,"error_count":0,
            "skipped_count":0,"total_count":0,"results":[{"engine_name":"x"}]})",
        "status");
}

} // namespace

int main() {
    testStatusMapping();
    testMinimalReport();
    testRealIciReport();
    testV3ReportRetainsLegacyRendering();
    testSupportMatrixIsParsedLosslessly();
    testSupportMatrixMayBeOmittedOrNull();
    testSupportMatrixAllowsEmptyCollections();
    testEngineSupportMatrixIsParsed();
    testSupportMatrixValidationBranches();
    testMalformedSupportMatrixIsRejected();
    testValidationErrors();
    return checkSummary();
}
