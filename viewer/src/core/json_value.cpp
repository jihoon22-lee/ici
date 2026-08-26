#include "json_value.hpp"

namespace icirv {

namespace {
const std::string& emptyString() {
    static const std::string value;
    return value;
}
} // namespace

JsonValue::JsonValue() = default;

JsonValue::JsonValue(bool value) : kind_(Kind::Bool), bool_(value) {}

JsonValue::JsonValue(double value) : kind_(Kind::Number), number_(value) {}

JsonValue::JsonValue(std::string value) : kind_(Kind::String), string_(std::move(value)) {}

JsonValue JsonValue::makeArray(std::vector<JsonValue> items) {
    JsonValue value;
    value.kind_ = Kind::Array;
    value.array_ = std::move(items);
    return value;
}

JsonValue JsonValue::makeObject(std::map<std::string, JsonValue> members) {
    JsonValue value;
    value.kind_ = Kind::Object;
    value.object_ = std::move(members);
    return value;
}

const JsonValue& JsonValue::nullValue() {
    static const JsonValue value;
    return value;
}

JsonValue::Kind JsonValue::kind() const { return kind_; }
bool JsonValue::isNull() const { return kind_ == Kind::Null; }
bool JsonValue::isBool() const { return kind_ == Kind::Bool; }
bool JsonValue::isNumber() const { return kind_ == Kind::Number; }
bool JsonValue::isString() const { return kind_ == Kind::String; }
bool JsonValue::isArray() const { return kind_ == Kind::Array; }
bool JsonValue::isObject() const { return kind_ == Kind::Object; }

bool JsonValue::asBool(bool fallback) const { return isBool() ? bool_ : fallback; }

double JsonValue::asNumber(double fallback) const { return isNumber() ? number_ : fallback; }

const std::string& JsonValue::asString() const { return isString() ? string_ : emptyString(); }

std::size_t JsonValue::size() const {
    if (isArray()) {
        return array_.size();
    }
    return isObject() ? object_.size() : 0;
}

const JsonValue& JsonValue::at(std::size_t index) const {
    if (!isArray() || index >= array_.size()) {
        return nullValue();
    }
    return array_[index];
}

const JsonValue& JsonValue::member(const std::string& key) const {
    if (!isObject()) {
        return nullValue();
    }
    const auto found = object_.find(key);
    return found == object_.end() ? nullValue() : found->second;
}

bool JsonValue::has(const std::string& key) const {
    return isObject() && object_.find(key) != object_.end();
}

std::vector<std::string> JsonValue::keys() const {
    std::vector<std::string> names;
    if (!isObject()) {
        return names;
    }
    names.reserve(object_.size());
    for (const auto& entry : object_) {
        names.push_back(entry.first);
    }
    return names;
}

} // namespace icirv
