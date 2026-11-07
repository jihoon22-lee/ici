struct Raw { ~Raw() { const char* r = R"x(throw catch (...) {})x"; (void)r; } };
// a comment continued by a line splice \
struct Fake { ~Fake() { throw 11; } };
struct Real { ~Real() {
  const char c = '}'; throw 12; (void)c;
} };
void k() { try {} catch (...) { /* } */ } }
void m() { const char* q = "\"catch (...) {}\""; (void)q; try {} catch (...) {} }
