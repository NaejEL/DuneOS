#include "st7789_hw.h"
#include "duneos/klog.h"
#include "board_config.h"

#include "driver/spi_master.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <string.h>
#include <stdbool.h>

static const char *TAG = "duneos/st7789_hw";

/* ST7789 command bytes */
#define ST7789_SWRESET  0x01
#define ST7789_SLPOUT   0x11
#define ST7789_NORON    0x13
#define ST7789_INVON    0x21
#define ST7789_DISPON   0x29
#define ST7789_CASET    0x2A
#define ST7789_RASET    0x2B
#define ST7789_RAMWR    0x2C
#define ST7789_MADCTL   0x36
#define ST7789_COLMOD   0x3A

static spi_device_handle_t s_spi       = NULL;
static bool                s_init_done = false;

/* DMA-safe line buffer for clear operations (static = internal DRAM) */
static uint16_t s_clear_line[320];

esp_err_t st7789_hw_init(void)
{
    if (s_init_done) return ESP_OK;

    /* Configure output GPIOs: DC (mandatory), RST + BL (optional) */
    uint64_t pin_mask = (1ULL << DUNEOS_DISPLAY_DC_PIN);
#if DUNEOS_DISPLAY_RST_PIN >= 0
    pin_mask |= (1ULL << DUNEOS_DISPLAY_RST_PIN);
#endif
#if DUNEOS_DISPLAY_BL_PIN >= 0
    pin_mask |= (1ULL << DUNEOS_DISPLAY_BL_PIN);
#endif
    gpio_config_t gcfg = {
        .pin_bit_mask = pin_mask,
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&gcfg);

#if !DUNEOS_DISPLAY_BUS_SHARED
    spi_bus_config_t bus = {
        .mosi_io_num     = DUNEOS_DISPLAY_MOSI_PIN,
        .miso_io_num     = -1,
        .sclk_io_num     = DUNEOS_DISPLAY_CLK_PIN,
        .quadwp_io_num   = -1,
        .quadhd_io_num   = -1,
        .max_transfer_sz = 4096,
    };
    esp_err_t berr = spi_bus_initialize(DUNEOS_DISPLAY_SPI_HOST, &bus, SPI_DMA_CH_AUTO);
    if (berr != ESP_OK && berr != ESP_ERR_INVALID_STATE) {
        klog_e(TAG, "spi_bus_initialize: %s", esp_err_to_name(berr));
        return berr;
    }
#endif

    spi_device_interface_config_t dev = {
        .mode           = 0,
        .clock_speed_hz = DUNEOS_DISPLAY_FREQ_HZ,
        .spics_io_num   = DUNEOS_DISPLAY_CS_PIN,
        .queue_size     = 1,
    };
    esp_err_t err = spi_bus_add_device(DUNEOS_DISPLAY_SPI_HOST, &dev, &s_spi);
    if (err != ESP_OK) {
        klog_e(TAG, "spi_bus_add_device: %s", esp_err_to_name(err));
        return err;
    }

#if DUNEOS_DISPLAY_RST_PIN >= 0
    gpio_set_level(DUNEOS_DISPLAY_RST_PIN, 0);
    vTaskDelay(pdMS_TO_TICKS(10));
    gpio_set_level(DUNEOS_DISPLAY_RST_PIN, 1);
    vTaskDelay(pdMS_TO_TICKS(120));
#endif

    st7789_hw_cmd(ST7789_SWRESET); vTaskDelay(pdMS_TO_TICKS(120));
    st7789_hw_cmd(ST7789_SLPOUT);  vTaskDelay(pdMS_TO_TICKS(120));
    st7789_hw_cmd(ST7789_COLMOD);
    uint8_t colmod = 0x55;
    st7789_hw_data(&colmod, 1);
    st7789_hw_cmd(ST7789_MADCTL);
    uint8_t madctl = DUNEOS_DISPLAY_MADCTL;
    st7789_hw_data(&madctl, 1);
    st7789_hw_cmd(ST7789_INVON);
    st7789_hw_cmd(ST7789_NORON);  vTaskDelay(pdMS_TO_TICKS(10));
    st7789_hw_cmd(ST7789_DISPON); vTaskDelay(pdMS_TO_TICKS(10));

#if DUNEOS_DISPLAY_BL_PIN >= 0
    gpio_set_level(DUNEOS_DISPLAY_BL_PIN, 1);
#endif

    s_init_done = true;
    klog_i(TAG, "ST7789 %ux%u madctl=0x%02x swap_xy=%d col_off=%u row_off=%u ready",
           DUNEOS_DISPLAY_WIDTH, DUNEOS_DISPLAY_HEIGHT,
           DUNEOS_DISPLAY_MADCTL, DUNEOS_DISPLAY_SWAP_XY,
           DUNEOS_DISPLAY_COL_OFFSET, DUNEOS_DISPLAY_ROW_OFFSET);

    /* Clear the active display area to black on every boot so there is no
     * residual content from a previous run. */
    st7789_hw_clear(0x0000);

    return ESP_OK;
}

void st7789_hw_clear(uint16_t color_be)
{
    for (int i = 0; i < DUNEOS_DISPLAY_WIDTH; i++) s_clear_line[i] = color_be;
    st7789_hw_set_window(0, 0, DUNEOS_DISPLAY_WIDTH, DUNEOS_DISPLAY_HEIGHT);
    for (int row = 0; row < DUNEOS_DISPLAY_HEIGHT; row++)
        st7789_hw_data(s_clear_line, DUNEOS_DISPLAY_WIDTH * 2);
}

void st7789_hw_cmd(uint8_t c)
{
    gpio_set_level(DUNEOS_DISPLAY_DC_PIN, 0);
    spi_transaction_t t = { .length = 8, .flags = SPI_TRANS_USE_TXDATA, .tx_data = { c } };
    spi_device_transmit(s_spi, &t);
}

void st7789_hw_data(const void *buf, size_t len)
{
    if (!len) return;
    gpio_set_level(DUNEOS_DISPLAY_DC_PIN, 1);
    spi_transaction_t t = { .length = len * 8, .tx_buffer = buf };
    spi_device_transmit(s_spi, &t);
}

void st7789_hw_set_window(uint16_t x, uint16_t y, uint16_t w, uint16_t h)
{
    uint8_t buf[4];

    /* DUNEOS_DISPLAY_SWAP_XY=1 when MADCTL has MV set (landscape rotations).
     * With MV=1 the CASET register addresses the row space (wider range, maps to
     * display X) and RASET addresses the column space (narrower, maps to display Y).
     * The portrait offsets therefore cross over: ROW_OFFSET goes to CASET and
     * COL_OFFSET goes to RASET. */
#if DUNEOS_DISPLAY_SWAP_XY
    uint16_t cs = x + DUNEOS_DISPLAY_ROW_OFFSET, ce = cs + w - 1;
    uint16_t rs = y + DUNEOS_DISPLAY_COL_OFFSET, re = rs + h - 1;
#else
    uint16_t cs = x + DUNEOS_DISPLAY_COL_OFFSET, ce = cs + w - 1;
    uint16_t rs = y + DUNEOS_DISPLAY_ROW_OFFSET, re = rs + h - 1;
#endif

    buf[0] = cs >> 8; buf[1] = cs & 0xFF;
    buf[2] = ce >> 8; buf[3] = ce & 0xFF;
    st7789_hw_cmd(ST7789_CASET);
    st7789_hw_data(buf, 4);

    buf[0] = rs >> 8; buf[1] = rs & 0xFF;
    buf[2] = re >> 8; buf[3] = re & 0xFF;
    st7789_hw_cmd(ST7789_RASET);
    st7789_hw_data(buf, 4);

    st7789_hw_cmd(ST7789_RAMWR);
}

uint16_t st7789_hw_width(void)    { return DUNEOS_DISPLAY_WIDTH; }
uint16_t st7789_hw_height(void)   { return DUNEOS_DISPLAY_HEIGHT; }
uint8_t  st7789_hw_rotation(void) { return DUNEOS_DISPLAY_ROTATION; }
