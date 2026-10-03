#include <climits>

int signedOverflow(int lhs, int rhs);

int main()
{
    return signedOverflow(INT_MAX, 1);
}
