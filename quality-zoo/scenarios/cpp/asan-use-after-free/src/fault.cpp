int useAfterFree()
{
    int *value = new int(7);
    delete value;
    return *value;
}
