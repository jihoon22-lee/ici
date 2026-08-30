cmake_minimum_required(VERSION 3.16)
project(icirv LANGUAGES CXX)

# Builds the Qt-free CLI and its tests. The GUI has its own CMakeLists under
# src/gui/ because it needs Qt, and keeping them apart is what lets this one be
# linked statically and shipped to a machine that has no Qt at all.
#
# This file sits at the root of the viewer project, which is what ici verifies
# (the repository root is a Python project with its own ici.toml). ici reads it
# and drives configure, build and CTest from here.

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# The parsing and model code lives in a library so the tests can link it without
# dragging in main(). src/gui/CMakeLists.txt builds its own copy under the same
# name for the Qt shell; the two are separate CMake projects and never meet.
add_library(icirv_core STATIC
    src/json_parser.cpp
    src/json_value.cpp
    src/report_model.cpp
    src/summary.cpp
)
target_include_directories(icirv_core PUBLIC include)

add_executable(icirv src/main.cpp)
target_link_libraries(icirv PRIVATE icirv_core)

# Static by default so the binary runs on a host with a much older glibc than
# the builder's — the closed-network target is RHEL 8 (glibc 2.28) while CI
# builds on Ubuntu (2.39), and glibc is not forward compatible.
option(ICIRV_STATIC "Link the CLI statically" ON)
# -static and -fsanitize=address cannot be combined, and ici's sanitize engine
# configures its own tree with the sanitizers on. Asking for both produces a
# build failure rather than a diagnosis, so the static link stands down when
# this is a sanitizer build.
if(ICIRV_STATIC AND NOT CMAKE_CXX_FLAGS MATCHES "-fsanitize")
    target_link_options(icirv PRIVATE -static)
endif()

enable_testing()

foreach(name IN ITEMS json_parser report_model summary)
    add_executable(test_${name} tests/test_${name}.cpp)
    target_link_libraries(test_${name} PRIVATE icirv_core)
    target_include_directories(test_${name} PRIVATE tests)
    # Always the project root. tests/fixtures.hpp opens tests/data/ by relative
    # path, which is the contract C-9 fixed: a test must not depend on who
    # launched it or on whether gcov happened to be installed.
    add_test(NAME test_${name} COMMAND test_${name} WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
endforeach()
