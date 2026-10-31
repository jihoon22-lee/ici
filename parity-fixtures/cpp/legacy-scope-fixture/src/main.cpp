#include <cstdio>
int simple(int a) { return a + 1; }
int branchy(int a, int b) {
    int r = 0;
    for (int i = 0; i < a; ++i) {
        if (i % 2 == 0 && b > 0) {
            r += i;
        } else if (i % 3 == 0 || b < 0) {
            r -= i;
        } else {
            while (b > i) { --b; if (b == 7) break; }
        }
        switch (i) { case 1: r++; break; case 2: r--; break; default: break; }
    }
    return r > 0 ? r : -r;
}
struct Counter {
    int value = 0;
    int operator()(int step) {
        if (step > 0) { value += step; }
        return value;
    }
    int nested(int x) const {
        if (x > 0) {
            if (x > 1) {
                if (x > 2) { return 3; }
            }
        }
        auto twice = [](int y) { if (y) { return y * 2; } return 0; };
        return twice(x);
    }
};
int main() {
    Counter counter;
    std::printf("%d %d\n", branchy(simple(3), 2), counter(counter.nested(4)));
    return 0;
}
