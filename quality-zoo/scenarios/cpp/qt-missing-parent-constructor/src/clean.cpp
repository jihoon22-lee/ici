#include <QObject>

class ParentAware final : public QObject {
public:
    explicit ParentAware(QObject *parent = nullptr) : QObject(parent) {}
};
