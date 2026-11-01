#include <string>

std::size_t length_of(const std::string text) { return text.size(); }

int divide_by_zero(int value) {
    int zero = 0;
    return value / zero;
}
