cmake_minimum_required(VERSION 3.16)

# Must be set before include(project.cmake) — cannot be changed after.
# Override with: idf.py set-target <target>
if(NOT DEFINED IDF_TARGET)
    set(IDF_TARGET esp32s3)
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(duneos)
