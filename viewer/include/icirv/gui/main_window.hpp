#pragma once

#include <QMainWindow>

#include <optional>

#include "icirv/report_model.hpp"

class EngineTreeModel;
class QCheckBox;
class QLabel;
class QLineEdit;
class QTreeView;

// Shows one ici report: why the gate landed where it did, then the detail.
class MainWindow : public QMainWindow {
    Q_OBJECT

public:
    explicit MainWindow(QWidget* parent = nullptr);

    // Loads without the dialog, so `icirv-gui <report.json>` works and the load
    // path can be exercised headlessly.
    void openReport(const QString& path);

private slots:
    void chooseReport();
    void openSelectedLocation(const QModelIndex& index);

private:
    EngineTreeModel* model_ = nullptr;
    QTreeView* tree_ = nullptr;
    QLabel* gateLabel_ = nullptr;
    QLabel* scoreLabel_ = nullptr;
    QCheckBox* issuesOnly_ = nullptr;
    QLabel* status_ = nullptr;
    std::optional<icirv::Suite> suite_;

    void showSuite();
};
