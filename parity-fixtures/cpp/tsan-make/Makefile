# One library source and two threaded test programs; check runs the clean one, check-race the
# one with an unsynchronized counter (a ThreadSanitizer data race under VARIANT=thread).
OUT ?= build/ici-make
FLAGS =
ifeq ($(VARIANT),coverage)
FLAGS = --coverage -O0
endif
ifeq ($(VARIANT),sanitize)
FLAGS = -fsanitize=address,undefined -fno-omit-frame-pointer -g
endif
ifeq ($(VARIANT),thread)
FLAGS = -fsanitize=thread -g -O1
endif

all: $(OUT)/test_counter $(OUT)/test_race

$(OUT)/counter.o: src/counter.cpp src/counter.h
	mkdir -p $(OUT)
	g++ $(FLAGS) -Isrc -c src/counter.cpp -o $@

$(OUT)/%.o: tests/%.cpp src/counter.h
	mkdir -p $(OUT)
	g++ $(FLAGS) -Isrc -c $< -o $@

$(OUT)/test_%: $(OUT)/test_%.o $(OUT)/counter.o
	g++ $(FLAGS) -pthread $^ -o $@

check: all
	cd $(OUT) && ./test_counter

check-race: all
	cd $(OUT) && ./test_counter
	cd $(OUT) && ./test_race

clean:
	rm -rf $(OUT)

.PHONY: all check check-race clean
