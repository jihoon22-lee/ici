cmake_minimum_required(VERSION 3.16)
project(no_ctest CXX)
# No enable_testing(): the configured shadow has no CTestTestfile.cmake.
add_executable(app src/main.cpp)
