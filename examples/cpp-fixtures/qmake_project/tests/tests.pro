TEMPLATE = app
# testcase generates the `check` target and forwards TESTARGS to the binary.
CONFIG += testcase
QT = core testlib
TARGET = test_counter
INCLUDEPATH += $$PWD/../src
SOURCES = test_counter.cpp
LIBS += -L$$OUT_PWD/../src -lcounter
