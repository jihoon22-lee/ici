#include "check.hpp"
#include "fixtures.hpp"

#include "../src/core/report_model.hpp"

#include <string>

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
    testValidationErrors();
    return checkSummary();
}
