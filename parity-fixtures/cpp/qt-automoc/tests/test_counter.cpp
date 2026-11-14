#include "counter.h"

int main() {
    Counter counter;
    int seen = 0;
    QObject::connect(&counter, &Counter::changed, [&seen](int value) { seen = value; });
    counter.step(0);
    counter.step(3);
    return counter.value() == 3 && seen == 3 ? 0 : 1;
}
