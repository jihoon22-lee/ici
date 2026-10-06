cmake_minimum_required(VERSION 3.16)
project(quality_zoo_cpp_ubsan_signed_overflow LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

enable_testing()
find_package(Threads REQUIRED)

add_executable(test_ubsan_signed_overflow src/fault.cpp src/clean.cpp tests/test_fault.cpp)
target_link_libraries(test_ubsan_signed_overflow PRIVATE Threads::Threads)
add_test(NAME test_ubsan_signed_overflow COMMAND test_ubsan_signed_overflow)
