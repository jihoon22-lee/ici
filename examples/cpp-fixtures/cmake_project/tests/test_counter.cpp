#include <QSignalSpy>
#include <QtTest>

#include "counter.hpp"

class TestCounter : public QObject {
    Q_OBJECT

private slots:
    void addEmitsChanged();
    void addZeroIsSilent();
};

void TestCounter::addEmitsChanged() {
    Counter counter;
    QSignalSpy spy(&counter, &Counter::changed);
    counter.add(3);
    QCOMPARE(counter.value(), 3);
    QCOMPARE(spy.count(), 1);
}

void TestCounter::addZeroIsSilent() {
    Counter counter;
    QSignalSpy spy(&counter, &Counter::changed);
    counter.add(0);
    QCOMPARE(spy.count(), 0);
}

QTEST_MAIN(TestCounter)
#include "test_counter.moc"
