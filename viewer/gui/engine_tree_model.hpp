#pragma once

#include <QAbstractItemModel>

#include <vector>

#include "icirv/report_model.hpp"

// Two-level tree: engines at the top, their inspection targets beneath.
//
// Rows are addressed by an internal id rather than by pointer, so the model
// stays valid across a reload of the underlying Suite.
class EngineTreeModel : public QAbstractItemModel {
    Q_OBJECT

public:
    enum Column { ColumnName = 0, ColumnStatus, ColumnDetail, ColumnCount };

    explicit EngineTreeModel(QObject* parent = nullptr);

    void setSuite(const icirv::Suite* suite);
    // Hides targets that already pass, which is most of them on a healthy run.
    void setIssuesOnly(bool issuesOnly);

    const icirv::Target* targetAt(const QModelIndex& index) const;

    QModelIndex index(int row, int column,
                      const QModelIndex& parent = QModelIndex()) const override;
    QModelIndex parent(const QModelIndex& index) const override;
    int rowCount(const QModelIndex& parent = QModelIndex()) const override;
    int columnCount(const QModelIndex& parent = QModelIndex()) const override;
    QVariant data(const QModelIndex& index, int role) const override;
    QVariant headerData(int section, Qt::Orientation orientation, int role) const override;

private:
    const icirv::Suite* suite_ = nullptr;
    bool issues_only_ = true;
    // visible_[engineIndex] holds the target indices shown for that engine.
    std::vector<std::vector<int>> visible_;

    void rebuild();
    const icirv::EngineResult* engineAt(int row) const;
};
