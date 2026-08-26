#pragma once

#include <string>
#include <utility>
#include <vector>

#include "report_model.hpp"

namespace icirv {

// Engine counts per status, ordered worst-first.
std::vector<std::pair<Status, int>> statusHistogram(const Suite& suite);

std::vector<const EngineResult*> enginesByStatus(const Suite& suite, Status status);

// Every non-PASS target across all engines, worst status first, then engine
// name, then file path.
std::vector<const Target*> actionableTargets(const Suite& suite);

// TEM as a percentage of the maximum. Returns 0 when max_tem_score is 0.
double temPercent(const Suite& suite);

// Explains why the suite landed on its status.
//
// This exists because ici's own console prints a Pass/Warn/Fail/Error tally
// that can read "Error: 0" while the suite status is ERROR: the tally counts
// engine statuses, but the suite status is decided by a separate rule in
// aggregate_suite_status (src/ici/core/models.py) where a *required* engine
// that SKIPs — or whose evidence is NOT_RUN — escalates the whole suite. That
// rule appears nowhere in the output, so a red gate can have no visible cause.
std::string gateReason(const Suite& suite);

} // namespace icirv
