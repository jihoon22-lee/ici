cmake_minimum_required(VERSION 3.16)
project(linker_broken LANGUAGES CXX)
message(FATAL_ERROR "this project intentionally fails to configure")
add_executable(app src/main.cpp)
