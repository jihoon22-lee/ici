#include "engine_tree_model.hpp"

#include <QBrush>
#include <QColor>
#include <QString>

namespace {

// Engine rows carry this in their internal id; target rows carry their engine
// index, which is how parent() recovers the owner without storing pointers.
constexpr quintptr kEngineRowId = static_cast<quintptr>(-1);

struct StatusStyle {
    icirv::Status status;
    const char* colour;
};

const StatusStyle kStatusStyles[] = {
    {icirv::Status::Pass, "#5bbf7a"},  {icirv::Status::Warn, "#e0b341"},
    {icirv::Status::Fail, "#e0645a"},  {icirv::Status::Error, "#ff4d4d"},
    {icirv::Status::Skip, "#8a8f98"},  {icirv::Status::Unknown, "#8a8f98"},
};

QColor colourFor(icirv::Status status) {
    for (const StatusStyle& style : kStatusStyles) {
        if (style.status == status) {
            return QColor(style.colour);
        }
    }
    return QColor("#8a8f98");
}

QString targetLocation(const icirv::Target& target) {
    return QStringLiteral("%1:%2")
        .arg(QString::fromStdString(target.file_path))
        .arg(target.start_line);
}

} // namespace

EngineTreeModel::EngineTreeModel(QObject* parent) : QAbstractItemModel(parent) {}

void EngineTreeModel::setSuite(const icirv::Suite* suite) {
    beginResetModel();
    suite_ = suite;
    rebuild();
    endResetModel();
}

void EngineTreeModel::setIssuesOnly(bool issuesOnly) {
    beginResetModel();
    issues_only_ = issuesOnly;
    rebuild();
    endResetModel();
}

void EngineTreeModel::rebuild() {
    visible_.clear();
    if (suite_ == nullptr) {
        return;
    }
    visible_.resize(suite_->results.size());
    for (std::size_t e = 0; e < suite_->results.size(); ++e) {
        const icirv::EngineResult& engine = suite_->results[e];
        for (std::size_t t = 0; t < engine.targets.size(); ++t) {
            const bool passing = engine.targets[t].status == icirv::Status::Pass;
            if (issues_only_ && passing) {
                continue;
            }
            visible_[e].push_back(static_cast<int>(t));
        }
    }
}

const icirv::EngineResult* EngineTreeModel::engineAt(int row) const {
    if (suite_ == nullptr || row < 0 || row >= static_cast<int>(suite_->results.size())) {
        return nullptr;
    }
    return &suite_->results[static_cast<std::size_t>(row)];
}

const icirv::Target* EngineTreeModel::targetAt(const QModelIndex& index) const {
    if (!index.isValid() || index.internalId() == kEngineRowId) {
        return nullptr;
    }
    const int engineRow = static_cast<int>(index.internalId());
    const icirv::EngineResult* engine = engineAt(engineRow);
    if (engine == nullptr) {
        return nullptr;
    }
    const std::vector<int>& rows = visible_[static_cast<std::size_t>(engineRow)];
    if (index.row() < 0 || index.row() >= static_cast<int>(rows.size())) {
        return nullptr;
    }
    return &engine->targets[static_cast<std::size_t>(rows[static_cast<std::size_t>(index.row())])];
}

QModelIndex EngineTreeModel::index(int row, int column, const QModelIndex& parent) const {
    if (!hasIndex(row, column, parent)) {
        return QModelIndex();
    }
    if (!parent.isValid()) {
        return createIndex(row, column, kEngineRowId);
    }
    return createIndex(row, column, static_cast<quintptr>(parent.row()));
}

QModelIndex EngineTreeModel::parent(const QModelIndex& index) const {
    if (!index.isValid() || index.internalId() == kEngineRowId) {
        return QModelIndex();
    }
    return createIndex(static_cast<int>(index.internalId()), 0, kEngineRowId);
}

int EngineTreeModel::rowCount(const QModelIndex& parent) const {
    if (suite_ == nullptr) {
        return 0;
    }
    if (!parent.isValid()) {
        return static_cast<int>(suite_->results.size());
    }
    if (parent.internalId() != kEngineRowId) {
        return 0;
    }
    return static_cast<int>(visible_[static_cast<std::size_t>(parent.row())].size());
}

int EngineTreeModel::columnCount(const QModelIndex& parent) const {
    return parent.isValid() && parent.internalId() != kEngineRowId ? 0 : ColumnCount;
}

QVariant EngineTreeModel::data(const QModelIndex& index, int role) const {
    if (!index.isValid()) {
        return QVariant();
    }
    const icirv::Target* target = targetAt(index);
    if (target != nullptr) {
        if (role == Qt::DisplayRole) {
            if (index.column() == ColumnName) {
                return targetLocation(*target);
            }
            if (index.column() == ColumnStatus) {
                return QString::fromLatin1(icirv::statusName(target->status));
            }
            return QString::fromStdString(target->message);
        }
        if (role == Qt::ForegroundRole) {
            return QBrush(colourFor(target->status));
        }
        if (role == Qt::ToolTipRole) {
            return QString::fromStdString(target->snippet);
        }
        return QVariant();
    }

    const icirv::EngineResult* engine = engineAt(index.row());
    if (engine == nullptr) {
        return QVariant();
    }
    if (role == Qt::DisplayRole) {
        if (index.column() == ColumnName) {
            return QString::fromStdString(engine->engine_name);
        }
        if (index.column() == ColumnStatus) {
            return QString::fromLatin1(icirv::statusName(engine->status));
        }
        return QString::fromStdString(engine->summary);
    }
    if (role == Qt::ForegroundRole) {
        return QBrush(colourFor(engine->status));
    }
    if (role == Qt::ToolTipRole) {
        // Evidence is worth surfacing: MEASURED and ESTIMATED mean very
        // different things about how much the result can be trusted.
        return QStringLiteral("evidence: %1  |  required: %2")
            .arg(QString::fromStdString(engine->evidence))
            .arg(engine->required ? QStringLiteral("yes") : QStringLiteral("no"));
    }
    return QVariant();
}

QVariant EngineTreeModel::headerData(int section, Qt::Orientation orientation, int role) const {
    if (role != Qt::DisplayRole || orientation != Qt::Horizontal) {
        return QVariant();
    }
    static const char* const kHeaders[] = {"Engine / Location", "Status", "Detail"};
    if (section < 0 || section >= ColumnCount) {
        return QVariant();
    }
    return QString::fromLatin1(kHeaders[section]);
}
