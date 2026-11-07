struct Crlf {
  ~Crlf() {
    throw 13;
  }
};
void n() {
  try {} catch (...) {
  }
}
