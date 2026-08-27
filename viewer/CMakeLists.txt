cmake_minimum_required(VERSION 3.16)
project(icirv LANGUAGES CXX)

# Builds the Qt-free CLI only. The GUI has its own CMakeLists under src/gui/
# because it needs Qt, and keeping them apart is what lets this one be linked
# statically and shipped to a machine that has no Qt at all.
#
# Note this file is NOT at the repository root: ici's build engine refuses to
# run when it finds a build descriptor there, and the root is a Python project
# anyway.

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(icirv
    src/main.cpp
    src/json_parser.cpp
    src/json_value.cpp
    src/report_model.cpp
    src/summary.cpp
)
target_include_directories(icirv PRIVATE include)

# Static by default so the binary runs on a host with a much older glibc than
# the builder's — the closed-network target is RHEL 8 (glibc 2.28) while CI
# builds on Ubuntu (2.39), and glibc is not forward compatible.
option(ICIRV_STATIC "Link the CLI statically" ON)
if(ICIRV_STATIC)
    target_link_options(icirv PRIVATE -static)
endif()
