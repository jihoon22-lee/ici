#include "buffer.hpp"

int readAt(const int* data, std::size_t size, std::size_t index) {
    if (index > size) {
        return -1;
    }
    return data[index];
}
