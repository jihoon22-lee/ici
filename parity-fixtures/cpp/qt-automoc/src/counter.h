#pragma once
#include <QObject>

class Counter : public QObject {
    Q_OBJECT
public:
    explicit Counter(QObject *parent = nullptr);
    int value() const;
    void step(int amount);
signals:
    void changed(int value);

private:
    int value_ = 0;
};
