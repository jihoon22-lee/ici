#include "config.hpp"

static_assert(QZ_CXX17 == 1, "C++17 target define was not selected");
static_assert(QZ_HEADER_VERSION == 17, "C++17 include search selected the wrong header");

int target17() {
    return qz_header_value();
}
