#include "check.hpp"
#include "fixtures.hpp"

#include "../src/core/summary.hpp"

#include <string>

using icirv::gateReason;
using icirv::LoadError;
using icirv::loadReport;
using icirv::Status;
using icirv::Suite;

namespace {

Suite mustLoad(const std::string& text) {
    LoadError error;
    auto suite = loadReport(text, error);
    if (!suite) {
        std::fprintf(stderr, "unexpected load failure: %s\n", error.message.c_str());
        ++g_checkFailures;
        return Suite();
    }
    return *suite;
}

// The whole reason this viewer exists: ici's console can print "Error: 0"
// while the suite status is ERROR, because a *required* engine that SKIPs
// escalates the suite via a rule that never appears in the output.
void testSkipEscalationIsExplained() {
    const Suite suite = mustLoad(skipEscalationReport());
    CHECK_EQ(suite.error_count, 0);
    CHECK(suite.suite_status == Status::Error);

    const std::string reason = gateReason(suite);
    CHECK(reason.find("ERROR") != std::string::npos);
    CHECK(reason.find("dead") != std::string::npos);
    CHECK(reason.find("SKIP") != std::string::npos);
    if (reason.find("dead") == std::string::npos) {
        std::fprintf(stderr, "  actual reason: %s\n", reason.c_str());
    }
}

void testNotRunEscalationIsExplained() {
    std::string text = skipEscalationReport();
    const std::string from = R"("status": "SKIP", "summary": "no Python sources", "duration": 0.1,
     "required": true, "evidence": "ESTIMATED")";
    const std::string to = R"("status": "WARN", "summary": "ruff unavailable", "duration": 0.1,
     "required": true, "evidence": "NOT_RUN")";
    const std::size_t at = text.find(from);
    CHECK(at != std::string::npos);
    if (at == std::string::npos) {
        return;
    }
    text.replace(at, from.size(), to);

    const std::string reason = gateReason(mustLoad(text));
    CHECK(reason.find("did not run") != std::string::npos);
    CHECK(reason.find("NOT_RUN") != std::string::npos);
}

void testNonRequiredSkipDoesNotEscalate() {
    std::string text = skipEscalationReport();
    const std::string from = R"("engine_name": "dead", "status": "SKIP", "summary": "no Python sources", "duration": 0.1,
     "required": true)";
    const std::string to = R"("engine_name": "dead", "status": "SKIP", "summary": "no Python sources", "duration": 0.1,
     "required": false)";
    const std::size_t at = text.find(from);
    CHECK(at != std::string::npos);
    if (at == std::string::npos) {
        return;
    }
    text.replace(at, from.size(), to);

    const std::string reason = gateReason(mustLoad(text));
    CHECK(reason.find("dead") == std::string::npos);
}

void testHistogramAndLookup() {
    const Suite suite = mustLoad(skipEscalationReport());
    bool sawPass = false;
    bool sawSkip = false;
    for (const auto& entry : icirv::statusHistogram(suite)) {
        sawPass = sawPass || (entry.first == Status::Pass && entry.second == 1);
        sawSkip = sawSkip || (entry.first == Status::Skip && entry.second == 1);
    }
    CHECK(sawPass);
    CHECK(sawSkip);
    CHECK_EQ(icirv::enginesByStatus(suite, Status::Pass).size(), static_cast<std::size_t>(1));
    CHECK(icirv::enginesByStatus(suite, Status::Fail).empty());
}

void testTemPercent() {
    CHECK_NEAR(icirv::temPercent(mustLoad(minimalReport())), 100.0, 1e-9);
    Suite zero;
    zero.tem_score = 3.0;
    zero.max_tem_score = 0.0;
    CHECK_NEAR(icirv::temPercent(zero), 0.0, 1e-9);
}

void testActionableTargetsOrdering() {
    const Suite suite = mustLoad(minimalReport());
    const auto findings = icirv::actionableTargets(suite);
    // Only the WARN target is actionable; the PASS one is filtered out.
    CHECK_EQ(findings.size(), static_cast<std::size_t>(1));
    if (!findings.empty()) {
        CHECK_EQ(findings.front()->file_path, std::string("src/a.py"));
    }

    Suite ordered;
    icirv::EngineResult engine;
    engine.engine_name = "e";
    engine.targets.push_back({"z.py", 1, 0, "w", Status::Warn, "", ""});
    engine.targets.push_back({"a.py", 1, 0, "e", Status::Error, "", ""});
    engine.targets.push_back({"m.py", 1, 0, "f", Status::Fail, "", ""});
    engine.targets.push_back({"b.py", 1, 0, "p", Status::Pass, "", ""});
    ordered.results.push_back(engine);
    const auto sorted = icirv::actionableTargets(ordered);
    CHECK_EQ(sorted.size(), static_cast<std::size_t>(3));
    if (sorted.size() == 3) {
        CHECK(sorted[0]->status == Status::Error);
        CHECK(sorted[1]->status == Status::Fail);
        CHECK(sorted[2]->status == Status::Warn);
    }
}

void testRealReportReasons() {
    const Suite suite = mustLoad(readFixture("ici_self_report.json"));
    const std::string reason = gateReason(suite);
    CHECK(!reason.empty());
    CHECK(reason.find("WARN") != std::string::npos);
    CHECK(!icirv::actionableTargets(suite).empty());
    CHECK(icirv::temPercent(suite) > 50.0);
}

void testAllPassReason() {
    Suite suite;
    suite.suite_status = Status::Pass;
    icirv::EngineResult engine;
    engine.engine_name = "lint";
    engine.status = Status::Pass;
    engine.required = true;
    engine.evidence = "MEASURED";
    suite.results.push_back(engine);
    CHECK(gateReason(suite).find("all engines passed") != std::string::npos);
}

void testFailReason() {
    Suite suite;
    suite.suite_status = Status::Fail;
    icirv::EngineResult engine;
    engine.engine_name = "test";
    engine.status = Status::Fail;
    engine.required = true;
    engine.evidence = "MEASURED";
    suite.results.push_back(engine);
    const std::string reason = gateReason(suite);
    CHECK(reason.find("FAIL") != std::string::npos);
    CHECK(reason.find("test") != std::string::npos);
}

} // namespace

int main() {
    testSkipEscalationIsExplained();
    testNotRunEscalationIsExplained();
    testNonRequiredSkipDoesNotEscalate();
    testHistogramAndLookup();
    testTemPercent();
    testActionableTargetsOrdering();
    testRealReportReasons();
    testAllPassReason();
    testFailReason();
    return checkSummary();
}
