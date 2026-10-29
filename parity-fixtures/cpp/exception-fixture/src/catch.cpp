#include <exception>
void x();
void log();
bool y();
void a() { try { x(); } catch (...) {} }
void b() { try { x(); } catch ( ... ) { /* ignored */ } }
void c() {
  try { x(); }
  catch (...)
  {
  }
}
void d() { try { x(); } catch (...) { log(); } }
void e() { try { x(); } catch (...) { ; } }
void f() { try { x(); } catch (const std::exception&) {} }
void g() { try { x(); } catch (...) { if (y()) { } } }
const char* s = "catch (...) {}";
void h() { try { x(); } catch (...) { // swallowed
} }
