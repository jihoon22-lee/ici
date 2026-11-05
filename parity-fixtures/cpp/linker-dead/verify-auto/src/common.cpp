__attribute__((noinline,visibility("hidden"))) int kept_for_one(){return 1;}
__attribute__((noinline,visibility("hidden"))) int dead_everywhere(){return 2;}
__attribute__((noinline)) int exported_default(){return 3;}
