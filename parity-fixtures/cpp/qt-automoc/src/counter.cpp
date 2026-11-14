#include "counter.h"

Counter::Counter(QObject *parent) : QObject(parent) {}

int Counter::value() const { return value_; }

void Counter::step(int amount) {
    if (amount == 0) {
        return;
    }
    value_ += amount;
    emit changed(value_);
}
