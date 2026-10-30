#define DEFINE(name) int name(){ return 1; }
struct X { int operator()(int n){if(n)return n;return 0;} ~X(){} };
int ordinary(int n){auto lambda=[](){if(true)return 1;return 0;};if(n)return lambda();return 0;}
DEFINE(macro_generated)
template<class T> T add(T a,T b){return a+b;}
int use(){return add(1,2);}
