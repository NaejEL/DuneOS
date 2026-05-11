#pragma once

#include <stdint.h>

/*
 * DuneOS battery device interface — /dev/battery0
 *
 * Usage:
 *   int fd = open("/dev/battery0", O_RDONLY);
 *   battery_info_t info;
 *   ioctl(fd, BATTERY_GET_INFO, &info);
 *   close(fd);
 *
 * The device is only present when the board BSP declares a battery section.
 * Backend is selected at build time from board_config.h (adc_simple, ip5306, …).
 */

#define BATTERY_GET_INFO  0x01  /* arg: battery_info_t* */

#define BATTERY_DISCHARGING  0
#define BATTERY_CHARGING     1
#define BATTERY_FULL         2

typedef struct {
    uint16_t voltage_mv;   /* raw cell voltage in millivolts */
    uint8_t  percent;      /* 0–100, estimated from linear discharge curve */
    uint8_t  status;       /* BATTERY_DISCHARGING | CHARGING | FULL */
} battery_info_t;
