#include <QLabel>
#include <QTemporaryFile>
#include <QTreeView>
#include <QtTest>

#include "icirv/gui/main_window.hpp"

class TestMainWindow : public QObject {
    Q_OBJECT

private slots:
    void openingARealReportFillsTheTree();
    void openingAMissingFileClearsTheLoadedReport();
    void openingMalformedJsonClearsTheLoadedReport();
};

namespace {

QLabel* label(MainWindow& window, const char* objectName) {
    return window.findChild<QLabel*>(QString::fromLatin1(objectName));
}

int treeRowCount(MainWindow& window) {
    auto* tree = window.findChild<QTreeView*>(QStringLiteral("engineTree"));
    if (tree == nullptr || tree->model() == nullptr) {
        return -1;
    }
    return tree->model()->rowCount(QModelIndex());
}

void assertCleared(MainWindow& window, const QString& statusFragment) {
    QVERIFY(!window.hasLoadedReport());
    QCOMPARE(treeRowCount(window), 0);

    QLabel* gate = label(window, "gateLabel");
    QLabel* score = label(window, "scoreLabel");
    QLabel* status = label(window, "statusLabel");
    QVERIFY(gate != nullptr);
    QVERIFY(score != nullptr);
    QVERIFY(status != nullptr);
    QCOMPARE(gate->text(), QStringLiteral("Could not load report"));
    QVERIFY(score->text().isEmpty());
    QVERIFY(status->text().contains(statusFragment));
    QCOMPARE(window.windowTitle(), QStringLiteral("ici report viewer"));
}

} // namespace

void TestMainWindow::openingARealReportFillsTheTree() {
    MainWindow window;
    window.openReport(QStringLiteral("tests/data/ici_self_report.json"));

    QVERIFY(window.hasLoadedReport());
    QVERIFY(treeRowCount(window) > 0);

    QLabel* gate = label(window, "gateLabel");
    QLabel* score = label(window, "scoreLabel");
    QLabel* status = label(window, "statusLabel");
    QVERIFY(gate != nullptr);
    QVERIFY(score != nullptr);
    QVERIFY(status != nullptr);
    QVERIFY(gate->text().contains(QStringLiteral("WARN")));
    QVERIFY(!score->text().isEmpty());
    QCOMPARE(status->text(), QStringLiteral("Loaded"));
    QVERIFY(window.windowTitle().contains(QStringLiteral("ici_self_report.json")));
}

void TestMainWindow::openingAMissingFileClearsTheLoadedReport() {
    MainWindow window;
    window.openReport(QStringLiteral("tests/data/ici_self_report.json"));
    QVERIFY(window.hasLoadedReport());
    QVERIFY(treeRowCount(window) > 0);

    const QString missing = QStringLiteral("tests/data/does-not-exist.json");
    window.openReport(missing);

    assertCleared(window, QStringLiteral("Cannot read"));
}

void TestMainWindow::openingMalformedJsonClearsTheLoadedReport() {
    QTemporaryFile broken;
    QVERIFY(broken.open());
    QVERIFY(broken.write(QByteArrayLiteral("{ this is not json")) > 0);
    broken.close();

    MainWindow window;
    window.openReport(QStringLiteral("tests/data/ici_self_report.json"));
    QVERIFY(window.hasLoadedReport());
    QVERIFY(treeRowCount(window) > 0);

    window.openReport(broken.fileName());

    assertCleared(window, QStringLiteral("invalid JSON"));
}

QTEST_MAIN(TestMainWindow)
#include "test_main_window.moc"
