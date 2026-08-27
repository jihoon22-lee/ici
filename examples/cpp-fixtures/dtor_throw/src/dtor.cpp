#include <stdexcept>

// Throwing from a destructor terminates during stack unwinding.
class Bad {
public:
    ~Bad() {
        throw std::runtime_error("thrown while unwinding");
    }
};

int useBad() {
    Bad b;
    return 0;
}
