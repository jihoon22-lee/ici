#include <stdio.h>

static int add(int a, int b) { return a + b; }

int count_odd(const int* items, int n)
{
    int odd = 0;
    for (int i = 0; i < n; i++) {
        if (items[i] & 1) {
            odd++;
        }
    }
    return odd;
}

#ifdef HAVE_MAIN
int main(void)
{
    printf("%d\n", add(1, 2));
    return 0;
}
#endif
