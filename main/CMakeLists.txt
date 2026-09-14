idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES
        duneos_kernel                # main.c:16
        duneos_loader                # main.c:20
        esp_driver_usb_serial_jtag   # main.c:13
)
