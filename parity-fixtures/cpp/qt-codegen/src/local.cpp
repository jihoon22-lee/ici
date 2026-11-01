#include <QObject>

class Local : public QObject {
    Q_OBJECT
};

int local_value() { return 1; }

#include "local.moc"
