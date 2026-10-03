int duplicate_right(int value) {
    int result = value;
    result += 2;
    result *= 3;
    result -= 1;
    result ^= 7;
    if (result > 10) {
        result -= 10;
    }
    return result;
}
