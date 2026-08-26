#pragma once

#include <cstddef>
#include <optional>
#include <string>

#include "json_value.hpp"

namespace icirv {

struct JsonError {
    std::size_t offset = 0;
    std::size_t line = 1;
    std::string message;
};

// Maximum container nesting accepted. Deeper input is rejected rather than
// risking a stack overflow on hostile or corrupt data.
constexpr int kMaxJsonDepth = 200;

// Parses text as JSON. On failure returns std::nullopt and fills error.
// Never throws.
std::optional<JsonValue> parseJson(const std::string& text, JsonError& error);

} // namespace icirv
