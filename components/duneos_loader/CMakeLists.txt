idf_component_register(
    SRCS "src/loader.c"
    INCLUDE_DIRS "include"
    REQUIRES duneos_kernel esp_heap_caps
)
