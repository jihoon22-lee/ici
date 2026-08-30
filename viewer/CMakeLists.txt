cmake_minimum_required(VERSION 3.16)
project(icirv LANGUAGES CXX)

# Builds the Qt-free CLI and its tests. The optional GUI is added from
# src/gui/ only when Qt is available; keeping its dependency behind the option
# lets this one be linked statically and shipped to a machine that has no Qt.
#
# This file sits at the root of the viewer project, which is what ici verifies
# (the repository root is a Python project with its own ici.toml). ici reads it
# and drives configure, build and CTest from here.

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

option(ICIRV_BUILD_GUI "Build the Qt report viewer" ON)

if(ICIRV_BUILD_GUI)
    set(CMAKE_AUTOMOC ON)
    # Use whichever Qt major is installed. The static CLI build sets
    # ICIRV_BUILD_GUI=OFF and therefore has no Qt configure-time dependency.
    find_package(QT NAMES Qt6 Qt5 REQUIRED COMPONENTS Widgets Test)
    find_package(Qt${QT_VERSION_MAJOR} REQUIRED COMPONENTS Widgets Test)
endif()

# The parsing and model code lives in a library so the CLI and GUI tests can
# link it without dragging in main().
add_library(icirv_core STATIC
    src/json_parser.cpp
    src/json_value.cpp
    src/report_model.cpp
    src/summary.cpp
)
target_include_directories(icirv_core PUBLIC include)

add_executable(icirv src/main.cpp)
target_link_libraries(icirv PRIVATE icirv_core)

if(ICIRV_BUILD_GUI)
    add_subdirectory(src/gui)
endif()

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
