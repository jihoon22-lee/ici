#include <memory>

int safeOwnedValue()
{
    const auto value = std::make_unique<int>(7);
    return *value;
}
