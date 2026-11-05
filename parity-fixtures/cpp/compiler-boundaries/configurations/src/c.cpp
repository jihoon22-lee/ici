int always(int n) {
  if (n) { return 1; }
  return 0;
}

#ifdef FEATURE
int feature_only(int n) {
  if (n) { return 2; }
  return 0;
}
#endif

int varies(int n) {
#ifdef FEATURE
  n += 1;
#endif
  return n;
}
