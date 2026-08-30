#include "icirv/gui/main_window.hpp"

#include <QCheckBox>
#include <QDesktopServices>
#include <QFileDialog>
#include <QFont>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QLabel>
#include <QPushButton>
#include <QStatusBar>
#include <QStringList>
#include <QTableWidget>
#include <QTreeView>
#include <QUrl>
#include <QVBoxLayout>

#include <fstream>
#include <sstream>

#include "icirv/gui/engine_tree_model.hpp"
#include "icirv/summary.hpp"

namespace {

QColor colourForStatus(icirv::Status status) {
    if (status == icirv::Status::Pass) {
        return QColor("#5bbf7a");
    }
    if (status == icirv::Status::Warn) {
        return QColor("#e0b341");
    }
    if (status == icirv::Status::Skip) {
        return QColor("#8a8f98");
    }
    return QColor("#e0645a");
}

bool readFile(const QString& path, std::string& out) {
    std::ifstream input(path.toStdString());
    if (!input) {
        return false;
    }
    std::stringstream buffer;
    buffer << input.rdbuf();
    out = buffer.str();
    return true;
}

QString joinValues(const std::vector<std::string>& values) {
    QStringList converted;
    converted.reserve(static_cast<int>(values.size()));
    for (const std::string& value : values) {
        converted.push_back(QString::fromStdString(value));
    }
    return converted.isEmpty() ? QStringLiteral("—") : converted.join(QStringLiteral(", "));
}

QString optionalMode(const std::optional<std::string>& mode) {
    return mode.has_value() ? QString::fromStdString(mode.value()) : QStringLiteral("—");
}

QString supportState(const icirv::SupportEntry& entry) {
    if (!entry.applicable) {
        return QStringLiteral("not-applicable");
    }
    return entry.enabled ? QStringLiteral("applicable") : QStringLiteral("disabled");
}

QString supportLanguage(const icirv::SupportEntry& entry) {
    QString language = QString::fromStdString(entry.language);
    if (!entry.frameworks.empty()) {
        language += QStringLiteral(" (") + joinValues(entry.frameworks) + QStringLiteral(")");
    }
    return language;
}

} // namespace

MainWindow::MainWindow(QWidget* parent) : QMainWindow(parent) {
    auto* central = new QWidget(this);
    auto* layout = new QVBoxLayout(central);

    auto* bar = new QHBoxLayout();
    auto* openButton = new QPushButton(tr("Open report…"), central);
    openButton->setObjectName(QStringLiteral("openButton"));
    openButton->setAccessibleName(tr("Open report file"));
    issuesOnly_ = new QCheckBox(tr("Issues only"), central);
    issuesOnly_->setObjectName(QStringLiteral("issuesOnly"));
    issuesOnly_->setAccessibleName(tr("Show issues only"));
    issuesOnly_->setChecked(true);
    bar->addWidget(openButton);
    bar->addStretch(1);
    bar->addWidget(issuesOnly_);
    layout->addLayout(bar);

    // The gate reason gets the largest type on screen. ici's console can print
    // "Error: 0" while the suite is ERROR, because the tally counts engine
    // statuses and the suite status comes from a different rule that appears
    // nowhere in the output. Saying it plainly is the reason this app exists.
    gateLabel_ = new QLabel(tr("No report loaded"), central);
    gateLabel_->setObjectName(QStringLiteral("gateLabel"));
    QFont gateFont = gateLabel_->font();
    gateFont.setPointSize(gateFont.pointSize() + 4);
    gateFont.setBold(true);
    gateLabel_->setFont(gateFont);
    gateLabel_->setWordWrap(true);
    layout->addWidget(gateLabel_);

    scoreLabel_ = new QLabel(QString(), central);
    scoreLabel_->setObjectName(QStringLiteral("scoreLabel"));
    layout->addWidget(scoreLabel_);

    supportScope_ = new QLabel(tr("No support matrix in report"), central);
    supportScope_->setObjectName(QStringLiteral("supportScope"));
    supportScope_->setAccessibleName(tr("Project support scope"));
    supportScope_->setWordWrap(true);
    layout->addWidget(supportScope_);

    supportTable_ = new QTableWidget(central);
    supportTable_->setObjectName(QStringLiteral("supportMatrix"));
    supportTable_->setAccessibleName(tr("Engine capability matrix"));
    supportTable_->setColumnCount(9);
    supportTable_->setHorizontalHeaderLabels(
        {tr("Engine"), tr("Language"), tr("Mode"), tr("Active"), tr("State"), tr("Evidence"),
         tr("Confidence"), tr("Tools"), tr("Fallback")});
    supportTable_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    supportTable_->setSelectionBehavior(QAbstractItemView::SelectRows);
    supportTable_->setSelectionMode(QAbstractItemView::SingleSelection);
    supportTable_->setAlternatingRowColors(true);
    supportTable_->setSortingEnabled(false);
    supportTable_->verticalHeader()->setVisible(false);
    supportTable_->horizontalHeader()->setStretchLastSection(true);
    supportTable_->setVisible(false);
    layout->addWidget(supportTable_);

    model_ = new EngineTreeModel(this);
    tree_ = new QTreeView(central);
    tree_->setObjectName(QStringLiteral("engineTree"));
    tree_->setAccessibleName(tr("Verification engine results"));
    tree_->setModel(model_);
    tree_->setAlternatingRowColors(true);
    tree_->header()->setStretchLastSection(true);
    layout->addWidget(tree_, 1);

    setCentralWidget(central);
    status_ = new QLabel(tr("Ready"), this);
    status_->setObjectName(QStringLiteral("statusLabel"));
    statusBar()->addWidget(status_);

    connect(openButton, &QPushButton::clicked, this, &MainWindow::chooseReport);
    connect(issuesOnly_, &QCheckBox::toggled, model_, &EngineTreeModel::setIssuesOnly);
    connect(tree_, &QTreeView::doubleClicked, this, &MainWindow::openSelectedLocation);

    resize(1100, 720);
    setWindowTitle(tr("ici report viewer"));
}

void MainWindow::chooseReport() {
    const QString path = QFileDialog::getOpenFileName(this, tr("Open an ici JSON report"),
                                                      QString(), tr("JSON (*.json)"));
    if (path.isEmpty()) {
        return;
    }
    openReport(path);
}

void MainWindow::openReport(const QString& path) {
    std::string text;
    if (!readFile(path, text)) {
        clearReport(tr("Cannot read %1").arg(path));
        return;
    }
    icirv::LoadError error;
    suite_ = icirv::loadReport(text, error);
    if (!suite_) {
        // A schema mismatch or a malformed field is reported rather than
        // silently producing an empty-looking but valid window. Clearing the
        // previous suite first also prevents stale labels and tree rows from
        // being mistaken for the failed replacement.
        QString message = QString::fromStdString(error.message);
        if (error.line > 0) {
            message += tr(" (line %1)").arg(static_cast<qulonglong>(error.line));
        }
        clearReport(message);
        return;
    }
    showSuite();
    setWindowTitle(tr("ici report viewer — %1").arg(path));
}

void MainWindow::clearReport(const QString& statusMessage) {
    suite_.reset();
    model_->setSuite(nullptr);
    clearSupportMatrix();
    gateLabel_->setText(tr("Could not load report"));
    gateLabel_->setStyleSheet(QStringLiteral("color: #e0645a;"));
    scoreLabel_->clear();
    setWindowTitle(tr("ici report viewer"));
    status_->setText(statusMessage);
}

void MainWindow::showSuite() {
    const icirv::Suite& suite = suite_.value();
    gateLabel_->setText(QString::fromStdString(icirv::gateReason(suite)));
    gateLabel_->setStyleSheet(
        QStringLiteral("color: %1;").arg(colourForStatus(suite.suite_status).name()));

    scoreLabel_->setText(tr("TEM %1 / %2  (%3%)   —   %4 engines, %5 actionable finding(s)")
                             .arg(suite.tem_score, 0, 'f', 2)
                             .arg(suite.max_tem_score, 0, 'f', 2)
                             .arg(icirv::temPercent(suite), 0, 'f', 1)
                             .arg(suite.results.size())
                             .arg(icirv::actionableTargets(suite).size()));

    if (suite.support_matrix.has_value()) {
        showSupportMatrix(suite.support_matrix.value());
    } else {
        clearSupportMatrix();
    }

    model_->setSuite(&suite);
    model_->setIssuesOnly(issuesOnly_->isChecked());
    tree_->expandAll();
    tree_->resizeColumnToContents(EngineTreeModel::ColumnName);
    status_->setText(tr("Loaded"));
}

void MainWindow::clearSupportMatrix() {
    if (supportScope_ == nullptr || supportTable_ == nullptr) {
        return;
    }
    supportScope_->setText(tr("No support matrix in report"));
    supportTable_->clearContents();
    supportTable_->setRowCount(0);
    supportTable_->setVisible(false);
}

void MainWindow::showSupportMatrix(const icirv::SupportMatrix& matrix) {
    if (supportScope_ == nullptr || supportTable_ == nullptr) {
        return;
    }
    supportScope_->setText(tr("Project scope: languages=%1  frameworks=%2")
                               .arg(joinValues(matrix.project_languages))
                               .arg(joinValues(matrix.project_frameworks)));
    supportTable_->clearContents();
    supportTable_->setRowCount(static_cast<int>(matrix.entries.size()));
    for (int row = 0; row < static_cast<int>(matrix.entries.size()); ++row) {
        const icirv::SupportEntry& entry = matrix.entries[static_cast<std::size_t>(row)];
        const QStringList tools = [&entry]() {
            QStringList values;
            if (!entry.required_tools.empty()) {
                values.push_back(QStringLiteral("required: ") + joinValues(entry.required_tools));
            }
            if (!entry.optional_tools.empty()) {
                values.push_back(QStringLiteral("optional: ") + joinValues(entry.optional_tools));
            }
            return values;
        }();
        const QStringList columns = {
            QString::fromStdString(entry.engine_name),
            supportLanguage(entry),
            QString::fromStdString(entry.mode),
            optionalMode(entry.active_mode),
            supportState(entry),
            QString::fromStdString(entry.evidence),
            QString::fromStdString(entry.confidence),
            tools.isEmpty() ? QStringLiteral("—") : tools.join(QStringLiteral("; ")),
            optionalMode(entry.fallback_mode),
        };
        for (int column = 0; column < columns.size(); ++column) {
            auto* item = new QTableWidgetItem(columns[column]);
            supportTable_->setItem(row, column, item);
        }
        QString detail = QString::fromStdString(entry.reason);
        if (!entry.limitations.empty()) {
            if (!detail.isEmpty()) {
                detail += QStringLiteral("\n");
            }
            detail += joinValues(entry.limitations);
        }
        if (!detail.isEmpty()) {
            for (int column = 0; column < supportTable_->columnCount(); ++column) {
                supportTable_->item(row, column)->setToolTip(detail);
            }
        }
    }
    supportTable_->resizeColumnsToContents();
    supportTable_->setVisible(true);
}

void MainWindow::openSelectedLocation(const QModelIndex& index) {
    const icirv::Target* target = model_->targetAt(index);
    if (target == nullptr || target->file_path.empty()) {
        return;
    }
    // Best effort: hand the path to the desktop and let it choose an editor.
    QDesktopServices::openUrl(QUrl::fromLocalFile(QString::fromStdString(target->file_path)));
}
