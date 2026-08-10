#pragma once

/*
 * Kernel-internal logging API.
 *
 * All kernel subsystems use klog_* instead of ESP_LOGx so that:
 *   1. Messages are captured in the in-RAM ring buffer, readable via /dev/klog
 *   2. The UART is kept clean for the shell (no interleaved log spam)
 *   3. Production builds can silence the kernel entirely with DUNEOS_KERNEL_SILENT
 *
 * Only errors (klog_e) are forwarded to ESP_LOGx — I/W/D stay in the ring
 * buffer only, readable via /dev/klog (cat /dev/klog in the shell).
 *
 * To silence: add CONFIG_DUNEOS_KERNEL_SILENT=y to sdkconfig.defaults.
 */

#include <stddef.h>

#ifdef CONFIG_DUNEOS_KERNEL_SILENT

#define klog_i(tag, fmt, ...)  do {} while (0)
#define klog_w(tag, fmt, ...)  do {} while (0)
#define klog_e(tag, fmt, ...)  do {} while (0)
#define klog_d(tag, fmt, ...)  do {} while (0)

#else

void klog_write(char level, const char *tag, const char *fmt, ...)
    __attribute__((format(printf, 3, 4)));

#define klog_i(tag, fmt, ...)  klog_write('I', tag, fmt, ##__VA_ARGS__)
#define klog_w(tag, fmt, ...)  klog_write('W', tag, fmt, ##__VA_ARGS__)
#define klog_e(tag, fmt, ...)  klog_write('E', tag, fmt, ##__VA_ARGS__)
#define klog_d(tag, fmt, ...)  klog_write('D', tag, fmt, ##__VA_ARGS__)

#endif /* CONFIG_DUNEOS_KERNEL_SILENT */

/*
 * Ring buffer access — used internally by vfs_dev.c to serve /dev/klog.
 * Not part of the kernel ABI exposed to apps.
 *
 * klog_ring_read: copy up to max_len bytes starting at *abs_pos into buf.
 *   Updates *abs_pos to the position after the last byte read.
 *   Returns number of bytes copied (0 = no new data / caught up).
 *
 * klog_ring_write_pos: current absolute write head (exclusive end of valid data).
 * klog_ring_oldest:    oldest absolute position still in the ring.
 */
size_t klog_ring_read(size_t *abs_pos, char *buf, size_t max_len);
size_t klog_ring_write_pos(void);
size_t klog_ring_oldest(void);

/* Route esp_rom_printf (panic/assert/abort output, normally lost on boards
 * with no UART0 header) into the klog ring. Call once at kernel init. */
void klog_capture_rom_output(void);
