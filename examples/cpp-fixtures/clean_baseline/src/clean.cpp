#include <string>

// Nothing here should trip any detector. If a fixture-driven test starts
// failing on this file, the engine has gained a false positive.
std::string greet(const std::string& name) {
    if (name.empty()) {
        return "hello";
    }
    return "hello, " + name;
}
