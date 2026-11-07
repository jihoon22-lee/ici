#include <QtTest>

class Unit : public QObject {
    Q_OBJECT
private slots:
    void passes() { QVERIFY(true); }
    void fails() { QVERIFY(1 + 1 == 3); }
    void unexpectedlyPasses() {
        QEXPECT_FAIL("", "tracked elsewhere", Continue);
        QVERIFY(true);
    }
    void skipped() { QSKIP("not on this host"); }
};

QTEST_APPLESS_MAIN(Unit)
#include "test_unit.moc"
