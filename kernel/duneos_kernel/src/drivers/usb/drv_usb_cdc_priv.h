#pragma once
#include "tinyusb_cdc_acm.h"

/* Called by drv_usb.c before tinyusb_driver_install() */
void drv_usb_cdc_preinit(void);

/* Redirect esp_log output to CDC — call after tinyusb_cdcacm_init() */
void drv_usb_cdc_attach_log(void);

/* TinyUSB CDC callbacks — wired by drv_usb.c */
void drv_usb_cdc_rx_cb(int itf, cdcacm_event_t *event);
void drv_usb_cdc_line_state_cb(int itf, cdcacm_event_t *event);
