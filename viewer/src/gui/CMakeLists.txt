cmake_minimum_required(VERSION 3.16)
project(icirv_gui LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Widgets)

# The core is Qt-free so it can be unit-tested and linked anywhere; the GUI adds
# Qt on top. Both live under src/ and both are analysed by ici — only the link
# step differs, which is what project.cpp_external_build_dirs declares.
add_library(icirv_core STATIC
    ../json_parser.cpp
    ../json_value.cpp
    ../report_model.cpp
    ../summary.cpp
)
target_include_directories(icirv_core PUBLIC ../../include)

add_executable(icirv-gui
    gui_main.cpp
    engine_tree_model.cpp
    main_window.cpp
)
target_link_libraries(icirv-gui PRIVATE icirv_core Qt6::Widgets)
