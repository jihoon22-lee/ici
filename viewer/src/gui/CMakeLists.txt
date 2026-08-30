cmake_minimum_required(VERSION 3.16)
project(icirv_gui LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Widgets)

# A standalone project on purpose: this is the only part that needs Qt, and
# keeping it apart is what lets the root project link the CLI statically and
# ship it to a machine with no Qt at all. The root builds its own icirv_core;
# these are separate CMake projects and never meet.
add_library(icirv_core STATIC
    ../json_parser.cpp
    ../json_value.cpp
    ../report_model.cpp
    ../summary.cpp
)
target_include_directories(icirv_core PUBLIC ../../include)

# The headers are listed explicitly because they sit under include/ rather than
# beside their .cpp. AUTOMOC finds a Q_OBJECT header on its own only when it
# shares a directory and a basename with a source in the target; anywhere else
# it has to be named here or moc never runs on it.
add_executable(icirv-gui
    gui_main.cpp
    engine_tree_model.cpp
    main_window.cpp
    ../../include/icirv/gui/engine_tree_model.hpp
    ../../include/icirv/gui/main_window.hpp
)
target_link_libraries(icirv-gui PRIVATE icirv_core Qt6::Widgets)
