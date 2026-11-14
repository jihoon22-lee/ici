#define CALL(x) x
void a() { break; }
void b() { continue; }
void c() { case 1: x; }
void d() { else x; }
void e() { catch (...) {} }
void f() { try { } x; }
void g() { if consteval x; }
void i() { for x; }
void j() { do {} while (x) }
void k() { a = b }
void m() { if (a) {} else [[unlikely]] {} }
void p() { x = (a ? b : c; }
void q() { if (a { } }
void r() {
    CALL(g)
    if (a) {}
}
void s() { if (a) { return; } }
