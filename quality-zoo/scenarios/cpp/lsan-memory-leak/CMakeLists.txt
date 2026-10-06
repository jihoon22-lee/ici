cmake_minimum_required(VERSION 3.16)
project(quality_zoo_cpp_lsan_memory_leak LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

enable_testing()
find_package(Threads REQUIRED)

add_executable(test_lsan_memory_leak src/fault.cpp src/clean.cpp tests/test_fault.cpp)
target_link_libraries(test_lsan_memory_leak PRIVATE Threads::Threads)
add_test(NAME test_lsan_memory_leak COMMAND test_lsan_memory_leak)
