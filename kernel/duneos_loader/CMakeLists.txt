get_filename_component(DUNEOS_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)

idf_component_register(
    SRCS "src/loader.c"
          "src/elf_parse.c"
          "src/manifest_depth.c"
          "${DUNEOS_ROOT}/third_party/cjson/cJSON.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
                      "${DUNEOS_ROOT}/third_party/cjson"
    REQUIRES duneos_kernel heap
)

# LEG-41, second line of defence only — the primary bound is the pre-parse scan
# in src/manifest_depth.c. Set here rather than in third_party/cjson/cJSON.h so
# a submodule update cannot drop it. 16 levels is 416 + 64*16 = 1440 B on the
# manifest chain: 768 B above the 672 B fixed-depth maximum of the load path,
# still inside main_task's 784 B worst-case measured margin.
target_compile_definitions(${COMPONENT_LIB} PRIVATE CJSON_NESTING_LIMIT=16)
