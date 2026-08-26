cmake_minimum_required(VERSION 3.16)
project(icirv_gui LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Widgets)

# The core is Qt-free on purpose: it is what ici's own CI verifies, and keeping
# Qt out of it is what lets every engine run against this directory. The GUI
# links it as a plain static library.
add_library(icirv_core STATIC
    ../src/json_parser.cpp
    ../src/json_value.cpp
    ../src/report_model.cpp
    ../src/summary.cpp
)
target_include_directories(icirv_core PUBLIC ../include)

add_executable(icirv-gui
    main.cpp
    engine_tree_model.cpp
    main_window.cpp
)
target_link_libraries(icirv-gui PRIVATE icirv_core Qt6::Widgets)
