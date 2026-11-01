#include "calc.h"

int clamp_add(int left, int right, int limit) {
    int sum = left + right;
    if (sum > limit) {
        return limit;
    }
    return sum;
}
