CXX ?= c++
OUT ?= build/ici-make

.PHONY: build clean

build: $(OUT)/app $(OUT)/lib/libgreeter.so

$(OUT):
	mkdir -p $(OUT)

$(OUT)/lib:
	mkdir -p $(OUT)/lib

$(OUT)/lib/libgreeter.so: src/greeter.cpp | $(OUT)/lib
	$(CXX) -std=c++17 -fPIC -shared $< -o $@

$(OUT)/app: src/main.cpp $(OUT)/lib/libgreeter.so | $(OUT)
	$(CXX) -std=c++17 $< -L$(OUT)/lib -lgreeter -o $@

clean:
	rm -f $(OUT)/app $(OUT)/lib/libgreeter.so
	rmdir $(OUT)/lib 2>/dev/null || true
	rmdir $(OUT) 2>/dev/null || true
