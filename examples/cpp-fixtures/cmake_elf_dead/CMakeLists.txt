cmake_minimum_required(VERSION 3.16)
project(ici_cmake_elf_dead LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(elf_dead src/main.cpp src/reachability.cpp)
