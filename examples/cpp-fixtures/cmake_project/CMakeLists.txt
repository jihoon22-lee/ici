cmake_minimum_required(VERSION 3.16)
project(cmake_fixture LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Core Test)

add_library(counter STATIC src/counter.cpp)
target_include_directories(counter PUBLIC src)
target_link_libraries(counter PUBLIC Qt6::Core)

enable_testing()

add_executable(test_counter tests/test_counter.cpp)
target_link_libraries(test_counter PRIVATE counter Qt6::Test)
# Always the project root, matching the contract fixed in C-9: a test that opens
# a fixture by relative path must not depend on who launched it.
add_test(NAME test_counter COMMAND test_counter WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
