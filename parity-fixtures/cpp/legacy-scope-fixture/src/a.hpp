#pragma once
#include "b.hpp"
inline int header_fn(int x) { if (x) { return 1; } return 0; }
struct H { ~H() noexcept(false) { throw 1; } };
