int ordinary(int n) {
  if (n) { return 1; }
  for (int i = 0; i < n; ++i) { if (i) { n--; } }
  return 0;
}

struct Box {
  int operator()(int n) { if (n) return n; return 0; }
  int member(int a, int b) const {
    auto twice = [](int v) { if (v) { return v * 2; } return 0; };
    return twice(a) && b ? a : b;
  }
};

namespace geo {
int area(int w, int h) {
  switch (w) {
    case 0: return 0;
    default: break;
  }
  return w * h;
}
}  // namespace geo
