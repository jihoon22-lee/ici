#include "../src/buffer.hpp"

#include <cstdio>
#include <cstdlib>

int main() {
    const std::size_t size = 4;
    int* data = static_cast<int*>(std::malloc(size * sizeof(int)));
    for (std::size_t i = 0; i < size; ++i) {
        data[i] = static_cast<int>(i);
    }
    // index == size reads one past the end.
    const int value = readAt(data, size, size);
    std::printf("%d\n", value);
    std::free(data);
    return 0;
}
