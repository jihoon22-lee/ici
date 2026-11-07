#define DEFINE(name) int name() { return 1; }

int ordinary(int n) {
  while (n > 0) { n--; }
  return n;
}

DEFINE(macro_generated)
