#define QZ_HEADER_VERSION 17

// This first candidate intentionally participates in a small include cycle.
#include "cycle.hpp"

#ifndef QZ_CONFIG17_FUNCTIONS
#define QZ_CONFIG17_FUNCTIONS
inline int qz_header_value() { return 17; }
#endif
