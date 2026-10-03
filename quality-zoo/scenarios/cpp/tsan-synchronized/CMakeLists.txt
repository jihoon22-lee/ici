cmake_minimum_required(VERSION 3.16)
project(quality_zoo_cpp_tsan_synchronized LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Threads REQUIRED)
enable_testing()

add_executable(tsan_synchronized src/clean.cpp tests/test_clean.cpp)
target_link_libraries(tsan_synchronized PRIVATE Threads::Threads)
add_test(NAME tsan_synchronized COMMAND tsan_synchronized)
