#include <cstdlib>
int main() { int* p = (int*)std::malloc(4); std::free(p); return p[0]; }
