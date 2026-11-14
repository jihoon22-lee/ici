cmake_minimum_required(VERSION 3.16)
project(unity_cache CXX)
# A project that turns unity builds on through its cache: frozen's release analysis database
# overrides it with -DCMAKE_UNITY_BUILD=OFF, so every source keeps its own compile command.
set(CMAKE_UNITY_BUILD ON CACHE BOOL "unity")
add_executable(app src/a.cpp src/b.cpp)
