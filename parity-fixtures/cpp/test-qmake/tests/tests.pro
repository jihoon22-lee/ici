QT += testlib
QT -= gui
CONFIG += testcase console
CONFIG -= app_bundle
TARGET=test_unit
DESTDIR = $$OUT_PWD/..
SOURCES += test_unit.cpp
