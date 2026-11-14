int widget(int value) {
    for (int i = 0; i < value; ++i) {
        if (i == 3) {
            return i;
        }
    }
    return value;
}
