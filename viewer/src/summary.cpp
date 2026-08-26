#include "icirv/summary.hpp"

#include <algorithm>

namespace icirv {

namespace {

const Status kStatusOrder[] = {Status::Error, Status::Fail, Status::Skip,
                               Status::Warn,  Status::Pass, Status::Unknown};

// Mirrors aggregate_suite_status: a required engine is treated as not having
// verified anything when it SKIPs or its evidence says NOT_RUN.
bool escalatesSuite(const EngineResult& engine) {
    if (!engine.required) {
        return false;
    }
    return engine.status == Status::Skip || engine.status == Status::Error ||
           engine.evidence == "NOT_RUN";
}

std::string describeEscalation(const EngineResult& engine) {
    if (engine.evidence == "NOT_RUN") {
        return "required engine '" + engine.engine_name + "' did not run (evidence NOT_RUN)";
    }
    return "required engine '" + engine.engine_name + "' reported " +
           statusName(engine.status);
}

bool targetPrecedes(const Target* a, const Target* b) {
    const int severityA = statusSeverity(a->status);
    const int severityB = statusSeverity(b->status);
    if (severityA != severityB) {
        return severityA > severityB;
    }
    return a->file_path < b->file_path;
}

} // namespace

std::vector<std::pair<Status, int>> statusHistogram(const Suite& suite) {
    std::vector<std::pair<Status, int>> histogram;
    for (Status status : kStatusOrder) {
        const int count = static_cast<int>(enginesByStatus(suite, status).size());
        histogram.emplace_back(status, count);
    }
    return histogram;
}

std::vector<const EngineResult*> enginesByStatus(const Suite& suite, Status status) {
    std::vector<const EngineResult*> matches;
    for (const EngineResult& engine : suite.results) {
        if (engine.status == status) {
            matches.push_back(&engine);
        }
    }
    return matches;
}

std::vector<const Target*> actionableTargets(const Suite& suite) {
    std::vector<const Target*> findings;
    for (const EngineResult& engine : suite.results) {
        for (const Target& target : engine.targets) {
            if (target.status != Status::Pass) {
                findings.push_back(&target);
            }
        }
    }
    std::stable_sort(findings.begin(), findings.end(), targetPrecedes);
    return findings;
}

double temPercent(const Suite& suite) {
    if (suite.max_tem_score <= 0.0) {
        return 0.0;
    }
    return suite.tem_score / suite.max_tem_score * 100.0;
}

std::string gateReason(const Suite& suite) {
    const std::string prefix = std::string(statusName(suite.suite_status)) + " — ";
    for (const EngineResult& engine : suite.results) {
        if (escalatesSuite(engine)) {
            return prefix + describeEscalation(engine);
        }
    }
    const std::vector<const EngineResult*> failed = enginesByStatus(suite, Status::Fail);
    if (!failed.empty()) {
        return prefix + "required engine '" + failed.front()->engine_name + "' failed";
    }
    const std::vector<const EngineResult*> warned = enginesByStatus(suite, Status::Warn);
    if (!warned.empty()) {
        return prefix + std::to_string(warned.size()) + " engine(s) reported warnings";
    }
    return prefix + "all engines passed";
}

} // namespace icirv
