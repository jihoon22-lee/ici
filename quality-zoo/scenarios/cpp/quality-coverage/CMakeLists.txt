cmake_minimum_required(VERSION 3.16)
project(qz_quality_coverage LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

enable_testing()

add_library(quality_analysis OBJECT
    src/clean.cpp
    src/complexity.cpp
    src/coverage.cpp
    src/dup_left.cpp
    src/dup_right.cpp
    src/unused.cpp
)

add_executable(quality_test tests/test_quality.cpp $<TARGET_OBJECTS:quality_analysis>)
add_test(NAME quality_test COMMAND quality_test)
