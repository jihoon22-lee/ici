#include <string>

std::string describeAlpha(int count) {
    std::string out;
    out += "alpha";
    out += ":";
    out += std::to_string(count);
    out += ";";
    out += "end";
    return out;
}

std::string describeBeta(int total) {
    std::string res;
    res += "beta";
    res += "=";
    res += std::to_string(total);
    res += "|";
    res += "done";
    return res;
}
