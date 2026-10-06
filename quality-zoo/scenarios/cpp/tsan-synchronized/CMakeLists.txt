cmake_minimum_required(VERSION 3.16)
project(quality_zoo_cpp_tsan_synchronized LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

enable_testing()
find_package(Threads REQUIRED)

add_executable(test_tsan_synchronized src/clean.cpp tests/test_clean.cpp)
target_link_libraries(test_tsan_synchronized PRIVATE Threads::Threads)
add_test(NAME test_tsan_synchronized COMMAND test_tsan_synchronized)
