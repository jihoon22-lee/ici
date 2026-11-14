#include <mutex>
#include <thread>

#include "counter.h"

int main() {
    int total = 0;
    std::mutex guard;
    std::thread worker([&] {
        std::lock_guard<std::mutex> lock(guard);
        total += add_twice(1);
    });
    {
        std::lock_guard<std::mutex> lock(guard);
        total += add_twice(2);
    }
    worker.join();
    return total == 6 ? 0 : 1;
}
