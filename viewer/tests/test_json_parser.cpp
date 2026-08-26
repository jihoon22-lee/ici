#include "check.hpp"

#include "icirv/json_parser.hpp"

#include <string>

using icirv::JsonError;
using icirv::JsonValue;
using icirv::parseJson;

namespace {

// Parses text that is expected to be valid and returns it; records a failure
// and returns a null value otherwise.
JsonValue mustParse(const std::string& text) {
    JsonError error;
    auto value = parseJson(text, error);
    if (!value) {
        std::fprintf(stderr, "unexpected parse failure: %s\n", error.message.c_str());
        ++g_checkFailures;
        return JsonValue();
    }
    return *value;
}

void expectRejected(const std::string& text) {
    JsonError error;
    const auto value = parseJson(text, error);
    CHECK(!value.has_value());
    CHECK(!error.message.empty());
}

void testScalars() {
    CHECK(mustParse("null").isNull());
    CHECK_EQ(mustParse("true").asBool(), true);
    CHECK_EQ(mustParse("false").asBool(true), false);
    CHECK_NEAR(mustParse("42").asNumber(), 42.0, 1e-9);
    CHECK_NEAR(mustParse("-3.5").asNumber(), -3.5, 1e-9);
    CHECK_NEAR(mustParse("1.5e3").asNumber(), 1500.0, 1e-9);
    CHECK_NEAR(mustParse("2E-2").asNumber(), 0.02, 1e-9);
    CHECK_EQ(mustParse("\"hi\"").asString(), std::string("hi"));
    // Wrong-type accessors fall back instead of throwing.
    CHECK_EQ(mustParse("null").asBool(true), true);
    CHECK_NEAR(mustParse("\"x\"").asNumber(7.0), 7.0, 1e-9);
    CHECK(mustParse("5").asString().empty());
}

void testEscapes() {
    CHECK_EQ(mustParse(R"("a\"b")").asString(), std::string("a\"b"));
    CHECK_EQ(mustParse(R"("a\\b")").asString(), std::string("a\\b"));
    CHECK_EQ(mustParse(R"("a\/b")").asString(), std::string("a/b"));
    CHECK_EQ(mustParse(R"("\b\f\n\r\t")").asString(), std::string("\b\f\n\r\t"));
    CHECK_EQ(mustParse(R"("A")").asString(), std::string("A"));
    CHECK_EQ(mustParse(R"("é")").asString(), std::string("\xc3\xa9"));
    CHECK_EQ(mustParse(R"("한")").asString(), std::string("\xed\x95\x9c"));
    // Surrogate pair -> U+1F600
    CHECK_EQ(mustParse(R"("😀")").asString(), std::string("\xf0\x9f\x98\x80"));
    // Lone high surrogate is emitted rather than dropped.
    CHECK(!mustParse(R"("\ud83dx")").asString().empty());
    // High surrogate followed by a non-low escape keeps both.
    CHECK(!mustParse(R"("\ud83dA")").asString().empty());
}

void testContainers() {
    const JsonValue array = mustParse("[1, 2, [3]]");
    CHECK(array.isArray());
    CHECK_EQ(array.size(), static_cast<std::size_t>(3));
    CHECK_NEAR(array.at(0).asNumber(), 1.0, 1e-9);
    CHECK_NEAR(array.at(2).at(0).asNumber(), 3.0, 1e-9);
    CHECK(array.at(99).isNull());
    CHECK(array.member("nope").isNull());

    const JsonValue object = mustParse(R"({"a": 1, "b": {"c": true}})");
    CHECK(object.isObject());
    CHECK_EQ(object.size(), static_cast<std::size_t>(2));
    CHECK(object.has("a"));
    CHECK(!object.has("z"));
    CHECK_EQ(object.member("b").member("c").asBool(), true);
    CHECK(object.member("z").isNull());
    CHECK(object.at(0).isNull());
    CHECK_EQ(object.keys().size(), static_cast<std::size_t>(2));
    CHECK(mustParse("[]").isArray());
    CHECK_EQ(mustParse("{}").size(), static_cast<std::size_t>(0));
    CHECK(mustParse("5").keys().empty());
    // Duplicate keys resolve last-wins.
    CHECK_NEAR(mustParse(R"({"a":1,"a":2})").member("a").asNumber(), 2.0, 1e-9);
}

void testRejections() {
    expectRejected("");
    expectRejected("{");
    expectRejected("[1,");
    expectRejected("{\"a\" 1}");
    expectRejected("{a:1}");
    expectRejected("tru");
    expectRejected("-");
    expectRejected("1e");
    expectRejected("\"unterminated");
    expectRejected(R"("bad \q escape")");
    expectRejected(R"("bad \u00zz")");
    expectRejected("{} trailing");
    expectRejected(std::string("\"ctrl\x01char\""));
}

void testDepthCap() {
    std::string deep;
    for (int i = 0; i <= icirv::kMaxJsonDepth + 2; ++i) {
        deep += "[";
    }
    JsonError error;
    CHECK(!parseJson(deep, error).has_value());
    CHECK(error.line >= 1);
}

void testErrorPosition() {
    JsonError error;
    CHECK(!parseJson("{\n  \"a\": bad\n}", error).has_value());
    CHECK_EQ(error.line, static_cast<std::size_t>(2));
}

} // namespace

int main() {
    testScalars();
    testEscapes();
    testContainers();
    testRejections();
    testDepthCap();
    testErrorPosition();
    return checkSummary();
}
