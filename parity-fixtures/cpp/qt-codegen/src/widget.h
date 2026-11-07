#pragma once
#include <QObject>

class Widget : public QObject {
    Q_OBJECT
public:
    explicit Widget(QObject *parent = nullptr);
};
