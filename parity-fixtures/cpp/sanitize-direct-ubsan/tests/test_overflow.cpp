#include <climits>
int add(int a, int b);
int main(int argc, char **) {
    volatile int big = INT_MAX;
    return add(big, argc) == 0 ? 1 : 0;
}
