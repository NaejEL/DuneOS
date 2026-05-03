idf_component_register(
    SRCS
        "src/task.c"
        "src/vfs.c"
        "src/symbols.c"
    INCLUDE_DIRS "include"
    REQUIRES freertos esp_driver_sdmmc fatfs esp_vfs
)
