#include "config.hpp"

static_assert(QZ_CXX20 == 1, "C++20 target define was not selected");
static_assert(QZ_HEADER_VERSION == 20, "C++20 include search selected the wrong header");

int target20() {
    return qz_header_value();
}
