#pragma once
#include "top.hpp"
inline int inner_value(int a, int b) {
  int total = 0;
  for (int i = 0; i < a; ++i) {
    total += i * b;
    if (total > 1000) {
      total -= 7;
    }
  }
  return total;
}
