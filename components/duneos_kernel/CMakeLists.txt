if(NOT DEFINED DUNEOS_BOARD)
    set(DUNEOS_BOARD "esp32s3-devkitc")
endif()

# CMAKE_SOURCE_DIR is unreliable during the requirements phase — it may point
# to the build directory. Navigate from the component's own directory instead.
set(DUNEOS_BOARD_CONFIG_DIR "${CMAKE_CURRENT_LIST_DIR}/../../boards/${DUNEOS_BOARD}")

if(NOT EXISTS "${DUNEOS_BOARD_CONFIG_DIR}/board_config.h")
    message(FATAL_ERROR
        "No board_config.h found for board '${DUNEOS_BOARD}'.\n"
        "Expected: ${DUNEOS_BOARD_CONFIG_DIR}/board_config.h\n"
        "Run: duneos-bspgen boards/${DUNEOS_BOARD}.yaml  (Phase 4)\n"
        "Or create it manually from an existing board template."
    )
endif()

idf_component_register(
    SRCS
        "src/klog.c"
        "src/task.c"
        "src/vfs.c"
        "src/vfs_tmp.c"
        "src/vfs_dev.c"
        "src/symbols.c"
        "src/supervisor.c"
    INCLUDE_DIRS
        "include"
        "${DUNEOS_BOARD_CONFIG_DIR}"
    REQUIRES
        freertos
        pthread
        driver
        esp_driver_gpio
        esp_driver_sdspi
        esp_driver_uart
        esp_system
        fatfs
        vfs
        newlib
        json
)
