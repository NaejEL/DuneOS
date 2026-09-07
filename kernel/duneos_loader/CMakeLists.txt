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
# a submodule update cannot drop it.
#
# 16 is two orders below cJSON's stock 1000, and is reached only if a call site
# ever parses a manifest without the gate. It is NOT a budgeted chain: at 16
# levels the descent is 416 + 64*16 = 1440 B, i.e. 768 B above the 672 B
# fixed-depth maximum of the load path, and manifest_depth.h says why nothing
# should be spent above that maximum at all — parse_string's callees, and
# cJSON's per-item allocations from inside the descent, are counted in neither
# figure. So this is a net against a bug, not a second budget; the acceptance
# criteria rest on the gate.
target_compile_definitions(${COMPONENT_LIB} PRIVATE CJSON_NESTING_LIMIT=16)
