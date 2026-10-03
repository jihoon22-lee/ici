cmake_minimum_required(VERSION 3.16)
project(qz_static_context LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(v17 OBJECT src/target17.cpp)
target_compile_features(v17 PRIVATE cxx_std_17)
target_compile_definitions(v17 PRIVATE QZ_CXX17=1)
target_include_directories(v17 PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include/first"
    "${CMAKE_CURRENT_SOURCE_DIR}/include/second"
)

add_library(v20 OBJECT src/target20.cpp)
target_compile_features(v20 PRIVATE cxx_std_20)
target_compile_definitions(v20 PRIVATE QZ_CXX20=1)
target_include_directories(v20 PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include/second"
    "${CMAKE_CURRENT_SOURCE_DIR}/include/first"
)

add_library(warnings OBJECT src/warning.cpp)
add_library(errors OBJECT src/compile_error_user.cpp)
add_library(unresolved EXCLUDE_FROM_ALL OBJECT src/unresolved.cpp)
