#include "core/report_model.hpp"
#include "core/summary.hpp"

#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

struct CliOptions {
    std::string path;
    std::string engine;
    std::string status;
    bool targets = false;
    bool help = false;
    bool valid = true;
};

void printUsage(std::ostream& out) {
    out << "Usage: icirv <report.json> [options]\n"
        << "  --engine NAME   only this engine\n"
        << "  --status STATUS only findings with this status (PASS/WARN/FAIL/ERROR/SKIP)\n"
        << "  --targets       list actionable findings instead of the summary\n"
        << "  --help          show this message\n";
}

bool takeValue(const std::vector<std::string>& args, std::size_t& index, std::string& out) {
    if (index + 1 >= args.size()) {
        return false;
    }
    ++index;
    out = args[index];
    return true;
}

bool applyValueOption(const std::vector<std::string>& args, std::size_t& index,
                      const std::string& arg, CliOptions& options) {
    if (arg == "--engine") {
        options.valid = takeValue(args, index, options.engine) && options.valid;
        return true;
    }
    if (arg != "--status") {
        return false;
    }
    options.valid = takeValue(args, index, options.status) && options.valid;
    return true;
}

bool applyFlag(const std::string& arg, CliOptions& options) {
    if (arg == "--help") {
        options.help = true;
        return true;
    }
    if (arg == "--targets") {
        options.targets = true;
        return true;
    }
    return false;
}

void applyPositional(const std::string& arg, CliOptions& options) {
    const bool looksLikeOption = !arg.empty() && arg[0] == '-';
    if (looksLikeOption || !options.path.empty()) {
        options.valid = false;
        return;
    }
    options.path = arg;
}

CliOptions parseArgs(int argc, char** argv) {
    CliOptions options;
    const std::vector<std::string> args(argv + 1, argv + argc);
    for (std::size_t i = 0; i < args.size(); ++i) {
        const std::string& arg = args[i];
        if (applyFlag(arg, options)) {
            continue;
        }
        if (applyValueOption(args, i, arg, options)) {
            continue;
        }
        applyPositional(arg, options);
    }
    return options;
}

bool readFile(const std::string& path, std::string& out) {
    std::ifstream input(path);
    if (!input) {
        return false;
    }
    std::stringstream buffer;
    buffer << input.rdbuf();
    out = buffer.str();
    return true;
}

bool engineSelected(const icirv::EngineResult& engine, const CliOptions& options) {
    return options.engine.empty() || engine.engine_name == options.engine;
}

void printHistogram(const icirv::Suite& suite, std::ostream& out) {
    for (const auto& entry : icirv::statusHistogram(suite)) {
        if (entry.second > 0) {
            out << "  " << icirv::statusName(entry.first) << ": " << entry.second << "\n";
        }
    }
}

void printEngineRow(const icirv::EngineResult& engine, std::ostream& out) {
    out << "  " << icirv::statusName(engine.status) << "  " << engine.engine_name;
    if (!engine.evidence.empty()) {
        out << " [" << engine.evidence << "]";
    }
    out << "  " << engine.summary << "\n";
}

void printSummary(const icirv::Suite& suite, const CliOptions& options, std::ostream& out) {
    out << icirv::gateReason(suite) << "\n\n";
    out << "TEM " << suite.tem_score << " / " << suite.max_tem_score << "  ("
        << icirv::temPercent(suite) << "%)\n\nEngines:\n";
    printHistogram(suite, out);
    out << "\n";
    for (const icirv::EngineResult& engine : suite.results) {
        if (engineSelected(engine, options)) {
            printEngineRow(engine, out);
        }
    }
}

bool targetSelected(const icirv::Target& target, icirv::Status wanted) {
    return wanted == icirv::Status::Unknown || target.status == wanted;
}

void printTargets(const icirv::Suite& suite, const CliOptions& options, std::ostream& out) {
    const icirv::Status wanted =
        options.status.empty() ? icirv::Status::Unknown : icirv::parseStatus(options.status);
    std::size_t shown = 0;
    for (const icirv::Target* target : icirv::actionableTargets(suite)) {
        if (!targetSelected(*target, wanted)) {
            continue;
        }
        out << target->file_path << ":" << target->start_line << "  ["
            << icirv::statusName(target->status) << "]  " << target->target_name << "  "
            << target->message << "\n";
        ++shown;
    }
    out << "\n" << shown << " finding(s)\n";
}

int run(const CliOptions& options) {
    std::string text;
    if (!readFile(options.path, text)) {
        std::cerr << "fatal: cannot read '" << options.path << "'\n";
        return 1;
    }
    icirv::LoadError error;
    const auto suite = icirv::loadReport(text, error);
    if (!suite) {
        std::cerr << "fatal: " << error.message;
        if (error.line > 0) {
            std::cerr << " (line " << error.line << ")";
        }
        std::cerr << "\n";
        return 1;
    }
    if (options.targets) {
        printTargets(*suite, options, std::cout);
    } else {
        printSummary(*suite, options, std::cout);
    }
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    const CliOptions options = parseArgs(argc, argv);
    if (options.help) {
        printUsage(std::cout);
        return 0;
    }
    if (!options.valid || options.path.empty()) {
        printUsage(std::cerr);
        return 1;
    }
    return run(options);
}
