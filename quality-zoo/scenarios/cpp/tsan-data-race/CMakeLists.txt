cmake_minimum_required(VERSION 3.16)
project(quality_zoo_cpp_tsan_data_race LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Threads REQUIRED)
enable_testing()

add_executable(tsan_data_race src/race.cpp tests/test_race.cpp)
target_link_libraries(tsan_data_race PRIVATE Threads::Threads)
add_test(NAME tsan_data_race COMMAND tsan_data_race)
