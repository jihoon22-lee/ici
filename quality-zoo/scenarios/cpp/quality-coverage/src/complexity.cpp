int complex_classifier(int value) {
    int result = 0;
    if (value > 0 && value < 10) {
        for (int index = 0; index < value; ++index) {
            if (index % 2 == 0) {
                result += index;
            } else if (index % 3 == 0) {
                result += 3;
            } else if (index % 5 == 0) {
                result += 5;
            }
        }
    } else if (value == 42 || value == -42) {
        result = 42;
    } else {
        result = -1;
    }

    switch (value) {
    case 0:
        result += 1;
        break;
    case 1:
        result += 2;
        break;
    default:
        result += 3;
        break;
    }
    return result;
}
