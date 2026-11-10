static int file_unused(){return 3;}
__attribute__((noinline,visibility("hidden"))) int used_helper(){return 1;}
__attribute__((noinline,visibility("hidden"))) int linker_unused(){return 2;}
int broken( {
