#pragma once

#include <QObject>

// Q_OBJECT is the point of this fixture: the class does not link without a
// moc-generated translation unit, so a passing test proves moc ran.
class Counter : public QObject {
    Q_OBJECT

public:
    explicit Counter(QObject* parent = nullptr);

    int value() const;
    void add(int amount);

signals:
    void changed(int value);

private:
    int value_ = 0;
};
