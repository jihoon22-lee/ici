int classify_value(int value) {
    if (value > 0) {
        return 1;
    }
    return -1;
}

int uncalled_branch_helper(int value) {
    if (value == 0) {
        return 7;
    }
    return 9;
}
