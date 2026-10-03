#include <atomic>
#include <thread>

namespace {

std::atomic<int> ready{0};
std::atomic<bool> go{false};
int shared_value = 0;

void write_shared() {
    ready.fetch_add(1, std::memory_order_relaxed);
    while (!go.load(std::memory_order_acquire)) {
        std::this_thread::yield();
    }
    shared_value += 1;
}

}  // namespace

void run_race() {
    std::thread first(write_shared);
    std::thread second(write_shared);
    while (ready.load(std::memory_order_acquire) != 2) {
        std::this_thread::yield();
    }
    go.store(true, std::memory_order_release);
    first.join();
    second.join();
}
