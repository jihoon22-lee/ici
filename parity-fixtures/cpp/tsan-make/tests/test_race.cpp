#include <thread>

#include "counter.h"

int shared_total = 0;

int main() {
    std::thread worker([] { shared_total += add_twice(1); });
    shared_total += add_twice(2);
    worker.join();
    return shared_total == 6 ? 0 : 1;
}
