#pragma once

#include <QMainWindow>

#include <optional>

#include "icirv/report_model.hpp"

class EngineTreeModel;
class QCheckBox;
class QLabel;
class QLineEdit;
class QTableWidget;
class QTreeView;

// Shows one ici report: why the gate landed where it did, then the detail.
class MainWindow : public QMainWindow {
    Q_OBJECT

public:
    explicit MainWindow(QWidget* parent = nullptr);

    // Loads without the dialog, so `icirv-gui <report.json>` works and the load
    // path can be exercised headlessly.
    void openReport(const QString& path);

    // Read-only state used by the shell test and by callers that need to
    // distinguish an empty viewer from a report that has no findings.
    bool hasLoadedReport() const { return suite_.has_value(); }

private slots:
    void chooseReport();
    void openSelectedLocation(const QModelIndex& index);

private:
    EngineTreeModel* model_ = nullptr;
    QTreeView* tree_ = nullptr;
    QLabel* gateLabel_ = nullptr;
    QLabel* scoreLabel_ = nullptr;
    QLabel* supportScope_ = nullptr;
    QTableWidget* supportTable_ = nullptr;
    QCheckBox* issuesOnly_ = nullptr;
    QLabel* status_ = nullptr;
    std::optional<icirv::Suite> suite_;

    void clearReport(const QString& statusMessage);
    void clearSupportMatrix();
    void showSupportMatrix(const icirv::SupportMatrix& matrix);
    void showSuite();
};
