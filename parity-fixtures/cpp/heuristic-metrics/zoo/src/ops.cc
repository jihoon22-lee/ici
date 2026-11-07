#include <string>

struct X {
    int v[4];
    bool a, b;
};

bool operator()(int) const { if (a) {} }
int& operator[](size_t i) { return v[i]; }
long double operator""_x(long double v) { return v; }
void* operator new[](size_t n) { return 0; }
X& X::operator=(const X&) { return *this; }
auto operator<=>(const X&) const = default;
T::operator std::string() const { return {}; }
bool operator==(const X&) const { return a && b; }
void X::operator()(int a) { if (a) {} }

Foo::Foo(int a) : a_(a), b_{a} {
    if (a) {}
}
Bar::Bar() : Base<T>{} {}
Baz::Baz() : arr_{}, m_(1) { while (x) {} }
Qux::Qux() try : a_(1) { g(); } catch (...) { if (e) {} }
struct S { S() : x(1) {} int x; };
