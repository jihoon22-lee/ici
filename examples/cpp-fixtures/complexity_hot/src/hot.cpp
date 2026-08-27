// Deliberately over the cyclomatic threshold so the detector has something
// real to find. Each branch is trivial; the count is the point.
int classify(int v) {
    int total = 0;
    if (v == 1) { total += 1; }
    if (v == 2) { total += 2; }
    if (v == 3) { total += 3; }
    if (v == 4) { total += 4; }
    if (v == 5) { total += 5; }
    if (v == 6) { total += 6; }
    if (v == 7) { total += 7; }
    if (v == 8) { total += 8; }
    if (v == 9) { total += 9; }
    if (v == 10) { total += 10; }
    if (v == 11) { total += 11; }
    if (v == 12) { total += 12; }
    if (v == 13) { total += 13; }
    if (v == 14) { total += 14; }
    if (v == 15) { total += 15; }
    if (v == 16) { total += 16; }
    return total;
}
