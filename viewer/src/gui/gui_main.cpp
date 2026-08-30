#include <QApplication>

#include "icirv/gui/main_window.hpp"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("icirv"));
    MainWindow window;
    window.show();
    const QStringList args = app.arguments();
    if (args.size() > 1) {
        window.openReport(args.at(1));
    }
    return app.exec();
}
