cmake_minimum_required(VERSION 3.16)
project(linker_unused LANGUAGES CXX)
add_executable(app src/main.cpp src/util.cpp)
add_custom_target(mark ALL COMMAND sh -c "echo ran >> ${CMAKE_SOURCE_DIR}/../custom-ran" VERBATIM)
