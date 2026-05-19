# ADR 013 — Network architecture: POSIX sockets + vendored lwIP + per-medium HAL

**Status:** Accepted · 2026-05-19

## Context

DuneOS exposes BSD sockets (Phase 14) over the lwIP stack that ESP-IDF provides. This works on a single SDK; it does not scale to the targets the roadmap commits to:

- **ESP32-S3 / C6 (Phase 28)** — Xtensa or RISC-V, both under ESP-IDF with esp_wifi + esp_netif + bundled lwIP. Different ISAs, same SDK plumbing.
- **RP2040 / Pico W (Phase 29)** — CYW43439 WiFi chip on SPI, driven by `cyw43-driver`. pico-sdk ships its own lwIP, wired through `pico_cyw43_arch_lwip_*`.
- **STM32H7 + LAN8720 RMII (future)** — MAC integrated in the SoC + external PHY over MDIO. STM32Cube ships HAL but no TCP/IP stack.
- **Linux simulator (Phase 26)** — pthread + PicoLibc; needs sockets to work for host-side tests.

Each platform brings its own stack and its own driver-to-stack wiring. Without a designed boundary, every port reinvents the lwIP integration, the WiFi state machine, the netif registry — and apps would silently see different socket behaviour across platforms.

The Linux pattern (`socket()` → BSD layer → TCP/IP stack → `struct net_device` registry → driver) is the proven model. We adopt it, but with two MCU-specific adjustments:

1. The control plane (associate to SSID, get link speed, scan APs) is too medium-specific to fit one HAL — Linux uses `ioctl(SIOCSIWFREQ)` + netlink, which is a mess. We split by medium instead.
2. We use lwIP's `struct netif` directly as the registry primitive rather than wrapping it. Adding a `duneos_netif_t` wrapper buys nothing while lwIP is the only stack we target.

## Decision

**Three layers, two HALs, one vendored stack.**

```
Apps                    POSIX sockets (libdune wrappers, existing)
  │
BSD socket layer        socket(), connect(), poll(), select() (Phase 27)
  │
TCP/IP stack            lwIP — vendored in third_party/lwip in Phase 27
  │
struct netif            lwIP's native interface registry — used directly
  │
Link layer drivers      kernel/duneos_kernel/src/drivers/net/
                          wifi_esp.c, wifi_cyw43.c, eth_rmii.c, …
  │
HAL (control plane)     kernel/duneos_kernel/include/duneos/hal_wifi.h
                                                       /hal_eth.h
                                                       /hal_phy.h
  │
Per-arch backend        arch/<arch>/hal/hal_wifi.c, hal_eth.c, …
```

**`hal_wifi.h`** — control plane only, pure C (no esp_wifi types, no cyw43 types):

```c
int  hal_wifi_init(const hal_wifi_config_t *cfg);
int  hal_wifi_scan(hal_wifi_ap_info_t *out, size_t max, int *count);
int  hal_wifi_connect_sta(const char *ssid, const char *psk);
int  hal_wifi_disconnect_sta(void);
int  hal_wifi_start_ap(const char *ssid, const char *psk, uint8_t channel);
int  hal_wifi_get_rssi(int8_t *out);
void hal_wifi_set_event_cb(void (*cb)(int event, void *ctx), void *ctx);
```

The kernel driver `wifi_esp.c` / `wifi_cyw43.c` calls these. The HAL implementation (`arch/<arch>/hal/hal_wifi.c`) wraps esp_wifi on ESP32, cyw43_driver on RP2040.

**`hal_eth.h`** — Ethernet MAC control, plus a separate **`hal_phy.h`** for MDIO PHY abstraction:

```c
// hal_eth.h — MAC control
int  hal_eth_init(const hal_eth_config_t *cfg);
int  hal_eth_set_link_state(bool up);
int  hal_eth_get_stats(hal_eth_stats_t *out);
void hal_eth_set_rx_cb(void (*cb)(const uint8_t *data, size_t len, void *ctx), void *ctx);
int  hal_eth_tx(const uint8_t *data, size_t len);

// hal_phy.h — MDIO PHY abstraction (LAN8720, KSZ8081, RTL8201, …)
int  hal_phy_read(uint8_t phy_addr, uint8_t reg, uint16_t *out);
int  hal_phy_write(uint8_t phy_addr, uint8_t reg, uint16_t value);
int  hal_phy_get_link(uint8_t phy_addr, hal_phy_link_t *out);   // up/down, speed, duplex
```

Each PHY chip has a small file (e.g. `arch/<arch>/hal/phy/lan8720.c`, ~50 lines) implementing the standard MII registers + chip-specific quirks. The MAC driver doesn't know which PHY it talks to — it goes through MDIO.

**Link layer driver** registers a `struct netif` (lwIP-native) and bridges:

```c
// kernel/duneos_kernel/src/drivers/net/wifi_esp.c
static struct netif s_wlan0_netif;

static err_t wlan0_linkoutput(struct netif *nif, struct pbuf *p) {
    // Called by lwIP to send a packet; hand off to hal_wifi internal tx
    return hal_wifi_tx_packet(p->payload, p->tot_len) == 0 ? ERR_OK : ERR_IF;
}

static void wlan0_rx_cb(const uint8_t *data, size_t len, void *ctx) {
    struct pbuf *p = pbuf_alloc(PBUF_RAW, len, PBUF_POOL);
    memcpy(p->payload, data, len);
    s_wlan0_netif.input(p, &s_wlan0_netif);  // hands to lwIP
}

void drv_wifi_register(void) {
    netif_add(&s_wlan0_netif, /*ip*/, /*nm*/, /*gw*/, NULL,
              wlan0_init_cb, tcpip_input);
    netif_set_default(&s_wlan0_netif);
    hal_wifi_set_event_cb(on_wifi_event, NULL);
}
```

Note: a richer `hal_wifi.h` would also expose `hal_wifi_tx_packet(buf, len)` and an RX callback the driver registers — kept brief here. The full surface lives in the header, not in this ADR.

**Loopback `lo`** is always created at kernel init (lwIP provides it natively). Useful for tests without hardware.

**Vendored lwIP** — `third_party/lwip` git submodule, compiled per target by the toolchain plugin. Decision binding from Phase 27. Until then, ESP-IDF's lwIP is fine on ESP32-S3 and the smoke test is not yet building anywhere else.

**Apps use BSD sockets unchanged.** Existing exports in `kernel/duneos_kernel/include/duneos/socket.h` stay. `getaddrinfo`, `gethostname` get added to libdune (lwIP provides them).

**`/dev/raw80211`** keeps working as today — it bypasses the IP stack entirely for 802.11 frame injection (security testing). Independent of this ADR.

**Network config API beyond sockets:**

```c
int duneos_netif_set_ip(const char *ifname, ip4_addr_t ip, ip4_addr_t nm, ip4_addr_t gw);
int duneos_netif_set_dns(const ip4_addr_t *dns_servers, int count);
int duneos_netif_get_status(const char *ifname, duneos_netif_status_t *out);
int duneos_netif_list(char names[][16], int max);
```

This replaces the Linux `ioctl(SIOCGIFFLAGS)` + netlink mess with a clean DuneOS API. `apps/system/bin/ifconfig` consumes it.

### board.yaml extension

```yaml
network:
  wifi:
    chip: esp32_wifi          # or cyw43439, or none
    sta: true
    ap: true
  ethernet:
    chip: rmii_lan8720        # or w5500_spi, or none
    mac_address: derived
    phy_addr: 1               # MDIO address (RMII only)
```

`bspgen` emits `CONFIG_DUNEOS_DRV_WIFI_ESP=y` / `CONFIG_DUNEOS_DRV_ETH_RMII=y` / etc. from these sections. The kernel driver compiled is selected by the Kconfig; the HAL backend by `arch.cmake`.

## Consequences

- Phase 27 (VFS native + networking) is now explicitly scoped: it vendors lwIP, creates `hal_wifi.h` / `hal_eth.h` / `hal_phy.h`, rewrites `drv_wifi.c` to register a `struct netif` directly, replaces `esp_netif` usage. The phase's TODO list gains concrete file targets.
- Phase 29 (RP2040) gains a clear contract: add `arch/arm_cortex_m/hal/rp2040/hal_wifi.c` wrapping cyw43_driver, plus the SPI/PIO bus setup. No kernel rewrite needed; `wifi_cyw43.c` driver looks essentially identical to `wifi_esp.c`, only the HAL changes.
- Apps remain unchanged across all ports — `socket(AF_INET, ...)` works everywhere. Phase 14 BSD socket exports stay valid.
- `esp_netif` (ESP-IDF wrapper around lwIP `struct netif`) is **removed** from the dependency surface in Phase 27. This reduces ESP-IDF coupling — one less component to abstract away.
- Loopback always available → tests can use `127.0.0.1` without networking hardware. Useful for [[ADR-012]] host-side tests.
- DHCP / SNTP / DNS clients (lwIP-provided) stay activated by `wifi_daemon`, no separate work needed.
- `duneos_netif_wait_ip()` (existing kernel symbol) gets reimplemented over `struct netif` flags + an OSAL semaphore. Same API, decoupled implementation.

### Out of scope for this ADR

Documented here for clarity, deferred to future ADRs when a real board needs them:

- **W5500 / ENC28J60 SPI Ethernet chips** — would live as a userspace lib in `sdk/network/` if used by one app, or as a kernel driver `eth_w5500.c` if shared. Decision postponed until a board ships with one.
- **LoRa-IP (6LoWPAN)**, **Thread**, **Zigbee**, **802.15.4** — when a stack is embedded (OpenThread, etc.), it registers a `struct netif` just like WiFi. Until then, those radios are datagram drivers like `/dev/raw80211`.
- **Bluetooth Classic / BLE** — not an IP network device; separate subsystem (Linux precedent: BlueZ is independent of the network stack). A future ADR will define the BLE GATT API; ADR 013 does not cover it.
- **VPN, packet filtering, firewalls, traffic shaping** — out of scope on MCUs of this size. Apps that need them link a userspace lib.

## Alternatives

- **Wrap `struct netif` in `duneos_netif_t`** — rejected. Adds a layer of indirection that buys nothing while lwIP is the only stack we target. If we ever swap stacks (PicoTCP, …), the abstraction is the wrong shape (PicoTCP doesn't have an `input` callback pattern); we'd rework it anyway.
- **PicoTCP instead of lwIP** — considered, rejected. lwIP has the larger community, better SDK integration on ESP-IDF and pico-sdk, more deployment evidence. PicoTCP is a viable alternative if lwIP becomes problematic, but vendoring two stacks today is yak-shaving.
- **Use `esp_netif` as the cross-platform netif abstraction** — rejected. It's ESP-IDF-specific by name and contract; using it on RP2040 means dragging an ESP-IDF type into pico-sdk land. lwIP's `struct netif` is the actually-portable primitive.
- **One unified `hal_net.h` covering both WiFi and Ethernet** — rejected. WiFi operations (scan, associate, RSSI) and Ethernet operations (link speed, MDIO PHY) don't overlap meaningfully. Forcing them into one HAL produces a wide interface where each backend implements 50% as `-ENOSYS`. The Linux precedent is similar — `iw` and `ethtool` are separate tools for a reason.
- **Linux `ioctl(SIOCSIWFREQ)` + netlink for interface config** — rejected. Notoriously over-engineered, hard to expose cleanly through the DuneOS ABI, and apps written against it would not be portable to non-Linux DuneOS targets.
