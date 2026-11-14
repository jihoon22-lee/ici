#include "a.hpp"
#include <stdexcept>
struct CcGuard { ~Guard() { throw std::runtime_error("x"); } };
int cc_helper(int v) { return v * 3; }
static int cc_internal(int v) { return v + 2; }
int cc_one(int a) { int s = 0; for (int i = 0; i < a; ++i) { s += i * 2; s -= 1; s ^= 3; } return s; }
int cc_two(int a) { int s = 0; for (int i = 0; i < a; ++i) { s += i * 2; s -= 1; s ^= 3; } return s; }
int f(){ try { return 1; } catch (...) { } return 0; }
struct Z { ~Z() { throw 2; } };
int g(){ try { return 2; } catch (...) { /* x */ } return 1; }
