int useAfterFree();

int main()
{
    return useAfterFree() == 7 ? 0 : 1;
}
