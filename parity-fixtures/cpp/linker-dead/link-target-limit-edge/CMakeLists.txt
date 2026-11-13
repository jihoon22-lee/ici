cmake_minimum_required(VERSION 3.16)
project(linker_target_limit_edge LANGUAGES CXX)
add_executable(app src/main.cpp src/util.cpp)
# 255 more executables CMake generates a link.txt for but never builds: 256 link targets, the bound itself.
foreach(index RANGE 1 255)
  add_executable(extra_${index} EXCLUDE_FROM_ALL src/main.cpp src/util.cpp)
endforeach()
