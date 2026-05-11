cmake_minimum_required(VERSION 3.16)

if(NOT DEFINED DUNEOS_BOARD)
    if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/.duneos_board")
        file(READ "${CMAKE_CURRENT_LIST_DIR}/.duneos_board" DUNEOS_BOARD)
        string(STRIP "${DUNEOS_BOARD}" DUNEOS_BOARD)
    else()
        set(DUNEOS_BOARD "esp32s3-devkitc")
    endif()
endif()

# SDKCONFIG_DEFAULTS must be set before include(project.cmake).
# Common settings first, then board-specific overrides.
set(SDKCONFIG_DEFAULTS
    "sdkconfig.defaults"
    "boards/${DUNEOS_BOARD}/sdkconfig.defaults"
)

# IDF_TARGET must also be set before include(project.cmake).
if(NOT DEFINED IDF_TARGET)
    set(IDF_TARGET esp32s3)
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(duneos)
