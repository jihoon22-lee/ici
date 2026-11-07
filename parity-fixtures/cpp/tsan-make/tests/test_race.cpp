#include <atomic>
#include <thread>

#include "counter.h"

int shared_total = 0;
std::atomic<int> written{0};

int main() {
    // A relaxed flag orders nothing for ThreadSanitizer: the worker's write is always recorded
    // before the unsynchronized main-thread write that reports the race.
    std::thread worker([] {
        shared_total = add_twice(1);
        written.store(1, std::memory_order_relaxed);
    });
    while (written.load(std::memory_order_relaxed) == 0) {
    }
    shared_total += add_twice(2);
    worker.join();
    return 0;
}
