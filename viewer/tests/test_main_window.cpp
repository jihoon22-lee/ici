#include <QLabel>
#include <QFile>
#include <QTemporaryDir>
#include <QTemporaryFile>
#include <QTableWidget>
#include <QTreeView>
#include <QtTest>

#include "icirv/gui/main_window.hpp"
#include "fixtures.hpp"

class TestMainWindow : public QObject {
    Q_OBJECT

private slots:
    void openingARealReportFillsTheTree();
    void openingASupportMatrixShowsScopeAndRows();
    void openingAnOmittedOrNullMatrixClearsTheCapabilityView();
    void openingEachGateStatusUsesItsColour();
    void openingAMissingFileClearsTheLoadedReport();
    void openingMalformedJsonClearsTheLoadedReport();
    void openingMalformedSupportMatrixClearsTheLoadedReport();
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

QTableWidget* supportTable(MainWindow& window) {
    return window.findChild<QTableWidget*>(QStringLiteral("supportMatrix"));
}

QLabel* supportScope(MainWindow& window) {
    return label(window, "supportScope");
}

QString writeReport(const QString& text, QTemporaryDir& directory, const QString& name) {
    const QString path = directory.filePath(name);
    QFile report(path);
    if (!report.open(QIODevice::WriteOnly | QIODevice::Text)) {
        return QString();
    }
    const QByteArray json = text.toUtf8();
    if (report.write(json) != json.size()) {
        return QString();
    }
    report.close();
    return path;
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

void TestMainWindow::openingASupportMatrixShowsScopeAndRows() {
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = writeReport(QString::fromStdString(validSupportMatrixReport()), directory,
                                     QStringLiteral("support.json"));
    QVERIFY(!path.isEmpty());

    MainWindow window;
    window.openReport(path);

    QVERIFY(window.hasLoadedReport());
    QTableWidget* table = supportTable(window);
    QLabel* scope = supportScope(window);
    QVERIFY(table != nullptr);
    QVERIFY(scope != nullptr);
    QCOMPARE(table->rowCount(), 2);
    QCOMPARE(table->item(0, 0)->text(), QStringLiteral("lint"));
    QCOMPARE(table->item(0, 1)->text(), QStringLiteral("python"));
    QCOMPARE(table->item(0, 2)->text(), QStringLiteral("tool-backed"));
    QCOMPARE(table->item(1, 1)->text(), QStringLiteral("cpp (qt)"));
    QCOMPARE(table->item(1, 4)->text(), QStringLiteral("not-applicable"));
    QVERIFY(scope->text().contains(QStringLiteral("python, cpp")));
    QVERIFY(scope->text().contains(QStringLiteral("qt")));
}

void TestMainWindow::openingAnOmittedOrNullMatrixClearsTheCapabilityView() {
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString validPath =
        writeReport(QString::fromStdString(validSupportMatrixReport()), directory,
                    QStringLiteral("valid.json"));
    const QString omittedPath =
        writeReport(QString::fromStdString(minimalV3Report()), directory,
                    QStringLiteral("omitted.json"));
    const QString nullPath =
        writeReport(QString::fromStdString(nullSupportMatrixReport()), directory,
                    QStringLiteral("null.json"));
    QVERIFY(!validPath.isEmpty());
    QVERIFY(!omittedPath.isEmpty());
    QVERIFY(!nullPath.isEmpty());

    MainWindow window;
    window.openReport(validPath);
    QTableWidget* table = supportTable(window);
    QLabel* scope = supportScope(window);
    QVERIFY(table != nullptr);
    QVERIFY(scope != nullptr);
    QCOMPARE(table->rowCount(), 2);

    window.openReport(omittedPath);
    QVERIFY(window.hasLoadedReport());
    QCOMPARE(table->rowCount(), 0);
    QVERIFY(scope->text().contains(QStringLiteral("No support matrix")));

    window.openReport(validPath);
    QCOMPARE(table->rowCount(), 2);
    window.openReport(nullPath);
    QVERIFY(window.hasLoadedReport());
    QCOMPARE(table->rowCount(), 0);
    QVERIFY(scope->text().contains(QStringLiteral("No support matrix")));
}

void TestMainWindow::openingEachGateStatusUsesItsColour() {
    struct StatusCase {
        const char* status;
        const char* colour;
    };
    const StatusCase cases[] = {
        {"PASS", "#5bbf7a"},
        {"WARN", "#e0b341"},
        {"SKIP", "#8a8f98"},
        {"FAIL", "#e0645a"},
        {"ERROR", "#e0645a"},
    };
    const QString reportTemplate = QStringLiteral(
        R"({
  "schema_version": "ici.result/v2",
  "suite_status": "%1",
  "tem_score": 0.0,
  "passed_count": 0, "warned_count": 0, "failed_count": 0,
  "error_count": 0, "skipped_count": 0, "total_count": 0,
  "results": []
})");
    QTemporaryDir directory;
    QVERIFY(directory.isValid());

    for (const StatusCase& statusCase : cases) {
        const QString path = directory.filePath(QString::fromLatin1(statusCase.status) +
                                                 QStringLiteral(".json"));
        QFile report(path);
        QVERIFY(report.open(QIODevice::WriteOnly | QIODevice::Text));
        const QByteArray json = reportTemplate.arg(QString::fromLatin1(statusCase.status)).toUtf8();
        QCOMPARE(report.write(json), static_cast<qint64>(json.size()));
        report.close();

        MainWindow window;
        window.openReport(path);
        QLabel* gate = label(window, "gateLabel");
        QVERIFY(gate != nullptr);
        QCOMPARE(gate->styleSheet(),
                 QStringLiteral("color: %1;").arg(QString::fromLatin1(statusCase.colour)));
    }
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

void TestMainWindow::openingMalformedSupportMatrixClearsTheLoadedReport() {
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString validPath =
        writeReport(QString::fromStdString(validSupportMatrixReport()), directory,
                    QStringLiteral("valid.json"));
    const QString malformedPath =
        writeReport(QString::fromStdString(malformedSupportMatrixReport()), directory,
                    QStringLiteral("malformed.json"));
    QVERIFY(!validPath.isEmpty());
    QVERIFY(!malformedPath.isEmpty());

    MainWindow window;
    window.openReport(validPath);
    QTableWidget* table = supportTable(window);
    QVERIFY(table != nullptr);
    QCOMPARE(table->rowCount(), 2);

    window.openReport(malformedPath);
    assertCleared(window, QStringLiteral("project_languages"));
    QCOMPARE(table->rowCount(), 0);
}

QTEST_MAIN(TestMainWindow)
#include "test_main_window.moc"
