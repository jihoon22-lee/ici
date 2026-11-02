static int helper() { return 1; }
#ifdef USE_HELPER
int use() { return helper(); }
#endif
int keep() { return 0; }
