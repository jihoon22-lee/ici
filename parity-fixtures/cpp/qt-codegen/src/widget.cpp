#include "widget.h"
#include "ui_form.h"

Widget::Widget(QObject *parent) : QObject(parent) {
    Ui_Form form;
    form.setupUi(this);
}
