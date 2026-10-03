cmake_minimum_required(VERSION 3.16)
project(quality_zoo_qt_missing_parent LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Qt6 REQUIRED COMPONENTS Core)

add_library(qt_lifetime_fixture OBJECT src/bad.cpp src/clean.cpp)
target_link_libraries(qt_lifetime_fixture PRIVATE Qt6::Core)
