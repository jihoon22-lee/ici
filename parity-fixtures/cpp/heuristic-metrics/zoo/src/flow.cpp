#include <vector>
#define DEFINE_GETTER(name) int get_##name() { return name; }

static int value = 0;
DEFINE_GETTER(value)

template <typename T>
T clamp_all(std::vector<T>& items, T low, T high) {
    for (auto& item : items) {
        if (item < low) {
            item = low;
        } else if (item > high) {
            item = high;
        }
    }
    return items.empty() ? low : items.front();
}

int dispatch(int op, int a, int b) {
    switch (op) {
    case 0: return a + b;
    case 1: return a - b;
    case 2: return a * b;
    case 3: return b != 0 ? a / b : 0;
    case 4: return a % (b ? b : 1);
    case 5: return a && b;
    case 6: return a || b;
    case 7: return a & b;
    case 8: return a | b;
    case 9: return a ^ b;
    case 10: return a << b;
    case 11: return a >> b;
    case 12: return a == b;
    case 13: return a != b;
    case 14: return a < b;
    case 15: return a > b;
    case 16: return a <= b;
    case 17: return a >= b;
    case 18: return -a;
    case 19: return ~a;
    case 20: return !a;
    case 21: return a + 1;
    case 22: return a - 1;
    case 23: return b + 1;
    case 24: return b - 1;
    default: return 0;
    }
}

int nested(int a, int b, int c, int d) {
    int total = 0;
    for (int i = 0; i < a; ++i) {
        while (b > 0) {
            if (c > 0) {
                for (int j = 0; j < d; ++j) {
                    if (j % 2 == 0 && i % 2 == 0 || c > 3) {
                        try {
                            total += j;
                        } catch (...) {
                            if (total) {
                                continue;
                            } else if (b) {
                                break;
                            } else {
                                goto done;
                            }
                        }
                    }
                }
            }
            --b;
        }
    }
done:
    return total;
}

int with_lambdas(std::vector<int>& items) {
    auto keep = [](int x) { if (x > 0) { return true; } return false; };
    int count = 0;
    for (int item : items) {
        if (keep(item)) count++;
    }
    return [&] { return count && items.size(); }();
}

int conditional(int a) {
#if defined(FAST)
    if (a) { return 1; }
#else
    while (a > 1) { a /= 2; }
#endif
    return a;
}

namespace outer {
namespace inner {
class Widget {
public:
    int size() const { return n ? n : 0; }
    void grow(int by);
private:
    int n = 0;
};
void Widget::grow(int by) {
    if (by > 0 && n < 100) {
        n += by;
    }
}
}  // namespace inner
}  // namespace outer

const char* text() {
    // braces in comments { and strings do not count
    const char* s = "} { \"}\"";
    const char* r = R"x( } { )x";
    return s ? s : r;
}
