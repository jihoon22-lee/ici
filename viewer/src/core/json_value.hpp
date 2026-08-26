#pragma once

#include <cstddef>
#include <map>
#include <string>
#include <vector>

namespace icirv {

// A parsed JSON document node.
//
// Accessors never throw and never abort. member() and at() return a shared
// null sentinel when the key or index is absent, so callers can chain lookups
// without a guard at every level — which keeps the mapping code in
// report_model flat enough to stay inside ici's block-nesting budget.
class JsonValue {
public:
    enum class Kind { Null, Bool, Number, String, Array, Object };

    JsonValue();
    explicit JsonValue(bool value);
    explicit JsonValue(double value);
    explicit JsonValue(std::string value);

    static JsonValue makeArray(std::vector<JsonValue> items);
    static JsonValue makeObject(std::map<std::string, JsonValue> members);
    static const JsonValue& nullValue();

    Kind kind() const;
    bool isNull() const;
    bool isBool() const;
    bool isNumber() const;
    bool isString() const;
    bool isArray() const;
    bool isObject() const;

    bool asBool(bool fallback = false) const;
    double asNumber(double fallback = 0.0) const;
    const std::string& asString() const;

    std::size_t size() const;
    const JsonValue& at(std::size_t index) const;
    const JsonValue& member(const std::string& key) const;
    bool has(const std::string& key) const;
    std::vector<std::string> keys() const;

private:
    Kind kind_ = Kind::Null;
    bool bool_ = false;
    double number_ = 0.0;
    std::string string_;
    std::vector<JsonValue> array_;
    std::map<std::string, JsonValue> object_;
};

} // namespace icirv
