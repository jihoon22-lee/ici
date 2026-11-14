cmake_minimum_required(VERSION 3.16)
project(linker_target_limit LANGUAGES CXX)
add_executable(app src/main.cpp src/util.cpp)
# 256 more executables CMake generates a link.txt for but never builds: 257 link targets.
foreach(index RANGE 1 256)
  add_executable(extra_${index} EXCLUDE_FROM_ALL src/main.cpp src/util.cpp)
endforeach()
