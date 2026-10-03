#include <cstring>
#include <iostream>

extern "C" const char* quality_zoo_message() noexcept;

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "--self-test") == 0) {
        std::cout << quality_zoo_message() << '\n';
        return 0;
    }
    return 2;
}
