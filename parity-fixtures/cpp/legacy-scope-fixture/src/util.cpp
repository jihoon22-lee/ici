#include "a.hpp"
#include <stdexcept>
struct Guard { ~Guard() { throw std::runtime_error("x"); } };
int unused_helper(int v) { return v * 3; }
static int internal_only(int v) { return v + 2; }
int dup_one(int a) { int s = 0; for (int i = 0; i < a; ++i) { s += i * 2; s -= 1; s ^= 3; } return s; }
int dup_two(int a) { int s = 0; for (int i = 0; i < a; ++i) { s += i * 2; s -= 1; s ^= 3; } return s; }
