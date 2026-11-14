#include "shared.hpp"

int noted(unsigned count) {
    int total = 0;
    for (int index = 0; index < count; ++index) {
        total += index;
    }
    return total + shared_value();
}

[[deprecated("use noted")]] int retired();

int caller() { return retired(); }
