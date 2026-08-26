#include "main_window.hpp"

#include <QCheckBox>
#include <QDesktopServices>
#include <QFileDialog>
#include <QFont>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QLabel>
#include <QPushButton>
#include <QStatusBar>
#include <QTreeView>
#include <QUrl>
#include <QVBoxLayout>

#include <fstream>
#include <sstream>

#include "engine_tree_model.hpp"
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

} // namespace

MainWindow::MainWindow(QWidget* parent) : QMainWindow(parent) {
    auto* central = new QWidget(this);
    auto* layout = new QVBoxLayout(central);

    auto* bar = new QHBoxLayout();
    auto* openButton = new QPushButton(tr("Open report…"), central);
    issuesOnly_ = new QCheckBox(tr("Issues only"), central);
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
    QFont gateFont = gateLabel_->font();
    gateFont.setPointSize(gateFont.pointSize() + 4);
    gateFont.setBold(true);
    gateLabel_->setFont(gateFont);
    gateLabel_->setWordWrap(true);
    layout->addWidget(gateLabel_);

    scoreLabel_ = new QLabel(QString(), central);
    layout->addWidget(scoreLabel_);

    model_ = new EngineTreeModel(this);
    tree_ = new QTreeView(central);
    tree_->setModel(model_);
    tree_->setAlternatingRowColors(true);
    tree_->header()->setStretchLastSection(true);
    layout->addWidget(tree_, 1);

    setCentralWidget(central);
    status_ = new QLabel(tr("Ready"), this);
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
        status_->setText(tr("Cannot read %1").arg(path));
        return;
    }
    icirv::LoadError error;
    suite_ = icirv::loadReport(text, error);
    if (!suite_) {
        // A schema mismatch or a malformed field is reported rather than
        // silently producing an empty-looking but valid window.
        gateLabel_->setText(tr("Could not load report"));
        gateLabel_->setStyleSheet(QStringLiteral("color: #e0645a;"));
        status_->setText(QString::fromStdString(error.message));
        model_->setSuite(nullptr);
        return;
    }
    showSuite();
    setWindowTitle(tr("ici report viewer — %1").arg(path));
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

    model_->setSuite(&suite);
    model_->setIssuesOnly(issuesOnly_->isChecked());
    tree_->expandAll();
    tree_->resizeColumnToContents(EngineTreeModel::ColumnName);
    status_->setText(tr("Loaded"));
}

void MainWindow::openSelectedLocation(const QModelIndex& index) {
    const icirv::Target* target = model_->targetAt(index);
    if (target == nullptr || target->file_path.empty()) {
        return;
    }
    // Best effort: hand the path to the desktop and let it choose an editor.
    QDesktopServices::openUrl(QUrl::fromLocalFile(QString::fromStdString(target->file_path)));
}
