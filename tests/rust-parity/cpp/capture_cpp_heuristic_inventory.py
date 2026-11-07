"""Development-only frozen brace-heuristic C++ inventory oracle; never called by the Rust product.

Calls the frozen `cpp_complexity._cpp_function_inventory`, `languages.metrics._slice_body` and
`_cpp_cognitive.cpp_cognitive_metric` directly on every C/C++ source and header under
`parity-fixtures/cpp/`, on deterministic line truncations of each (unterminated bodies), on
handwritten edge cases and on a few compact repeat recipes for the bounded limits. Every span
field, the metric lines, each terminated span's body slice and its cognitive metric (or the
exact exception) are recorded. Strings over 4096 code points are stored as length + SHA-256.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
sys.path.insert(0, str(ORACLE / "src"))

from ici.analysis import _cpp_cognitive, cpp_complexity  # noqa: E402
from ici.languages import metrics  # noqa: E402

for module in (_cpp_cognitive, cpp_complexity, metrics):
    assert Path(module.__file__).is_relative_to(ORACLE), module.__file__

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "parity-fixtures/cpp"
OUTPUT = ROOT / "tests/rust-parity/cpp/cpp-heuristic-inventory-oracle.json"
SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx", ".h++", ".inl"}
BLOB_LIMIT = 4096
SPAN_FIELDS = (
    "name",
    "start_line",
    "start_column",
    "body_start_line",
    "body_start_column",
    "end_line",
    "end_column",
    "complexity",
    "max_nesting",
    "excluded_lambdas",
    "function_kind",
    "is_template",
    "preprocessor_conditional",
)
METRIC_FIELDS = (
    "cognitive",
    "max_nesting",
    "unbraced_controls",
    "logical_sequences",
    "excluded_lambdas",
    "preprocessor_conditional",
)


def nested(depth: int, inner: str) -> str:
    return "{" * depth + inner + "}" * depth


def else_if_chain(count: int) -> str:
    return "if (a) {}" + " else if (a) {}" * (count - 1)


HANDWRITTEN = {
    "empty": "",
    "newline-only": "\n",
    "blank-lines": "\n\n\n",
    "simple": "int add(int a, int b) {\n    return a + b;\n}\n",
    "one-line-definitions": "void a() { if (x) {} }\nvoid b() { while (y) { z(); } }\n",
    "loop-is-not-function": "void f(int n) {\n    for (int i = 0; i < n; ++i) {\n"
    "        if (i && n || i) {}\n    }\n}\n",
    "split-signature": "static int\nsplit(\n    int a,\n    int b)\n{\n    return a ? b : a;\n}\n",
    "decisions": "void f() {\n    if (a && b && c || d) {}\n    x = a&&&&b;\n"
    "    switch (v) { case 1: case 2 : break; default: break; }\n"
    "    if constexpr (x) {}\n    if consteval {}\n    if !consteval {}\n"
    "    try { g(); } catch (int) {} catch (...) {}\n    while (q) {}\n}\n",
    "raw-strings": 'void f() {\n    auto a = R"x( } { )x";\n    auto b = u8R"d(\n}\n)d";\n'
    '    auto c = LR"( { )"; auto d = uR"(})"; auto e = UR"(})";\n'
    '    auto g = FOOR"( { )"; if (a) {}\n}\n',
    "raw-string-bad-delimiters": 'void f() {\n    auto a = R"12345678901234567( } )12345678901234567";\n'
    '    auto b = R"has space( { )has space";\n    auto c = R"a\\b( } )a\\b";\n}\n',
    "raw-string-unterminated": 'void f() {\n    auto a = R"x( } {\n}\nvoid g() {}\n',
    "comments-strings-chars": "void f() {\n    // }\n    /* } { */ char c = '}';\n"
    "    const char* s = \"}{\\\"}\"; char d = '\\'';\n    if (c) {}\n}\n",
    "line-splice-comment": "void f() { // comment \\\n    } still comment\n    if (a) {}\n}\n",
    "line-splice-crlf": "void f() { // comment \\\r\n    } not comment\nvoid g() {}\r\n",
    "block-comment-unterminated": "void f() {\n    /* never closed }\n}\n",
    "string-unterminated": 'void f() {\n    const char* s = "open\n    }\n}\n',
    "digit-separators": "void f() {\n    int a = 1'000'000;\n    if (a) {}\n}\n",
    "lambdas": "void f() {\n    auto g = [](int x) { if (x) return 1; return 0; };\n"
    "    auto h = +[]{ while (q) {} };\n    int v = [&]{ return a && b; }();\n"
    "    auto n = []{ return []{ return [] { if (a) {} }; }; };\n"
    "    return [=](int q) mutable -> int { while (q) {} return q; };\n}\n",
    "lambda-initializers": "auto g = [](int a) { return a; };\nauto h =+[]{ return 1; };\n"
    "auto k = + [] { return 2; };\nbool b = x == [y]{ return y; };\n"
    "bool c = x <= [y]{ return y; };\nvoid after() { if (a) {} }\n",
    "lambda-contexts": "void f() {\n    throw [](){ if (a) {} };\n    co_return []{ for (;;) {} };\n"
    "    a[0] = b[[]{ return 1; }()];\n    [[nodiscard]] int z = 0;\n    call(x, []{ if (y) {} });\n"
    "    auto t = c ? []{ if (p) {} } : []{ if (q) {} };\n}\n",
    "lambda-requires": "void f() {\n    auto g = []<typename T>(T t) requires requires (T u) { u.x; } "
    "{ if (t) {} };\n    auto h = []<class T> requires std::integral<T> (T v) { if (v) {} };\n"
    "    auto k = [](int a) -> std::vector<int> { if (a) {} return {}; };\n}\n",
    "constructor-initializers": "Foo::Foo(int a) : a_(a), b_{a} {\n    if (a) {}\n}\n"
    "Bar::Bar() : Base<T>{} {}\nBaz::Baz() : arr_{}, m_(1) { while (x) {} }\n"
    "Qux::Qux() try : a_(1) { g(); } catch (...) { if (e) {} }\n"
    "Arr::Arr() : data_{1, 2}, idx_[0] {}\nstruct S { S() : x(1) {} int x; };\n",
    "operators": "bool operator()(int) const { if (a) {} }\nint& operator[](size_t i) { return v[i]; }\n"
    'long double operator""_x(long double v) { return v; }\n'
    'long double operator "" _y(long double v) { return v; }\n'
    "void* operator new[](size_t n) { return 0; }\nvoid operator delete [] (void* p) noexcept {}\n"
    "operator bool() const { return true; }\nexplicit operator const char*() const { return 0; }\n"
    "X& X::operator=(const X&) { return *this; }\nauto operator<=>(const X&) const = default;\n"
    "X operator ++ (int) { return *this; }\nT::operator std::string() const { return {}; }\n"
    "operator  unsigned   long() { return 0; }\nauto operator->() { return p; }\n"
    "int operatorx() { return 0; }\nint operator_() { return 0; }\nvoid operator()() {}\n"
    "bool operator==(const X&) const { return a && b; }\nvoid operator delete(void* p) {}\n"
    "operator é() {}\nvoid X::operator()(int a) { if (a) {} }\n",
    "operator-window": "int aboperator" + " " * 56 + '""_x(long double v) { return v; }\n'
    "int cdoperator" + " " * 57 + '""_y(long double v) { return v; }\n',
    "requires-clauses": "template <typename T> void f(T t) requires requires (T u) { u.x; } "
    "{ if (t) {} }\ntemplate <typename T> void g(T t) requires (sizeof(T) > 1) { while (t) {} }\n"
    "template <typename T> requires std::integral<T> void h(T t) { if (t) {} }\n"
    "template <typename T> void k(T) requires requires { typename T::x; } { for (;;) {} }\n"
    "template <typename T> void m(T) requires requires (T a) { a; } && true { if (a) {} }\n",
    "templates": "template <typename T> T max(T a, T b) { return a > b ? a : b; }\n"
    "template<> void f<int>() { if (a) {} }\ntemplate\n<class T>\nvoid g(T) {}\n"
    "template <typename T> auto f() -> decltype(T{}) { return {}; }\n"
    "notemplate<int> x() {}\ntemplates <T> y() {}\n",
    "conditional-branches": "void f() {\n#if A\n    if (a) {\n#else\n    if (b) {\n#endif\n"
    "        g();\n    }\n}\n#ifdef X\nvoid h() {}\n#endif\n#if A\nvoid k(int a)\n#else\n"
    "void k(long a)\n#endif\n{ if (a) {} }\n",
    "conditional-variants": "void f() {\n  #  ifdef X\n  #elif Y\n  #ifé\n  # endif\n}\n"
    "void g() {\n#include <x>\n#  ifndef Z\n#endif\n}\nvoid h() {\n#iffy\n}\n",
    "macros": "#define DECLARE(x) void x()\n#define BODY(a) \\\n    do { \\\n        if (a) {} \\\n"
    "    } while (0)\nDECLARE(alpha)\nMY_MACRO(arg)\nlower_macro(arg)\n"
    "TEST(Suite, Name)\n{\n    EXPECT_TRUE(a && b);\n}\nTEST(Suite, Other) {\n    if (a) {}\n}\n"
    "void after() { BODY(x); }\n  # define SPACED( y ) y\nSPACED(1)\nint x; # stray\n"
    "void z() {}\n",
    "macro-in-body": "#define CALL(x) x\nvoid f() {\n    CALL(g)\n    LOG(a)\n    if (a) {}\n}\n",
    "digraphs": "void f() <%\n    if (a) <% g(); %>\n    int x<:1:> = <% 1 %>;\n"
    '    const char* s = "<% %>";\n%>\nvoid g() <% while (b) <% %> %>\n',
    "crlf": "void f() {\r\n    if (a) {\r\n    }\r\n}\r\nvoid g() {}\r\n",
    "cr-only": "void f() {\r    if (a) {}\r}\rvoid g() {}\r",
    "line-break-variants": "void f() {\x0c    if (a) {}\x0b}\x85void g() {\u2028 while (b) {}"
    "\u2029}\x1cvoid h() {\x1d}\x1evoid k() {}\n",
    "unit-separator-is-space": "void f() {\n    if\x1f(a) {}\n    for\x1f(;;) {}\n"
    "    if\x1fconstexpr\x1f(b) {}\n}\nint\x1fg()\x1f{}\n",
    "non-ascii-identifiers": "void é() { if (a) {} }\nvoid fé() { if (b) {} }\nint x²() {}\n"
    "void f\u0301oo() { if (c) {} }\nvoid ok() {\n    if\u0301 (a) {}\n    case\u0301 1:\n"
    "    for\u203f(;;) {}\n    while\u093f(b) {}\n    ²if (c) {}\n    x\u200dif (d) {}\n"
    "    ‿if (e) {}\n    \u093fif (f) {}\n    \u0345while (g) {}\n    x\u093f&&y;\n}\n"
    "void 名前() { if (a) {} }\nstruct Ü { void ß() {} };\nvoid operator\u093f() {}\n"
    "void \u093fname() {}\nvoid f() {\n#if\u093f X\n}\n",
    "non-ascii-keywords": "void f() try\u200d { a(); }\nvoid g() template\u0301<int> {}\n"
    "template\u0301 <class T> void h() {}\nvoid k() { int a = b ? c : d; }\n",
    "replacement-character": "void f() {\n    int \ufffd = 1;\n    if (\ufffd) {}\n}\n",
    "deep-nesting": "void f() {\n"
    + nested(130, "")
    + "\n}\nvoid g() {\n"
    + nested(127, "x;")
    + "\n}\n",
    "deep-controls": "void f() {\n" + "if (a) {" * 130 + "}" * 130 + "\n}\n",
    "deep-unbraced": "void f() {\n" + "if (a) " * 130 + "x;\n}\n",
    "else-if-chain-limit": "void f() {\n"
    + else_if_chain(129)
    + "\n}\nvoid g() {\n"
    + else_if_chain(128)
    + "\n}\n",
    "unbalanced": "void a() { if (a { } }\nvoid b() { ) }\nvoid c() { x[y[0]]; }\n"
    "void d() { int a = (1; }\nvoid e() { [[likely]] if (a) {} }\nvoid f() { [ }\n",
    "unterminated": "void f() {\n    if (a) {\n        g();\n",
    "function-try-truncated": "void f() try { a(); } catch (int) { if (x) {}\n",
    "function-try-non-catch": "void f() try { a(); }\nint b;\nvoid g() { if (a) {} }\n",
    "function-try-multiline-catch": "void f() try {\n    a();\n}\ncatch\n(int e)\n{\n"
    "    if (e) {}\n}\ncatch (...) { b(); }\nvoid g() {}\n",
    "cognitive-errors": "void a() { break; }\nvoid b() { continue; }\nvoid c() { case 1: x; }\n"
    "void d() { else x; }\nvoid e() { catch (...) {} }\nvoid f() { try { } x; }\n"
    "void g() { if consteval x; }\nvoid h() { if ! x; }\nvoid i() { for x; }\n"
    "void j() { do {} while (x) }\nvoid k() { a = b }\nvoid l() { if consteval {} else x; }\n"
    "void m() { if (a) {} else [[unlikely]] {} }\nvoid n() { try x; }\n"
    "void o() { default: ; }\nvoid p() { x = (a ? b : c; }\n",
    "cognitive-valid": "void a() { switch (x) { case 1: if (a) {} break; default: ; } }\n"
    "void b() { label: for (;;) { continue; } goto label; }\n"
    "void c() { x = a && b || c and d or e && f; }\nvoid d() { if (a) b; else c; }\n"
    "void e() { do x; while (y); }\nvoid f() { while (a) if (b) for (;;) c; }\n"
    "void g() { [[likely]] if (a) {} }\nvoid h() { int v[] = {1, {2}}; }\n"
    "void i() { switch (x) { case 1: while (a) {} } }\nvoid j() { try { } catch (int) x; }\n"
    "void k() { if (a) {} else if (b) {} else {} }\nvoid l() { s = S{.a = 1}; }\n"
    "void m() { f(a, b && c, d || e); }\nvoid n() { x = a ? b ? c : d : e; }\n",
    "nested-classes": "class A {\n    void f() { if (a) {} }\n    int g() const { return 1; }\n"
    "    struct B { B() = default; void h() {} };\n};\nnamespace n { void f() {} }\n"
    'extern "C" { void g() {} }\nenum class E { A, B };\nint arr[] = { 1, 2 };\n',
    "noexcept-signatures": "void f() noexcept(noexcept(g())) { if (a) {} }\n"
    "void g() const & noexcept { }\nauto h() -> int { return 1; }\nvoid k() = delete;\n"
    "void decl();\nint main(int argc, char** argv) { return argc; }\n",
    "keyword-heads": "if (a) {}\nwhile (b) {}\nreturn (c) {}\nsizeof(x) {}\n~for() {}\n"
    'A::~A() { if (a) {} }\nstatic_assert(sizeof(int) == 4, "x");\n',
    "hash-mid-line": "int a; # x\nvoid f() {}\nint b; #define Q(x) x\nQ(1)\n",
    "directive-continuation": "#define LONG \\\n    void f() { \\\n    }\nvoid g() { if (a) {} }\n"
    "#define TRAIL \\  \nvoid h() {}\n",
    "pending-expression": "int x = { 1 };\nauto y = f({1, 2}) ;\nstd::vector<int> v{1, 2};\n"
    "Foo::Foo() : m{ {1}, {2} } { if (a) {} }\n",
}

# Explicit line lists that `str.splitlines()` can never produce: line breaks inside a line.
EXPLICIT_LINES = {
    "form-feed-inside-line": ["void f() {\x0c if (a) {}", "}"],
    "vertical-tab-directive": ["#define X(a) \\\x0b", "void g() { if (b) {} }"],
    "next-line-in-comment": ["void f() { // a\x85}", "if (a) {}", "}"],
    "separator-in-string": ['void f() { const char* s = "a\u2028b"; if (c) {} }'],
    "carriage-return-inside": ["void f() {\r if (a) {}", "}"],
    "trailing-empty-lines": ["void f() {", "}", "", ""],
}

# Compact recipes expanded identically on both sides: [[text, repeat], ...].
RECIPES = {
    "token-limit": [["void f() {\n", 1], [";", 1_000_001], ["\n}\n", 1]],
    "token-limit-boundary": [["void f() {\n", 1], [";", 999_998], ["\n}\n", 1]],
    "lambda-limit": [["void f() {\n", 1], ["[]{};", 100_001], ["\n}\n", 1]],
    "scope-depth-limit": [["void f() {\n", 1], ["[", 200_001], ["]", 200_001], ["\n}\n", 1]],
    "scope-count-limit": [["void f() {\n", 1], ["[]", 200_001], ["\n}\n", 1]],
}

# Direct `cpp_cognitive_metric` inputs, including a top-level function-try handler list.
BODIES = {
    "function-try-handlers": "{ a(); } catch (int) { if (b) {} } catch (...) { }",
    "empty": "",
    "attributes-only": "[[nodiscard]]",
    "unterminated-statement": "{ a(); } b",
    "else-if-129": "{ " + else_if_chain(129) + " }",
    "nesting-129": "{ " + nested(129, "") + " }",
    "x-y-zero": "{ x[y[0]]; }",
    "lambda-excluded": "{ auto g = [](int a) { if (a) {} }; if (b) {} }",
    "conditional": "{\n#if A\n if (a) {}\n#endif\n}",
    "digraph-braces": "<% if (a) <% %> %>",
    "and-or-words": "{ x = a and b or c; }",
    "consteval-else": "{ if !consteval { } else { } }",
}
BODY_RECIPES = {
    "lambda-limit": [["{ ", 1], ["[]{};", 100_001], [" }", 1]],
}


def expand(recipe: list) -> str:
    return "".join(text * count for text, count in recipe)


def blob(text: str):
    if len(text) <= BLOB_LIMIT:
        return text
    return {"len": len(text), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def failure(error: Exception) -> dict:
    assert isinstance(error, ValueError), repr(error)
    return {"error": f"{type(error).__name__}: {error}"}


def cognitive(body: str) -> dict:
    try:
        metric = _cpp_cognitive.cpp_cognitive_metric(body)
    except Exception as error:
        return failure(error)
    return {"value": {field: getattr(metric, field) for field in METRIC_FIELDS}}


def inventory(lines: list[str]) -> dict:
    try:
        spans, metric_lines = cpp_complexity._cpp_function_inventory(list(lines))
    except Exception as error:
        return failure(error)
    rows = []
    for span in spans:
        row = {field: getattr(span, field) for field in SPAN_FIELDS}
        if span.end_column is not None:
            body = metrics._slice_body(
                metric_lines,
                span.body_start_line,
                span.body_start_column,
                span.end_line,
                span.end_column,
            )
            row["body"] = blob(body)
            row["cognitive"] = cognitive(body)
        rows.append(row)
    return {"metric_lines": [blob(line) for line in metric_lines], "spans": rows}


def case(name: str, *, text=None, lines=None, recipe=None) -> dict:
    record = {"name": name}
    if recipe is not None:
        record["recipe"] = recipe
        lines = expand(recipe).splitlines()
    elif text is not None:
        record["text"] = text
        lines = text.splitlines()
        record["lines"] = lines
    else:
        record["lines"] = lines
    record["outcome"] = inventory(lines)
    return record


def fixture_sources() -> list[Path]:
    return sorted(
        (path for path in FIXTURES.rglob("*") if path.is_file() and path.suffix in SUFFIXES),
        key=lambda path: path.relative_to(ROOT).as_posix(),
    )


cases = []
for path in fixture_sources():
    relative = path.relative_to(ROOT).as_posix()
    # Mirrors frozen `_scan_cpp`: text-mode read with errors="replace", then splitlines().
    text = path.read_text(encoding="utf-8", errors="replace")
    cases.append(case(f"fixture:{relative}", text=text))
    lines = text.splitlines()
    for offset in sorted({len(lines) // 4, len(lines) // 2, (3 * len(lines)) // 4} - {0}):
        cases.append(case(f"truncated:{relative}@{offset}", lines=lines[:offset]))
for name, text in HANDWRITTEN.items():
    cases.append(case(f"hand:{name}", text=text))
for name, lines in EXPLICIT_LINES.items():
    cases.append(case(f"lines:{name}", lines=lines))
for name, recipe in RECIPES.items():
    cases.append(case(f"recipe:{name}", recipe=recipe))
bodies = [{"name": name, "body": body, "result": cognitive(body)} for name, body in BODIES.items()]
bodies += [
    {"name": f"recipe:{name}", "recipe": recipe, "result": cognitive(expand(recipe))}
    for name, recipe in BODY_RECIPES.items()
]

spans = [span for item in cases if "spans" in item["outcome"] for span in item["outcome"]["spans"]]
summary = {
    "cases": len(cases),
    "inventory_errors": sum("error" in item["outcome"] for item in cases),
    "spans": len(spans),
    "terminated": sum(span["end_column"] is not None for span in spans),
    "unterminated": sum(span["end_column"] is None for span in spans),
    "cognitive_errors": sum("error" in span["cognitive"] for span in spans if "cognitive" in span),
    "bodies": len(bodies),
}
OUTPUT.write_text(
    json.dumps(
        {"oracle": EXPECTED, "summary": summary, "cases": cases, "bodies": bodies},
        indent=2,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(json.dumps(summary))
