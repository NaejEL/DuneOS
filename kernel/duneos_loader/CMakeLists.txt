get_filename_component(DUNEOS_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)

idf_component_register(
    SRCS "src/loader.c"
          "src/elf_parse.c"
          "${DUNEOS_ROOT}/third_party/cjson/cJSON.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
                      "${DUNEOS_ROOT}/third_party/cjson"
    REQUIRES duneos_kernel heap
)
