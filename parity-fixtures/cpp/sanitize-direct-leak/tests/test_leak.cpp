#include <cstdlib>
int answer();
int main() {
    void *volatile block = std::malloc(64);
    block = nullptr;
    return answer() == 42 ? 0 : 1;
}
