#include <csignal>
int main() { std::raise(SIGSEGV); return 0; }
