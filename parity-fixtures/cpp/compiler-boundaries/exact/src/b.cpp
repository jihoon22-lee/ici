template <typename T>
T pick(T a, T b) {
  return a < b ? b : a;
}

int use_pick() { return pick(1, 2); }
