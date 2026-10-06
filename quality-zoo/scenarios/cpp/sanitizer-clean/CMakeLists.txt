cmake_minimum_required(VERSION 3.16)
project(quality_zoo_cpp_sanitizer_clean LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

enable_testing()
find_package(Threads REQUIRED)

add_executable(test_sanitizer_clean src/clean.cpp tests/test_clean.cpp)
target_link_libraries(test_sanitizer_clean PRIVATE Threads::Threads)
add_test(NAME test_sanitizer_clean COMMAND test_sanitizer_clean)
