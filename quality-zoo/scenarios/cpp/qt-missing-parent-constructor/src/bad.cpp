#include <QObject>

class MissingParent final : public QObject {
public:
    MissingParent() = default;
};
