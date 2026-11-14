# Two test programs over one library source; check runs the passing one, check-all both.
OUT ?= build/ici-make
FLAGS =
ifeq ($(VARIANT),coverage)
FLAGS = --coverage -O0
endif
ifeq ($(VARIANT),sanitize)
FLAGS = -fsanitize=address,undefined -fno-omit-frame-pointer -g
endif

all: $(OUT)/test_calc $(OUT)/test_limit

$(OUT)/calc.o: src/calc.cpp src/calc.h
	mkdir -p $(OUT)
	g++ $(FLAGS) -Isrc -c src/calc.cpp -o $@

$(OUT)/%.o: tests/%.cpp src/calc.h
	mkdir -p $(OUT)
	g++ $(FLAGS) -Isrc -c $< -o $@

$(OUT)/test_%: $(OUT)/test_%.o $(OUT)/calc.o
	g++ $(FLAGS) $^ -o $@

check: all
	cd $(OUT) && ./test_calc

check-all: all
	cd $(OUT) && ./test_calc
	cd $(OUT) && ./test_limit

clean:
	rm -rf $(OUT)

.PHONY: all check check-all clean
