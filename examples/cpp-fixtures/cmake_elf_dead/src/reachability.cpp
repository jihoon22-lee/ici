namespace {

__attribute__((noinline)) int live_leaf(int value) {
    return value + 1;
}

__attribute__((noinline)) int dead_leaf(int value) {
    return value * 3;
}

}  // namespace

int live_entry(int value) {
    return live_leaf(value) * 2;
}

__attribute__((visibility("hidden"))) int dead_entry(int value) {
    return dead_leaf(value) - 1;
}
