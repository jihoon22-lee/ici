#include "shared.hpp"

int broken() {
    int unused_value = 3;
    return missing_symbol + shared_value();
}
