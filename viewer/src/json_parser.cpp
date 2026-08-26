#include "icirv/json_parser.hpp"

#include <cstdlib>
#include <map>
#include <vector>

namespace icirv {

namespace {

constexpr unsigned int kHighSurrogateBegin = 0xD800;
constexpr unsigned int kHighSurrogateEnd = 0xDBFF;
constexpr unsigned int kLowSurrogateBegin = 0xDC00;
constexpr unsigned int kLowSurrogateEnd = 0xDFFF;
constexpr unsigned int kSurrogateBase = 0x10000;

bool isDigit(char c) { return c >= '0' && c <= '9'; }

int hexDigitValue(char c) {
    if (isDigit(c)) {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

// Appends one Unicode code point as UTF-8.
void appendUtf8(unsigned int codePoint, std::string& out) {
    if (codePoint < 0x80) {
        out += static_cast<char>(codePoint);
        return;
    }
    if (codePoint < 0x800) {
        out += static_cast<char>(0xC0 | (codePoint >> 6));
        out += static_cast<char>(0x80 | (codePoint & 0x3F));
        return;
    }
    if (codePoint < kSurrogateBase) {
        out += static_cast<char>(0xE0 | (codePoint >> 12));
        out += static_cast<char>(0x80 | ((codePoint >> 6) & 0x3F));
        out += static_cast<char>(0x80 | (codePoint & 0x3F));
        return;
    }
    out += static_cast<char>(0xF0 | (codePoint >> 18));
    out += static_cast<char>(0x80 | ((codePoint >> 12) & 0x3F));
    out += static_cast<char>(0x80 | ((codePoint >> 6) & 0x3F));
    out += static_cast<char>(0x80 | (codePoint & 0x3F));
}

// Returns the escape character a backslash sequence maps to, or 0 when the
// sequence needs special handling (\u) or is invalid. A lookup keeps each
// mapping on one line instead of repeating a case/append/break block.
char simpleEscape(char c) {
    switch (c) {
        case '"': return '"';
        case '\\': return '\\';
        case '/': return '/';
        case 'b': return '\b';
        case 'f': return '\f';
        case 'n': return '\n';
        case 'r': return '\r';
        case 't': return '\t';
        default: return '\0';
    }
}

class Parser {
public:
    Parser(const std::string& text, JsonError& error) : text_(text), error_(error) {}

    bool run(JsonValue& out) {
        skipWhitespace();
        if (!parseValue(0, out)) {
            return false;
        }
        skipWhitespace();
        if (pos_ != text_.size()) {
            return fail("trailing content after top-level value");
        }
        return true;
    }

private:
    const std::string& text_;
    JsonError& error_;
    std::size_t pos_ = 0;
    std::size_t line_ = 1;

    bool atEnd() const { return pos_ >= text_.size(); }
    char peek() const { return atEnd() ? '\0' : text_[pos_]; }

    void advance() {
        if (atEnd()) {
            return;
        }
        if (text_[pos_] == '\n') {
            ++line_;
        }
        ++pos_;
    }

    bool fail(const std::string& message) {
        error_.offset = pos_;
        error_.line = line_;
        error_.message = message;
        return false;
    }

    void skipWhitespace() {
        while (!atEnd()) {
            const char c = text_[pos_];
            if (c != ' ' && c != '\t' && c != '\n' && c != '\r') {
                return;
            }
            advance();
        }
    }

    bool expect(char c) {
        if (peek() != c) {
            return fail(std::string("expected '") + c + "'");
        }
        advance();
        return true;
    }

    bool parseValue(int depth, JsonValue& out) {
        if (depth > kMaxJsonDepth) {
            return fail("maximum nesting depth exceeded");
        }
        const char c = peek();
        if (c == '{') {
            return parseObject(depth, out);
        }
        if (c == '[') {
            return parseArray(depth, out);
        }
        if (c == '"') {
            return parseStringValue(out);
        }
        if (c == '-' || isDigit(c)) {
            return parseNumber(out);
        }
        return parseLiteral(out);
    }

    bool parseLiteral(JsonValue& out) {
        if (text_.compare(pos_, 4, "true") == 0) {
            pos_ += 4;
            out = JsonValue(true);
            return true;
        }
        if (text_.compare(pos_, 5, "false") == 0) {
            pos_ += 5;
            out = JsonValue(false);
            return true;
        }
        if (text_.compare(pos_, 4, "null") == 0) {
            pos_ += 4;
            out = JsonValue();
            return true;
        }
        return fail("unexpected token");
    }

    bool parseNumber(JsonValue& out) {
        const std::size_t begin = pos_;
        if (peek() == '-') {
            advance();
        }
        if (!consumeDigits()) {
            return fail("expected digit in number");
        }
        consumeFraction();
        if (!consumeExponent()) {
            return false;
        }
        const std::string literal = text_.substr(begin, pos_ - begin);
        out = JsonValue(std::strtod(literal.c_str(), nullptr));
        return true;
    }

    bool consumeDigits() {
        const std::size_t begin = pos_;
        while (isDigit(peek())) {
            advance();
        }
        return pos_ > begin;
    }

    void consumeFraction() {
        if (peek() != '.') {
            return;
        }
        advance();
        consumeDigits();
    }

    bool consumeExponent() {
        if (peek() != 'e' && peek() != 'E') {
            return true;
        }
        advance();
        if (peek() == '+' || peek() == '-') {
            advance();
        }
        return consumeDigits() ? true : fail("expected digit in exponent");
    }

    bool parseStringValue(JsonValue& out) {
        std::string value;
        if (!parseString(value)) {
            return false;
        }
        out = JsonValue(std::move(value));
        return true;
    }

    bool parseString(std::string& out) {
        if (!expect('"')) {
            return false;
        }
        out.clear();
        while (!atEnd() && peek() != '"') {
            if (!parseStringChar(out)) {
                return false;
            }
        }
        return expect('"');
    }

    bool parseStringChar(std::string& out) {
        const char c = peek();
        if (c == '\\') {
            advance();
            return parseEscape(out);
        }
        if (static_cast<unsigned char>(c) < 0x20) {
            return fail("unescaped control character in string");
        }
        out += c;
        advance();
        return true;
    }

    bool parseEscape(std::string& out) {
        const char c = peek();
        if (c == 'u') {
            advance();
            return parseUnicodeEscape(out);
        }
        const char simple = simpleEscape(c);
        if (simple == '\0') {
            return fail("invalid escape sequence");
        }
        out += simple;
        advance();
        return true;
    }

    bool readHex4(unsigned int& value) {
        value = 0;
        for (int i = 0; i < 4; ++i) {
            const int digit = hexDigitValue(peek());
            if (digit < 0) {
                return fail("invalid \\u escape");
            }
            value = value * 16 + static_cast<unsigned int>(digit);
            advance();
        }
        return true;
    }

    bool parseUnicodeEscape(std::string& out) {
        unsigned int code = 0;
        if (!readHex4(code)) {
            return false;
        }
        if (code < kHighSurrogateBegin || code > kHighSurrogateEnd) {
            appendUtf8(code, out);
            return true;
        }
        return parseLowSurrogate(code, out);
    }

    // A high surrogate must be followed by \uDC00-\uDFFF; anything else is
    // emitted verbatim rather than dropped, so no input data is lost.
    bool parseLowSurrogate(unsigned int high, std::string& out) {
        if (peek() != '\\' || pos_ + 1 >= text_.size() || text_[pos_ + 1] != 'u') {
            appendUtf8(high, out);
            return true;
        }
        advance();
        advance();
        unsigned int low = 0;
        if (!readHex4(low)) {
            return false;
        }
        if (low < kLowSurrogateBegin || low > kLowSurrogateEnd) {
            appendUtf8(high, out);
            appendUtf8(low, out);
            return true;
        }
        const unsigned int combined =
            kSurrogateBase + ((high - kHighSurrogateBegin) << 10) + (low - kLowSurrogateBegin);
        appendUtf8(combined, out);
        return true;
    }

    // Consumes an immediately-closing container, e.g. [] or {}.
    bool consumeEmptyContainer(char closing) {
        skipWhitespace();
        if (peek() != closing) {
            return false;
        }
        advance();
        return true;
    }

    // After one element: 1 = another follows, 0 = container closed, -1 = error.
    int continueContainer(char closing) {
        skipWhitespace();
        if (peek() == ',') {
            advance();
            return 1;
        }
        return expect(closing) ? 0 : -1;
    }

    bool parseArray(int depth, JsonValue& out) {
        advance();
        std::vector<JsonValue> items;
        if (!consumeEmptyContainer(']') && !parseArrayItems(depth, items)) {
            return false;
        }
        out = JsonValue::makeArray(std::move(items));
        return true;
    }

    bool parseArrayItems(int depth, std::vector<JsonValue>& items) {
        while (true) {
            skipWhitespace();
            JsonValue item;
            if (!parseValue(depth + 1, item)) {
                return false;
            }
            items.push_back(std::move(item));
            const int next = continueContainer(']');
            if (next <= 0) {
                return next == 0;
            }
        }
    }

    bool parseObject(int depth, JsonValue& out) {
        advance();
        std::map<std::string, JsonValue> members;
        if (!consumeEmptyContainer('}') && !parseObjectMembers(depth, members)) {
            return false;
        }
        out = JsonValue::makeObject(std::move(members));
        return true;
    }

    // Duplicate keys resolve last-wins, matching common JSON practice.
    bool parseObjectMembers(int depth, std::map<std::string, JsonValue>& members) {
        while (true) {
            skipWhitespace();
            std::string key;
            if (!parseString(key)) {
                return false;
            }
            skipWhitespace();
            if (!expect(':')) {
                return false;
            }
            skipWhitespace();
            JsonValue value;
            if (!parseValue(depth + 1, value)) {
                return false;
            }
            members[key] = std::move(value);
            const int next = continueContainer('}');
            if (next <= 0) {
                return next == 0;
            }
        }
    }
};

} // namespace

std::optional<JsonValue> parseJson(const std::string& text, JsonError& error) {
    error = JsonError{};
    Parser parser(text, error);
    JsonValue value;
    if (!parser.run(value)) {
        return std::nullopt;
    }
    return value;
}

} // namespace icirv
