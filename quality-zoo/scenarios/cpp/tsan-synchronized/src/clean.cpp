#include <mutex>
#include <thread>

namespace {

std::mutex value_mutex;
int shared_value = 0;

void increment_shared() {
    std::lock_guard<std::mutex> lock(value_mutex);
    shared_value += 1;
}

}  // namespace

void run_synchronized() {
    std::thread first(increment_shared);
    std::thread second(increment_shared);
    first.join();
    second.join();
}
