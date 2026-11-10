cmake_minimum_required(VERSION 3.16)
project(linker_unused LANGUAGES CXX)
add_executable(app src/main.cpp src/util.cpp)
add_custom_target(mark ALL COMMAND ${CMAKE_COMMAND} -E touch "${CMAKE_SOURCE_DIR}/../custom-ran")
