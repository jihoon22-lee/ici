#include <stdexcept>
void work();
struct Plain { ~Plain() { throw 1; } };
struct Multi { ~Multi() { if (a) throw 1; else throw 2; } int a; };
struct Split {
  ~Split();
};
Split::~Split()
{
  auto f = [] { throw std::runtime_error("lambda"); };
  f();
}
struct Pending {
  ~Pending()
      noexcept(false)
  {
    throw 3;
  }
};
struct Guarded {
  ~Guarded() {
    try { throw 4; } catch (...) {}
  }
};
struct Rethrow { ~Rethrow() { try { work(); } catch (...) { throw; } } };
struct Comment { ~Comment() { /* throw 5; */ const char* s = "throw"; (void)s; } };
struct Spec { ~Spec() throw() {} };
struct Decl { ~Decl() = default; };
void after() { throw 6; }
struct Nested {
  ~Nested() {
    struct Inner { void g() { throw 7; } };
  }
};
template <class T> struct Box { ~Box(); };
template <class T> Box<T>::~Box() {
  throw 8; throw 9;
}
struct Later {
  ~Later() {
    work();
  }
  void h() { throw 10; }
};
