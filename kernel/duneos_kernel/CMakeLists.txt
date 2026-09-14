# During the ESP-IDF requirements phase the top-level CMakeLists' variables
# are not in scope, so DUNEOS_BOARD set at the project level is invisible
# here. Read .duneos_board directly — same source of truth in both phases.
if(NOT DEFINED DUNEOS_BOARD)
    if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/../../.duneos_board")
        file(READ "${CMAKE_CURRENT_LIST_DIR}/../../.duneos_board" DUNEOS_BOARD)
        string(STRIP "${DUNEOS_BOARD}" DUNEOS_BOARD)
    else()
        set(DUNEOS_BOARD "esp32s3-devkitc")
    endif()
endif()

# CMAKE_SOURCE_DIR is unreliable during the requirements phase — it may point
# to the build directory. Navigate from the component's own directory instead.
set(DUNEOS_BOARD_CONFIG_DIR "${CMAKE_CURRENT_LIST_DIR}/../../boards/${DUNEOS_BOARD}")
set(DUNEOS_ROOT             "${CMAKE_CURRENT_LIST_DIR}/../..")

if(NOT EXISTS "${DUNEOS_BOARD_CONFIG_DIR}/board_config.h")
    message(FATAL_ERROR
        "No board_config.h found for board '${DUNEOS_BOARD}'.\n"
        "Expected: ${DUNEOS_BOARD_CONFIG_DIR}/board_config.h\n"
        "Run: python tools/duneos-bspgen.py boards/${DUNEOS_BOARD}.yaml\n"
        "Or create it manually from an existing board template."
    )
endif()

# --- Core sources (always compiled) ----------------------------------------
set(DUNEOS_KERNEL_SRCS
    "src/klog.c"
    "src/task.c"
    "src/vfs.c"
    "src/vfs_tmp.c"
    "src/vfs_dev.c"
    "src/symbols.c"
    "src/supervisor.c"
    "src/init.c"
    "src/api.c"
    "src/drivers/drv_null.c"
    "src/drivers/drv_uart.c"
    "src/drivers/drv_klog.c"
    "src/drivers/drv_shellpipe.c"
)

# --- REQUIRES ---------------------------------------------------------------
# Grouped by removal phase to make the ESP-IDF decoupling roadmap visible.
#
# NOTE: CONFIG_DUNEOS_DRV_* Kconfig values are NOT available during the
# component-graph resolution phase in ESP-IDF.  SRCS are conditional on them,
# but REQUIRES entries for most platform deps must stay unconditional.
# Exception: CONFIG_IDF_TARGET_ARCH_* IS available (derived from IDF_TARGET
# before CMake runs), so arch-specific HAL deps can be gated on it.

# ── Phase 27 (OSAL): replace with duneos_osal.h + freertos_osal.c ──────────
# supervisor.c, task.c, symbols.c, klog.c, api.c, drv_input.c, input backends,
# drv_fb_st7789.c, drv_usb_cdc.c
# esp_ringbuf was here for a klog ring buffer that no longer exists: the tree
# has no Ringbuffer call left (drv_usb_cdc.c uses xStreamBuffer).
set(DUNEOS_KERNEL_REQUIRES
    freertos            # src/klog.c:23
    pthread             # src/api.c:22
    esp_system          # src/api.c:29
    esp_libc            # src/klog.c:79 (vsnprintf)
)

# ── Phase 26 (VFS natif): replace esp_vfs with duneos_vfs ──────────────────
# vfs.c, vfs_dev.c, vfs_tmp.c
list(APPEND DUNEOS_KERNEL_REQUIRES
    fatfs                # src/vfs.c:4
    vfs                  # src/vfs_tmp.c:13
    joltwallet__littlefs # src/vfs.c:5
)

# ── Phase 26 (hal_net.h): replace with arch/*/hal/hal_net.c ────────────────
# drv_wifi.c, drv_raw80211.c
list(APPEND DUNEOS_KERNEL_REQUIRES
    nvs_flash           # src/drivers/net/drv_wifi.c:26
    esp_wifi            # src/drivers/net/drv_wifi.c:22
    esp_netif           # src/drivers/net/drv_wifi.c:23
    esp_event           # src/drivers/net/drv_wifi.c:24
)

# ── TinyUSB: ESP32-S2/S3/P4 only (no USB OTG on plain ESP32/C3/H2) ─────────
# The one REQUIRES append in this tree that is allowed to sit under a
# CONFIG_DUNEOS_* guard, and the test that forbids the pattern names it
# explicitly: idf_component.yml gates espressif/esp_tinyusb on the target, so
# on esp32/esp32c3 the component does not exist and an unconditional REQUIRES
# is a hard configure error. Its headers are only reached from sources guarded
# by the same two symbols.
if(CONFIG_DUNEOS_DRV_USB_MSC OR CONFIG_DUNEOS_DRV_USB_CDC)
    list(APPEND DUNEOS_KERNEL_REQUIRES espressif__esp_tinyusb) # src/drivers/usb/drv_usb.c:28
endif()

# ESP-IDF's requirements phase re-runs this file through `cmake -P`, where
# CONFIGURE_DEPENDS is a hard error ("invalid for script and find package
# modes") — and where there is no build system to re-generate, so it would buy
# nothing. Carry it on the real configure pass only.
if(CMAKE_SCRIPT_MODE_FILE)
    set(_DUNEOS_GLOB_DEPENDS "")
else()
    set(_DUNEOS_GLOB_DEPENDS CONFIGURE_DEPENDS)
endif()

# --- Arch discovery ----------------------------------------------------------
# Each arch/*/arch.cmake self-selects via CONFIG_IDF_TARGET_ARCH_* and appends
# its HAL sources + ESP-IDF component deps to DUNEOS_KERNEL_SRCS/REQUIRES.
# Adding a new architecture = creating arch/<new_arch>/arch.cmake only.
# Zero changes to this file.
file(GLOB _DUNEOS_ARCH_MAKEFILES ${_DUNEOS_GLOB_DEPENDS} "${DUNEOS_ROOT}/arch/*/arch.cmake")
foreach(_ARCH_CMAKE ${_DUNEOS_ARCH_MAKEFILES})
    include("${_ARCH_CMAKE}")
endforeach()
unset(_DUNEOS_ARCH_MAKEFILES)
unset(_ARCH_CMAKE)

# --- Conditional driver sources --------------------------------------------
# Each driver compiled only when CONFIG_DUNEOS_DRV_*=y is set in the board's
# sdkconfig.defaults (generated by duneos-bspgen or edited manually).

if(CONFIG_DUNEOS_DRV_GPIO)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/gpio/drv_gpio.c")
endif()

if(CONFIG_DUNEOS_DRV_GPIOCHIP_SX1509)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/gpio/drv_gpiochip_sx1509.c")
endif()

if(CONFIG_DUNEOS_DRV_GPIOCHIP_PCF8574)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/gpio/drv_gpiochip_pcf8574.c")
endif()

if(CONFIG_DUNEOS_DRV_GPIOCHIP_MCP23017)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/gpio/drv_gpiochip_mcp23017.c")
endif()

if(CONFIG_DUNEOS_DRV_LOGIC)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/logic/drv_logic.c")
endif()

if(CONFIG_DUNEOS_DRV_I2C)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/i2c_bus.c")
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/drv_i2c.c")
endif()

if(CONFIG_DUNEOS_DRV_BATTERY_ADC_SIMPLE)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/battery/drv_battery_adc_simple.c")
endif()

if(CONFIG_DUNEOS_DRV_SPI)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/drv_spi.c")
endif()

if(CONFIG_DUNEOS_DRV_INPUT)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/input/drv_input.c")
endif()

if(CONFIG_DUNEOS_DRV_INPUT_ENCODER)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/input/enc_quadrature.c")
    # GPIO polling fallback (weak symbols); overridden by arch hal_encoder.c when available.
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/input/hal_encoder_fallback.c")
endif()

# st7789_hw.c is the shared low-level ST7789 hardware module. Kept only
# for drv_fb_st7789.c (PSRAM back-buffer / /dev/fb0). Non-PSRAM boards
# drive the display from userspace libst7789 via /dev/spi-N — no kernel
# driver involved.
if(CONFIG_DUNEOS_DRV_FB)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/display/st7789_hw.c")
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/display/drv_fb_st7789.c")
endif()

if(CONFIG_DUNEOS_DRV_ETH)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/net/drv_eth.c")
endif()

if(CONFIG_DUNEOS_DRV_WIFI)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/net/drv_wifi.c")
endif()

if(CONFIG_DUNEOS_DRV_RAW80211)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/net/drv_raw80211.c")
endif()

# drv_usb.c is always compiled when either USB class is enabled (it owns the
# TinyUSB driver install and descriptor tables).
if(CONFIG_DUNEOS_DRV_USB_MSC OR CONFIG_DUNEOS_DRV_USB_CDC)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/usb/drv_usb.c")
endif()

if(CONFIG_DUNEOS_DRV_USB_CDC)
    list(APPEND DUNEOS_KERNEL_SRCS "src/drivers/usb/drv_usb_cdc.c")
endif()

# ---------------------------------------------------------------------------
# First-boot provisioning blobs (optional).
# Place pre-built .dap files under firmware/blobs/ to embed them in the
# firmware image.  At first boot, vfs.c copies any missing blob to /flash/bin/.
# ---------------------------------------------------------------------------

file(GLOB DUNEOS_FIRMWARE_BLOBS ${_DUNEOS_GLOB_DEPENDS} "${CMAKE_SOURCE_DIR}/firmware/blobs/*.dap")

# Always generate blobs_gen.c; table is empty when no blobs are present.
set(BLOBS_GEN_C "${CMAKE_CURRENT_BINARY_DIR}/blobs_gen.c")
set(_BLOBS_EXTERN "")
set(_BLOBS_ENTRIES "")
foreach(BLOB_PATH ${DUNEOS_FIRMWARE_BLOBS})
    get_filename_component(BLOB_FNAME "${BLOB_PATH}" NAME)
    string(MAKE_C_IDENTIFIER "${BLOB_FNAME}" BLOB_SYM)
    string(APPEND _BLOBS_EXTERN
        "extern const uint8_t _binary_${BLOB_SYM}_start[];\n"
        "extern const uint8_t _binary_${BLOB_SYM}_end[];\n")
    string(APPEND _BLOBS_ENTRIES
        "    { \"${BLOB_FNAME}\", _binary_${BLOB_SYM}_start, (size_t)(_binary_${BLOB_SYM}_end - _binary_${BLOB_SYM}_start) },\n")
endforeach()
file(WRITE "${BLOBS_GEN_C}"
    "#include \"duneos/vfs.h\"\n\n"
    "${_BLOBS_EXTERN}\n"
    "const duneos_blob_entry_t g_duneos_blobs[] = {\n"
    "${_BLOBS_ENTRIES}"
    "};\n"
    "const int g_duneos_blob_count = (int)(sizeof(g_duneos_blobs) / sizeof(g_duneos_blobs[0]));\n"
)
list(APPEND DUNEOS_KERNEL_SRCS "${BLOBS_GEN_C}")

# ---------------------------------------------------------------------------

idf_component_register(
    SRCS
        ${DUNEOS_KERNEL_SRCS}
    INCLUDE_DIRS
        "include"
        "${DUNEOS_BOARD_CONFIG_DIR}"
    PRIV_INCLUDE_DIRS
        # Private headers shared between driver files (e.g. i2c_bus.h, drv_usb_cdc_priv.h).
        "src/drivers"
        "src/drivers/usb"
    REQUIRES
        ${DUNEOS_KERNEL_REQUIRES}
    EMBED_FILES
        ${DUNEOS_FIRMWARE_BLOBS}
    # Phase 24.9: WHOLE_ARCHIVE forces every kernel .o to be linked,
    # even ones whose only contribution is a GCC constructor from
    # DUNEOS_DRIVER_REGISTER. Without this, drivers whose register
    # function isn't externally referenced (because vfs_dev.c no longer
    # imports them by name) would be skipped from the static archive.
    WHOLE_ARCHIVE
)

# ieee80211_raw_frame_sanity_check in drv_raw80211.c shadows the symbol from
# the closed-source WiFi library — requires muldefs to resolve the conflict.
if(CONFIG_DUNEOS_DRV_RAW80211)
    target_link_libraries(${COMPONENT_LIB} INTERFACE "-Wl,-zmuldefs")
endif()
