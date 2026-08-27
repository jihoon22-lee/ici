#pragma once
#include <cstddef>

// Reads one element past the end when index == size. Deliberate: the point
// is that the sanitizer, not review, is what catches it.
int readAt(const int* data, std::size_t size, std::size_t index);
