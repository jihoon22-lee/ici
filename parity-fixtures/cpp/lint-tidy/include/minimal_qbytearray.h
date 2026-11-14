// A minimal stand-in for QtCore's QByteArray/qgetenv so clazy's qgetenv check fires without a
// Qt installation on the include path.
#pragma once

class QByteArray {
public:
    bool isEmpty() const { return true; }
};

QByteArray qgetenv(const char *name);
