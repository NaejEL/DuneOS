# DuneOS Roadmap V2

## 1. Le Manifeste

NuttX et Zephyr offrent une conformité POSIX incroyable, mais au prix d'une courbe d'apprentissage brutale (enfer des Kconfig, Device Trees complexes). DuneOS vise le Sweet Spot : La puissance d'un RTOS POSIX avec l'expérience développeur (DX) fluide d'une console comme la Playdate ou le Flipper Zero.

L'utilisateur ne doit jamais toucher à CMake ou aux internals du noyau. Un simple fichier YAML et du code C doivent suffire pour compiler, lier et déployer une application dynamiquement sur n'importe quel matériel supporté.

---

## 2. Héritage du POC (Phases 1 à 24 validées, dette résiduelle adressée par 26-27)

DuneOS n'est plus une simple idée — les fondations sont éprouvées sur ESP32-S3 (M5Stack CardPuter, LilyGo T-Embed CC1101, ESP32-S3-DevKitC) :

- **Exécution et ABI** : Loader ELF (ET_REL) Xtensa fonctionnel (cycle load/run/unload, relocations Xtensa complètes). ABI v3 : table de dispatch typée `duneos_api_t` injectée dans `__duneos_api_ptr` par le loader — résolution O(1).
- **VFS et Périphériques** : `/flash` (LittleFS, sysbin), `/sd` (FatFS optionnelle), `/tmp` (tmpfs), `/dev/uart0`, `/dev/klog`, `/dev/gpiochip0`, `/dev/i2c-0`, `/dev/spi-1`, `/dev/disp0`, `/dev/fb0` (boards PSRAM), `/dev/input/event0`, `/dev/raw80211`, `/dev/ttyUSB0`, `/dev/battery0`.
- **Display** : `/dev/disp0` streaming driver (toutes boards) + `/dev/fb0` PSRAM back-buffer (T-Embed). `libgfx` (`sdk/display/gfx.c`) sélectionne dynamiquement le backend au runtime (`/dev/fb0` si dispo, fallback `/dev/disp0`).
- **USB** : TinyUSB composite MSC + CDC sur boards OTG ; `/dev/ttyUSB0` exposé via `drv_usb_cdc.c` ; klog redirigé vers CDC à l'exécution.
- **Userspace** : `libdune.a` (6 sources : ptr, fs, mem, thread, time, sys) — PicoLibc + dispatch ABI v3. Permet aux apps d'écrire du C POSIX standard.
- **Écosystème** : Shell modulaire (`apps/system/usb_shell/` + `apps/system/shell_core/`, builtins + 14 commandes en `apps/system/bin/`), tooling (`dbt` package + TUI Textual + plugins toolchain), init system (`boards/<board>/init.yaml` flashé en sysbin + `/sd/init.yaml` optionnel), politiques de restart.
- **Réseau** : Daemon WiFi, `duneos_netif_wait_ip()`, injection de trames brutes 802.11 (`/dev/raw80211`), exports BSD socket complets, `apps/system/bin/ifconfig` + `ping`.
- **Hardening (Phase 20 partielle)** : per-app heap (heap_caps_malloc), Task WDT par slot, handler d'exception Xtensa par app (kill propre sans reboot kernel), validation pointeurs syscall (`check_user_ptr` + `check_app_writable_ptr`). Restent : stack canary, TLSF userspace.
- **DHI (Phase 24)** : 6 headers HAL purs (`hal_uart.h`, `hal_gpio.h`, `hal_i2c.h`, `hal_spi.h`, `hal_adc.h`, `hal_time.h`), implémentations Xtensa dans `arch/xtensa_esp32s3/hal/`, drivers HW délégant au HAL, `arch.cmake` self-selection. Périmètre HAL strict ; la purge des `esp_err_t` des 4 headers core (`init.h`/`task.h`/`vfs.h`/`supervisor.h`) est répartie sur Phases 26-27 (au moment où chaque .c sous-jacent change d'API).

**Objectif V2** : Transformer ce POC fortement couplé à ESP-IDF/FreeRTOS en un véritable OS indépendant, sécurisé sans MMU, avec un outillage de classe mondiale.

---

## 3. Architecture Cible

```text
DuneOS/
├── apps/
│   ├── system/           (Apps qui shippent avec DuneOS : usb_shell, shell_core, wifi_daemon, bin/*)
│   └── user/             (Apps tierces / templates de développeur)
├── tools/                (dbt/ modulaire, bspgen.py)
└── kernel/               (Code source du noyau pur)
    ├── arch/             (Spécifique au CPU : context switch, MPU/PMP, exceptions)
    ├── boards/           (Configuration YAML et headers générés)
    ├── osal/             (Abstraction OS : threads, mutexes, stubs FreeRTOS)
    ├── vfs/              (duneos_vfs natif, devfs)
    ├── drivers/          (Implémentation DHI : UART, I2C, SPI, TinyUSB)
    ├── loader/           (ELF Loader, Core Dump generator)
    └── libdune/          (PicoLibc + syscall stubs statiques)
```

---

## 4. Roadmap Détaillée

### Phase 17 — Tooling DX (DX First) ✅

Ne plus coder d'outils jetables. Préparer le terrain pour de multiples architectures sans toucher à CMake.

- [x] **Découpage de `dbt.py`** : Transformé en sous-modules (`tools/dbt/cli.py`, `builder.py`, `deploy.py`, `toolchain.py`).
- [x] **`duneos.yaml` applicatif** : Remplace `manifest.json`. Champs : `stack`, `heap`, `permissions`, `sources`. Rétrocompatibilité `manifest.json` maintenue une phase.
- [x] **`init.yaml`** : `/sd/init.json` migré vers `/sd/init.yaml`. Parser YAML au lieu de cJSON dans `init.c`.
- [x] **`bspgen.py` universel** : Dépendances ESP-IDF supprimées ; génère `board_config.h` pur C sans `esp_err_t`.
- [x] **TUI dbt** : Interface plein-écran btop/ranger (Textual) — panneau ACTIONS + OUTPUT. Flash Kernel, Flash Sysbin, Build All, Build App, Flash SD, BSP Gen, Init Config (`boards/<board>/init.yaml`), Board picker, Port picker, persistance du chemin SD (`.duneos_sd`).

> **Simulateur natif** (`dbt.py run --sim`) — déféré après la Phase 19, nécessite une phase dédiée.

---

### Phase 18 — libgfx (Portabilité display) ✅

Une app qui dessine doit compiler et tourner identiquement sur CardPuter (ST7789, pas de PSRAM) et T-Embed (ST7789, PSRAM, `/dev/fb0`) sans `#ifdef` dans le code source.

- [x] **`/flash/board.info`** (+ `/sd/board.info`) : Fichier YAML écrit par le noyau au boot depuis `board_config.h` (champs : `board`, `display`, `width`, `height`, `fb`).
- [x] **`<duneos/gfx.h>`** : API publique — `gfx_open/close/fill/pixel/text/flush/get_info`. Pixel format RGB565 host byte order.
- [x] **`sdk/display/gfx.c`** : Une seule implémentation, sélection **runtime** du backend dans `gfx_open()` — Tier B (`/dev/fb0`, kernel PSRAM framebuffer) si dispo, sinon Tier A (`/dev/disp0`, streaming SPI). Le drawing va dans un back-buffer userspace ; `gfx_flush()` pousse en un seul shot.
- [x] **Demo app** `gfx_demo.dap` — dessine formes + texte, tourne sur toutes les boards avec un display sans recompilation du source.

> **Note vs design original** : la roadmap mentionnait initialement deux fichiers backends séparés (`gfx_st7789.c` + `gfx_fb.c`) sélectionnés au build via `.duneos_board`. L'implémentation retenue est plus simple : une seule lib qui sniff `/dev/fb0` au runtime. Même résultat (portabilité source-level), moins de code dbt.

---

### Phase 19 — Flash storage (Boot sans SD) ✅

DuneOS doit booter et être utilisable même sans carte SD insérée.

- [x] **Partition `sysbin` LittleFS** dans `partitions.csv` (~1 MB) ; monter sur `/flash` dans `vfs.c`.
- [x] **Embed des apps vitales** (`shell.dap`, commandes `apps/system/bin/`) en blobs firmware via `COMPONENT_EMBED_FILES`.
- [x] **First-boot provisioning** : Le noyau copie les blobs manquants vers `/flash/bin/` au premier démarrage.
- [x] **Cascade loader** : Chercher `/flash/bin/` → `/sd/bin/` → `/sd/apps/`.
- [x] **BSP YAML** : Champ `has_sd: false` ; `vfs.c` skip le montage SD et lit `init.yaml` depuis `/flash`.
- [x] **`dbt.py flashimg`** : Produit une image LittleFS flashable directement via `esptool` (port depuis `.duneos_port` / `--port` / `DUNEOS_PORT`).
- [x] **`bspgen.py`** : Génère `partitions.csv` par board depuis `flash_size_mb` ; `sdkconfig.board` remplace les `sdkconfig.defaults` manuscrits.
- [x] **Init dedup par nom d'app** : `init.c` déduplique les services par nom (basename sans `.dap`) — évite le double-lancement quand un même service apparaît dans `/flash/init.yaml` et `/sd/init.yaml`. L'entrée flash gagne (chargée en premier).
- [x] **`boards/<board>/init.yaml`** : Fichier par board versionné dans le repo, contrôlé par l'utilisateur. `dbt flashimg` le copie tel quel (fini la liste hardcodée `_BOOT_SERVICES`). Éditable via TUI (`i` → Init Config) : sélection des apps + politique de restart (`always`/`on-failure`/`no`).

---

### Phase 20 — Hardening mémoire + Dette technique 🟡 PARTIEL

Garantir qu'une application ne peut pas crasher le système. Purger la dette technique accumulée.

- [x] **Per-app exception handler** : Intercepter le crash → logger dans `/dev/klog` → unload propre sans reboot du kernel. (`supervisor.c` → `app_exception_handler`).
- [x] **Task WDT** : Le supervisor nourrit le WDT pour l'app ; kick de l'app en cas de timeout.
- [x] **Per-app heap** : Pool DRAM dédié par app via `heap_caps_malloc` (bloc contigu Code+Data+Heap). Bloc monolithique TLSF différé.
- [x] **Validation syscall** : `duneos_supervisor_check_user_ptr` (permissif, buffers sources) + `duneos_supervisor_check_app_writable_ptr` (strict, buffers cibles). Utilisé par `api.c` (read/write) et `symbols.c`. Rejette espace périphériques et IRAM.
- [ ] **Stack canary** par task applicative.
- [ ] **TLSF userspace allocator** : `libdune.a` gère son propre `malloc()` uniquement dans ce pool — actuellement `malloc()` côté app appelle directement `heap_caps_malloc` du kernel via `__duneos_api_ptr->mem`.

---

### Phase 21 — dbt : plugin toolchain multi-arch ✅

`board.yaml` gagne les champs `arch:` et `sdk:`. `tools/dbt/toolchain/` devient un répertoire de plugins.

- [x] **`board.yaml`** : Champs `arch:` et `sdk:` ajoutés aux 4 boards existantes.
- [x] **`tools/dbt/toolchain/__init__.py`** : `load_plugin(sdk)` + `get_board_plugin()` — dispatche vers le bon module plugin.
- [x] **`tools/dbt/toolchain/esp_idf.py`** : Premier plugin — `SDK`, `ARCH`, `find_compiler()`, `cflags()`, `ldflags()`, `linker_script()`, `build_kernel()`, `flash_kernel()`, `monitor()`, `find_toolchain_root()`.
- [x] **`builder.py`**, **`cli.py`**, **`flashimg.py`**, **`kernel.py`** : Dispatch via `get_board_plugin()` — plus d'import direct de `toolchain.py`.
- [x] Ancien `toolchain.py` supprimé — remplacé par le package `toolchain/`.

---

### Phase 22 — Syscalls et Migration PicoLibc ✅

Vitesse et légèreté : fiabiliser l'ABI.

- [x] **`duneos_api_t` — table d'API typée (ABI v3)** : `kernel/duneos_kernel/include/duneos/api.h` définit une struct de pointeurs de fonctions couvrant `fs`, `mem`, `thread`, `time`, `sys`. Le loader injecte l'adresse du singleton noyau dans le symbole `__duneos_api_ptr` de l'app avant d'appeler `app_main`. Résolution O(1), nul besoin de recherche par chaîne.
- [x] **`DUNEOS_ABI_VERSION` → 3** : `abi.h` bumped ; compatibilité amont garantie (apps v1/v2 sans `__duneos_api_ptr` utilisent toujours la table de symboles `duneos_symbol_table_get()`).
- [x] **`kernel/duneos_kernel/src/api.c`** : Instance statique `s_api` + `duneos_api_get()`. Wrappers minces pour `read` (check_app_writable_ptr), `write` (check_user_ptr), `dup`/`dup2`, `dprintf`, `opendir`/`readdir`/`closedir`, loader et IPC via `void *` (évite la dépendance circulaire noyau↔loader).
- [x] **Stack allouée statiquement (`xTaskCreateStaticPinnedToCore`)** : Bornes exactes `[stack_mem, stack_mem+stack_size)` connues → `check_app_writable_ptr` peut valider les pointeurs d'écriture noyau→app.
- [x] **Validation pointeurs à deux niveaux** : `check_user_ptr` (permissive, pour les buffers sources) + `check_app_writable_ptr` (stricte, pour les buffers cibles). Rejette l'espace périphériques et IRAM.
- [x] **Parser manifeste cJSON** : Remplace l'ancien `strstr`/`sscanf` par `cJSON` dans `loader.c`. Non-fatal sur JSON invalide (boot avec defaults).
- [x] **`duneos_exit` relocalisé dans `supervisor.c`** : Emplacement sémantiquement correct (opération de cycle de vie). Retiré de `symbols.c`.
- [x] **Migration PicoLibc** : `-isystem picolibc/include` déjà injecté par `esp_idf.py`. `-D_POSIX_C_SOURCE=200809L` expose `clock_gettime` etc. `-D__PICOLIBC_ERRNO_FUNCTION=__errno` route `errno` via le TLS FreeRTOS du noyau.
- [x] **`libdune.a` — bibliothèque POSIX userspace** : `libdune/src/` : `libdune_ptr.c` (ancre `__duneos_api_ptr`), `libdune_fs.c` (I/O fichiers + répertoires), `libdune_mem.c` (malloc/free/realloc/calloc), `libdune_thread.c` (pthreads + sémaphores), `libdune_time.c` (clock, gettimeofday, usleep, sleep, nanosleep), `libdune_sys.c` (lifecycle DuneOS, IPC, supervisor, loader, `__errno` PicoLibc).
- [x] **`<duneos/libdune.h>`** : Header app-facing complet — déclare `duneos_restart_policy_t`, `duneos_slot_info_t`, toutes les fonctions DuneOS non-POSIX, `duneos_sys_restart()`, `duneos_sys_free_heap()`.
- [x] **`dbt build` intègre libdune** : `builder.py` appelle `build_libdune()` avant le link — cache par arch dans `libdune/build/{arch}/libdune.a`, invalidé par mtime des sources et headers. `find_compiler()` dans `esp_idf.py` expose désormais `ar` dans le dict `tc`.

---

### Phase 23 — The Flipper DX (USB Device Subsystem) ✅

L'expérience Plug and Play ultime.

- [x] **TinyUSB** : Composite MSC+CDC sur `espressif/esp_tinyusb ^2.0.1~1`. `drv_usb.c` initialise le device stack.
- [x] **USB MSC (Mass Storage)** : `/sd` exposé en drag-and-drop. `duneos_vfs_get_sd_card()` passe le handle sdmmc au backend MSC.
- [x] **USB CDC (Console)** : `drv_usb_cdc.c` — TX mutex-sérialisé (un seul `write_flush` non-bloquant, pas de collision), RX via ring buffer + sémaphore → `/dev/ttyUSB0`. `apps/system/usb_shell/` + `apps/system/shell_core/` remplacent `system/shell/`.
- [x] **`console: none`** : `bspgen.py` émet `CONFIG_ESP_CONSOLE_NONE=y` pour les boards OTG — UART0 reste libre pour les apps. Le klog est redirigé vers CDC à l'exécution via `esp_log_set_vprintf`.

---

### Phase 24 — DHI (DuneOS Hardware Interface) ✅

Isoler le noyau pour amorcer la sortie du framework Espressif. **Périmètre de cette phase = HAL hardware uniquement** : 6 headers HAL purs (UART/GPIO/I2C/SPI/ADC/time), implémentation Xtensa, migration des drivers HW, infrastructure multi-arch. La purge complète des autres types ESP-IDF (`esp_err_t` dans les headers core, FreeRTOS handles, `esp_*` réseau) se fait *au fur et à mesure* dans les phases qui touchent réellement à ces fichiers (26, 27).

- [x] **Headers DHI hardware** : `hal_uart.h`, `hal_gpio.h`, `hal_i2c.h`, `hal_spi.h`, `hal_adc.h`, `hal_time.h` — types purs (`uint32_t`, `int`, callbacks C standards). Zéro dépendance ESP-IDF.
- [x] **Implémentations ESP-IDF** : `arch/xtensa_esp32s3/hal/hal_*.c` — ESP-IDF types **uniquement en interne**. Compilation conditionnelle via le triple-guard `arch.cmake`.
- [x] **Migration des backends HW** : `drv_uart.c`, `drv_gpio.c`, `drv_i2c.c`, `drv_spi.c`, `i2c_bus.c`, `drv_battery_adc_simple.c` délèguent au HAL. Drivers input (`btn_gpio.c`, `enc_quadrature.c`, `kb_iomatrix.c`) migrés. `dev_driver.h` et `i2c_bus.h` retournent `int`.
- [x] **Abstraction interruptions** : `duneos_hal_gpio_set_intr()` (usage kernel-interne). `GPIOCHIP_SET_IRQ` retourne `ENOSYS` — livraison userspace via signal repoussée (Phase 27 — VFS natif + `poll`).
- [x] **Arch dans le manifest** : Champ `arch[32]` dans `duneos_app_manifest_t`. Loader rejette les `.dap` cross-ISA. `dbt builder.py` injecte `arch` depuis le plugin toolchain.
- [x] **IDs numériques en BSP** : `bspgen.py` émet SPI host et ADC unit en entiers.
- [x] **`arch.cmake` self-selection** : `file(GLOB arch/*/arch.cmake)` + triple-guard. Ajouter une arch = créer un seul fichier, zéro touche au kernel core.

**Dette `esp_err_t` dans les headers core — répartie par phase cible (pas par "Phase 24b" séparée)** :

| Header public | Migration prévue | Justification |
|---|---|---|
| `init.h` (1 fn) | **Pré-25, micro-tâche** | N'appelle aucune API ESP-IDF en interne, juste POSIX. Migration triviale isolée. |
| `task.h`, `supervisor.h` (4 fns + 2 typedefs) | **Phase 26 (OSAL)** | `xTaskCreate`/`xTaskNotify`/etc. disparaissent au profit de `osal_*` — le retour change naturellement. |
| `vfs.h` (6 fns) | **Phase 27 (VFS natif)** | `esp_vfs_register`/`esp_vfs_fat_*`/`esp_vfs_littlefs_*` disparaissent au profit de `duneos_vfs_*` natif. |

**Convention de retour décidée (ADR — voir Phase 24.5)** : `int` retournant **0** sur succès, **-errno** sur échec (`-ENOENT`, `-EIO`, `-ENOMEM`, `-EINVAL`, …). Pas d'enum `duneos_status_t` dédiée. Helper `esp_to_errno()` privé pendant la transition, supprimé quand son `.c` appelant migre.

**Mini-correctif documentaire à inclure dans la micro-tâche init.h** :

- Commentaire obsolète `input_ioctl.h:47` (mentionne `xTaskGetTickCount() * portTICK_PERIOD_MS` alors que le champ est désormais peuplé via `duneos_hal_monotonic_us() / 1000`).
- Commentaire `task.h:8-10` "FreeRTOS abstraction" — à reformuler en "OSAL abstraction" au moment de Phase 26.

**Driver placement debt** — découlant de l'application des [ADR 009](docs/adr/009-driver-boundary.md) (kernel/userspace boundary) et [ADR 010](docs/adr/010-arch-accelerators.md) aux drivers existants. Cinq items :

- [x] **`drv_battery_bq27220.c` → userspace** : Migré vers `sdk/sensor/libbq27220.c` (lib userspace pure). Le driver kernel `drv_battery_bq27220.c` survit pour servir `/dev/battery0`. Conversion totale en daemon `battery_daemon.dap` reportée — non bloquant.
- [x] **ST7789 : consolider 3 codepaths en 1** : `drv_disp_st7789.c` retiré (`/dev/disp0` n'existe plus). `drv_fb_st7789.c` conservé pour `/dev/fb0` sur boards PSRAM uniquement. `sdk/display/libst7789.c` réécrit pur userspace (ioctl/dev/spi-N + /dev/gpiochip0). Apps consomment via `<duneos/disp.h>` + `libdisp.c`.
- [x] **GPIO expanders : règle explicite dans `bspgen`** : Schéma `gpio_expanders: [{type: sx1509, i2c_addr: 0x3e, pins: 16}, ...]` dans `board.yaml`. `bspgen` émet `DUNEOS_GPIOCHIP{N}_TYPE/I2C_ADDR/PINS` dans `board_config.h` + `CONFIG_DUNEOS_DRV_GPIOCHIP_{type}=y` dans `sdkconfig.board`. Drivers C `drv_gpiochip_*.c` non encore implémentés (stubs Kconfig en place — sera complété quand un board en exige réellement).
- [x] **`hal_encoder` capability HAL (ADR 010 Pattern A)** : `hal_encoder.h` + backend Xtensa PCNT (strong symbol) + fallback GPIO polling (weak symbol). `enc_quadrature.c` réduit à un thin shim (83 → 49 lignes). Backend RP2040 (PIO) à ajouter en Phase 29.
- [ ] **uinput + input drivers userspace (item #5)** : `kb_iomatrix.c` et `btn_gpio.c` actuellement kernel-side ne satisfont aucun critère ADR 009 (un consommateur, pas d'ISR critique, pin matrice dédiée, lifecycle app-scope). Migration plan :
  1. Nouveau driver kernel **`drv_uinput.c`** exposant `/dev/uinput` (style Linux uinput) — apps userspace écrivent des `input_event_t`, le kernel les forwarde vers `/dev/input/event0`.
  2. **`apps/system/kb_iomatrix.dap`** : daemon userspace qui scanne la matrice via `/dev/gpiochip0` et émet vers `/dev/uinput`. Idem **`btn_gpio.dap`** pour boards à boutons.
  3. `enc_quadrature.c` reste **partiellement kernel** (le HAL `hal_encoder` a besoin de l'ISR PCNT) ; mais la conversion "delta → input_event" peut migrer dans un thin app userspace si profitable.
  4. `init.yaml` lance ces input daemons en `restart: always` au boot, avant tout app qui consomme `/dev/input`.
  5. **Étend ADR 014** : nouvelle capability `input` qui résout `lib${board.input.driver}.c` (kb_iomatrix, btn_gpio, …). Le board declare `input.driver: iomatrix` ; dbt link le bon backend.

  Effort : ~1 semaine focus. Idéalement avant le contest 2026 (démontre clairement le pattern "apps remplaçables sans reflasher").

> Items 1-4 sont des refactors purs (pas de change d'API public côté apps). Item 5 est plus structurel — touche au modèle d'input mais reste retro-compatible (apps lisent toujours `/dev/input/event0`).

---

### Phase 24.5 — Design Decisions / ADR ✅

11 ADRs courts dans [`docs/adr/`](docs/adr/) (voir [README](docs/adr/README.md) pour l'index). Format Michael Nygard, 1 page max chacun, statut `Accepted · 2026-05-19`. Tous les choix consommés par les phases 24 (debt) et 25-29.

- [x] **ADR 000** — process des ADR
- [x] **ADR 001** — error model `int`/-errno (consommé par 26-27)
- [x] **ADR 002** — OSAL API, static-storage handles uniquement (consommé par 26)
- [x] **ADR 003** — memory caps, fallback silencieux vers DRAM (consommé par 26)
- [x] **ADR 004** — task priorities 5 niveaux symboliques (consommé par 26)
- [x] **ADR 005** — path conventions, `/flash` mandatory (consommé par 25, kernel boot)
- [x] **ADR 006** — manifest extensibility, champs inconnus ignorés (consommé par 25, loader)
- [x] **ADR 007** — multi-arch smoke test (consommé par 26-28, prérequis 29)
- [x] **ADR 008** — memory fragmentation strategy (consommé par 20 TLSF, kernel review policy)
- [x] **ADR 009** — kernel/userspace boundary for drivers (consommé par Phase 24 debt, toute nouvelle PR driver)
- [x] **ADR 010** — architecture-specific accelerators (consommé par Phase 24 debt hal_encoder, Phase 29 PIO)
- [x] **ADR 011** — threat model: permissions are advisory (consommé par Phase 32 signing, README warning, future security claims)
- [x] **ADR 012** — test strategy: host-side first (consommé par Phase 26+, prérequis du refactor OSAL)

---

### Phase 24.7 — Safe boot & recovery

**Pourquoi ici (avant Phase 25) :** une `init.yaml` qui lance un service `restart: always` qui crash au démarrage met la board dans une boucle de relance. Sur CardPuter sans bouton de boot dédié et sans UART accessible, la seule sortie est `dbt flash sysbin` — qui suppose que USB MSC monte AVANT le crash. Cas pas garanti. Phase 25 (`dbt system deploy`) va automatiser des déploiements qui peuvent introduire exactement ce bug → on doit pouvoir s'en sortir avant.

**Périmètre minimal** :

- [ ] **Circuit breaker dans le supervisor** : si un service crash N fois en M secondes (par défaut 3 crashes en 30 s), passer en `restart: no` et logger `klog_e`. Configurable par service via un nouveau champ `init.yaml` (`max_restarts:` / `restart_window_s:`, optionnels, défauts raisonnables). Empêche la boucle infinie.
- [ ] **Hold-key-at-boot fallback** : `board.yaml` gagne un champ optionnel `recovery_pin: { gpio: 0, level: low }` (sur CardPuter c'est la touche `OK` ou le BOOT button selon le câblage). Si maintenu pressé à l'init, le kernel ignore `init.yaml` et lance uniquement `usb_shell.dap` depuis flash. Sortie immédiate vers une console fonctionnelle même sans SD.
- [ ] **Garantie ordre USB MSC avant init** : aujourd'hui `duneos_vfs_init()` appelle `drv_usb_preinit()` en première action, puis seulement après lance les services init. Vérifier et documenter que cette séquence est respectée sur tous les boards OTG. Si un service init crash, USB MSC reste monté et le user peut écraser le fichier fautif.
- [ ] **`dbt flash sysbin --safe`** : option pour flasher un sysbin avec une init.yaml minimale (uniquement `usb_shell.dap`) sans toucher au reste. Geste de récupération à un coup.
- [ ] **Doc README.md** : section "Recovering from a bad init.yaml" qui décrit les 3 voies de sortie (hold key, `dbt flash sysbin --safe`, full reflash).

> Ce n'est PAS un système d'OTA, ni de A/B partitions, ni de bootloader custom. Juste les filets nécessaires pour ne pas bricker une board pendant le dev. L'OTA complet est une phase future séparée.

---

> ### ⚡ Contest sprint 2026 — M5Stack Global Innovation Contest
>
> **Plan complet : [`docs/contest-2026.md`](docs/contest-2026.md).**
>
> En cas de participation au [contest M5Stack 2026](https://m5stack.com/global-innovation-contest-2026) (deadline 7 août 2026), la roadmap **gèle après Phase 25 (minimum viable)** jusqu'au 31 août. Les Phases 26-29 reprennent au 1er septembre. Le sprint absorbe Phases 24.7 + 25-minimal et livre une killer-app demo : `i2cscope` + `lua` REPL + `snake` + `tetris` + launcher graphique avec icônes (nouveau champ `icon:` dans le manifest).
>
> **Non-bloquant pour la roadmap principale** si on ne participe pas — dans ce cas, Phase 25 attaque directement après Phase 24.7.

---

### Phase 25 — dbt system (Image Recipes & Vérification)

Un Yocto sans la complexité de Yocto. Trois fichiers YAML et trois commandes `dbt` pour construire un système complet et vérifié — du kernel aux apps.

- [ ] **`profile.yaml`** : Profil kernel — liste les drivers à inclure, les options (max_apps, flash_only…). Surcharge `sdkconfig.board` généré par bspgen. Un même board peut avoir plusieurs profils (full, minimal, production).
- [ ] **`system.yaml`** : Recette système — référence un profil + déclare les apps avec leur politique de restart. Remplace la gestion manuelle de `init.yaml`.
- [ ] **`tools/dbt/capability_map.py`** : Table de correspondance `DUNEOS_PERM_*` ↔ `CONFIG_DUNEOS_DRV_*`. Codifie le mapping implicite de `vfs_dev.c`.
- [ ] **`dbt profile build`** : Construit le kernel avec le profil donné.
- [ ] **`dbt system check`** : Lit le manifest ELF de chaque `.dap` déclaré, croise les permissions requises contre les features du profil — rapport des incompatibilités avant le build. Exemple : `wifi_daemon.dap requires DUNEOS_PERM_NET → MANQUANT`.
- [ ] **`dbt system build`** : Orchestre kernel + apps + image flash en une commande.
- [ ] **`dbt system deploy`** : Build + flash + déploiement des `.dap` sur SD/flash.
- [ ] **`dbt TUI`** : Doit s'intégrer proprement au TUI
---

### Phase 26 — OSAL et Portabilité Scheduler

**Inversée avec l'ex-Phase 26 (VFS natif).** Raison : OSAL = surface API plus petite, débloque `dbt sim` plus tôt, valide l'abstraction sur 2 archs Espressif (Phase 28) avant le rewrite VFS plus risqué, et fournit les primitives (mutex, sem) qu'utilisera le VFS natif en Phase 27.

Abstraire FreeRTOS derrière une interface propre. **DuneOS ne réimplémente pas de scheduler** — FreeRTOS reste le scheduler sur toutes les targets qui le supportent. L'OSAL permet aux targets sans FreeRTOS natif (simulateur Linux, architectures futures) d'utiliser une implémentation alternative. **API gelée par ADR 002, 003, 004 — ne pas dévier sans nouvel ADR.**

- [ ] **`duneos_osal.h`** : Implémenter selon ADR 002. Primitives task, mutex, sem, queue, mem (avec flags ADR 003), monotonic_us, panic_print. Pas de timer software dans cette première version.
- [ ] **`freertos_osal.c`** : Implémentation pour toutes les platforms FreeRTOS (ESP32-S3 Xtensa, ESP32-C6 RISC-V, RP2040 SMP). Un seul fichier réutilisé à travers les toolchain plugins.
- [ ] **`pthread_osal.c`** : Implémentation via pthreads — permet `dbt sim` (simulateur natif Linux/macOS). Mapping ADR 004 vers `SCHED_OTHER` (priorités gérées par nice).
- [ ] **Confinement du Blob WiFi** : Isoler la dépendance du blob WiFi Espressif derrière `freertos_osal.c` (callbacks et structs FreeRTOS attendus par le blob restent dans le `.c` OSAL ESP-IDF).

**Migrations associées — purge des `freertos/*.h` et `esp_heap_caps.h` du cœur kernel** :

- [ ] **`task.h`, `supervisor.h`** : Migration des signatures publiques `esp_err_t` → `int` (-errno). Dette héritée de Phase 24 ; se fait naturellement ici parce que les implémentations sous-jacentes changent.
- [ ] **`supervisor.c`, `task.c`, `symbols.c`, `klog.c`, `api.c`** : Remplacer `freertos/*.h` + `esp_heap_caps.h` + `esp_system.h` par les primitives `duneos_osal.h`. Couvre création de tasks, queues/mutexes/sémaphores, allocation mémoire interne.
- [ ] **`esp_rom_printf` → `osal_panic_print()`** : Output de dernier recours (handlers exception/WDT/stack-overflow). Implémenté par `esp_rom_printf` sur ESP32, `fprintf(stderr,...)` sur le simulateur Linux. Pas de fallback `klog_e()` (risque de crash en contexte crashé).
- [ ] **`vfs_dev.c`, `vfs_tmp.c`** : Remplacer `freertos/*.h` (ring buffers, semaphores) par `duneos_osal.h`. Note : `esp_vfs.h` reste, c'est Phase 27 qui le supprime.
- [ ] **`drv_fb_st7789.c`** : Remplacer `esp_heap_caps.h` (allocation PSRAM) par `osal_mem_alloc(size, OSAL_MEM_EXTERNAL)`.
- [ ] **`duneos_loader/src/loader.c`** : 3 sites de `heap_caps_malloc(MALLOC_CAP_SPIRAM \| MALLOC_CAP_8BIT)` / `MALLOC_CAP_INTERNAL` (allocation des sections de l'app : code, data, heap) → `osal_mem_alloc(size, OSAL_MEM_EXTERNAL)` / `osal_mem_alloc(size, 0)`. `esp_rom_printf` → `osal_panic_print()` (mêmes sites que `supervisor.c`).
- [ ] **`loader.c` — `soc/soc.h`** : Constantes de memory map (utilisées par les checks de range internes). Décision : (a) exposer les ranges via une nouvelle struct `duneos_memmap_t` fournie par l'arch.cmake (préf.), (b) ou un mini-header `hal_memmap.h` arch-specific. À trancher en début de phase. Concerne uniquement `loader.c` aujourd'hui.
- [ ] **Reformuler commentaire `task.h:8-10`** "FreeRTOS abstraction" → "OSAL abstraction".

**Prérequis Libc — PicoLibc en `third_party/`** :

Jusqu'à cette phase, c'est `esp_libc` (composant ESP-IDF) qui fournit PicoLibc + ses stubs syscall. Dès que `pthread_osal.c` est implémenté pour le simulateur Linux, il n'y a plus d'ESP-IDF pour fournir la libc. C'est le **point de déclenchement obligatoire** pour bundler PicoLibc :

- [ ] **`third_party/picolibc`** : Git submodule (même stratégie que cJSON/LittleFS). Compilé pour la target courante par le toolchain plugin.
- [ ] **`tools/dbt/toolchain/esp_idf.py`** : Retirer la dépendance implicite sur `esp_libc` — passer l'include path et le linker script PicoLibc explicitement via le plugin.
- [ ] **Cohérence multi-SDK** : pico-sdk fournit newlib (pas PicoLibc) — comportements `stdio`/`errno` légèrement différents. Utiliser la PicoLibc bundled sur **toutes** les targets (décision actée par cette phase, validée par Phase 29).

---

### Phase 27 — Refonte VFS et Stabilisation Réseau

**Inversée avec l'ex-Phase 27 (OSAL).** Raison : voir Phase 26. VFS natif a besoin des primitives OSAL (mutex pour les structures globales du VFS, sem pour wait queues de `poll`/`select`), donc ne peut pas s'écrire avant.

Préparer la stack réseau avant d'affronter le découplage WiFi. **API gelée par ADR 001 et 005 — codes -errno, conventions de path documentées.**

**Migrations DHI hardware restantes** (les seules encore en `driver/spi_master.h` après Phase 24a) :

- [ ] **`vfs.c` init SD** : Remplacer `driver/spi_master.h` + `driver/gpio.h` (init bus SPI de la carte SD) par `hal_spi.h` + `hal_gpio.h` — les implémentations existent déjà, migration seule.
- [ ] **`display/st7789_hw.c/.h`** : Remplacer `driver/spi_master.h` par `hal_spi.h` — types `spi_device_handle_t` encapsulés dans l'implémentation, header public purgé d'`esp_err.h`.

**Architecture réseau** — gelée par [ADR 013](docs/adr/013-network-architecture.md) : sockets POSIX + lwIP vendored + per-medium HAL (`hal_wifi.h` ≠ `hal_eth.h`) + `struct netif` lwIP directement comme registre. Pas de wrapper `duneos_netif_t`. Pas d'`esp_netif`.

**Vendoring de lwIP** :

- [ ] **`third_party/lwip` git submodule** : Cesser d'utiliser l'lwIP fourni par ESP-IDF. Compilé pour la target courante par le toolchain plugin. Décision actée par ADR 013 ; même stack partout = même comportement, même DNS resolver, même API. Prérequis pour Phase 29 (RP2040 a son propre lwIP via pico-sdk, à substituer).
- [ ] **`tools/dbt/toolchain/esp_idf.py`** : Retirer la dépendance implicite sur `lwip` ESP-IDF component ; lier explicitement le `third_party/lwip` compilé.

**HAL réseau (split par médium, ADR 013)** :

- [ ] **`kernel/duneos_kernel/include/duneos/hal_wifi.h`** : Interface pure C — `hal_wifi_init/scan/connect_sta/disconnect/start_ap/get_rssi/set_event_cb` + `hal_wifi_tx_packet` + RX callback. Aucun type esp_wifi / cyw43.
- [ ] **`kernel/duneos_kernel/include/duneos/hal_eth.h`** : MAC control — `hal_eth_init/tx/set_rx_cb/get_stats`.
- [ ] **`kernel/duneos_kernel/include/duneos/hal_phy.h`** : MDIO abstraction — `hal_phy_read/write/get_link`. Permet support de plusieurs PHY (LAN8720, KSZ8081, RTL8201, …) avec un mini-fichier par chip sous `arch/<arch>/hal/phy/`.
- [ ] **`arch/xtensa_esp32s3/hal/hal_wifi.c`** : Wrap esp_wifi + esp_event en backend ESP-IDF.

**Drivers link layer** :

- [ ] **`kernel/duneos_kernel/src/drivers/net/wifi_esp.c`** : Réécrire l'actuel `drv_wifi.c` — utilise `hal_wifi.h` (plus d'`esp_wifi` direct), enregistre `struct netif` lwIP avec `linkoutput` callback, fait le pont packet rx_cb → `netif->input()`.
- [ ] **`drv_raw80211.c`** : Reste fonctionnellement identique (injection 802.11 bypass de la stack IP). Migration vers `hal_wifi.h` uniquement pour les bits de config initialisation partagés.
- [ ] **Loopback `lo`** : créé en kernel init via lwIP natif. Utile pour tests sans hardware ([[ADR-012]]).

**API de configuration network** (remplace le foutoir Linux ioctl/netlink) :

- [ ] **`duneos_netif_set_ip/set_dns/get_status/list`** : Header `kernel/duneos_kernel/include/duneos/netif.h`. Apps + `apps/system/bin/ifconfig` consomment.
- [ ] **`duneos_netif_wait_ip()`** : Réimplementation au-dessus de `struct netif` flags + `osal_sem`. Même symbol public.

**VFS natif** :

- [ ] **`vfs.h`** : Migration des 6 signatures publiques `esp_err_t` → `int` (-errno). Dette héritée de Phase 24.
- [ ] **`duneos_vfs` natif** : Remplacer `esp_vfs.h` dans `vfs.c`, `vfs_dev.c`, `vfs_tmp.c` — gère nativement `poll()`/`select()` et les sockets via wait-queues bâties sur `osal_sem`.
- [ ] **VFS Sockets** : Router les appels BSD vers lwIP via la nouvelle stack interne.
- [ ] **GPIO IRQ userspace** : `GPIOCHIP_SET_IRQ` (ENOSYS en Phase 24) devient livrable maintenant que `poll()` est dispo — events sur fd dédié.

**Ethernet RMII** (optionnel, dépend de la disponibilité d'un board cible) :

- [ ] **MAC ESP32 (pas S3)** : board RMII de référence à ajouter si la phase démarre avant Phase 29. PHY = LAN8720 ou KSZ8081 ; `arch/xtensa_esp32/hal/phy/lan8720.c` ~50 lignes implémentant `hal_phy_*`.

---

### Phase 28 — Ports RISC-V Espressif : ESP32-C6 + ESP32-P4

**Objectif : valider que `arch/*/arch.cmake` fonctionne réellement sur un second ISA.** ESP32-C6 et ESP32-P4 sont RISC-V mais restent sous ESP-IDF — même build system, nouvelle arch. Si quelque chose casse, c'est une lacune dans l'abstraction, pas dans le tooling.

**Unification des guards `arch.cmake`** — fondations posées en Phase 24, reste l'usage hors ESP-IDF :

- [x] **Variable `DUNEOS_ARCH` introduite** : Le triple-guard (`CONFIG_IDF_TARGET_ARCH_XTENSA` + `IDF_TARGET_ARCH` + `DUNEOS_ARCH`) est déjà en place dans `arch/xtensa_esp32s3/arch.cmake`. La doc CLAUDE.md décrit le pattern à reproduire pour les nouvelles archs.
- [ ] **Vérifier le guard hors ESP-IDF** : Tester qu'un plugin toolchain non-ESP-IDF (Phase 29) peut bien activer une arch en posant uniquement `DUNEOS_ARCH=<valeur>` avant le configure.

**Prérequis : modulariser le loader ET l'exception handler (dette héritée Phase 24)** — bloquant avant d'ajouter une 2e arch, sinon on duplique tout le code ISA-specific :

- [ ] **`duneos_arch_ops_t` interface** : Définir dans `kernel/duneos_loader/include/duneos/arch_ops.h` une struct `{ int (*apply_reloc)(void *base, const Elf32_Rela *r, ...); const char *arch_name; }` que l'arch enregistre via `duneos_loader_register_arch_ops(&ops)`. Loader.c devient arch-agnostique.
- [ ] **Extraire la logique Xtensa de `loader.c`** vers `arch/xtensa_esp32s3/reloc/loader_reloc_xtensa.c` (le stub existe depuis Phase 24 mais n'a jamais été rempli — environ 150 lignes à déplacer : R_XTENSA_32, R_XTENSA_SLOT0_OP, R_XTENSA_ASM_EXPAND, R_XTENSA_DIFF8/16/32 + `apply_slot0_op` helper).
- [ ] **`duneos_arch_exception_t` interface** : Définir dans `kernel/duneos_kernel/include/duneos/arch_exception.h` une struct `{ void (*install_handler)(int core_id, void (*on_crash)(int slot_id, uint32_t cause, uint32_t pc, uint32_t sp)); }`. Le supervisor pose son callback `on_crash` une fois ; l'arch s'occupe d'installer le vrai handler ISA-specific qui démêle l'XtExcFrame / `mcause` / Cortex-M Fault status.
- [ ] **Extraire l'exception handler Xtensa de `supervisor.c`** vers `arch/xtensa_esp32s3/exception/exc_xtensa.c` : `xt_set_exception_handler`, `xt_exc_handler`, `XtExcFrame`, `EXCCAUSE_*`, `XCHAL_EXCCAUSE_NUM`, le helper `app_exception_handler` et son dispatch via PS.INTLEVEL. Supervisor.c garde uniquement la callback générique `on_crash` qui parle des slots (pas des frames CPU).
- [ ] **Mettre à jour `arch/xtensa_esp32s3/arch.cmake`** : ajouter `loader_reloc_xtensa.c` + `exception/exc_xtensa.c` à `DUNEOS_KERNEL_SRCS`. Idem pour `arch/riscv32/arch.cmake` lors de son remplissage (`reloc/loader_reloc_riscv.c` + `exception/exc_riscv.c`).
- [ ] **Validation** : `dbt flash kernel --build-only` sur CardPuter passe avec reloc + exception dans les fichiers arch, pas dans loader.c / supervisor.c. Test de crash (Phase 20 `test_hardening`) : l'app crash est toujours intercepté et le slot tué proprement.

**ESP32-C6 (RISC-V, RV32IMC)** — l'arch RISC-V est ensuite triviale à ajouter parce que loader.c est neutralisé :

- [ ] **`arch/riscv32/arch.cmake`** : Remplir — `DUNEOS_KERNEL_SRCS` (HAL + loader_reloc_riscv) + `DUNEOS_KERNEL_REQUIRES` ESP-IDF RISC-V. Guard : `DUNEOS_ARCH STREQUAL "riscv32"`.
- [ ] **`arch/riscv32/hal/hal_*.c`** : 6 fichiers HAL ESP32-C6 via ESP-IDF. APIs souvent identiques à Xtensa sauf ADC (unit/channel différents sur C6).
- [ ] **`arch/riscv32/reloc/loader_reloc_riscv.c`** : Relocations ELF RISC-V (`R_RISCV_32`, `R_RISCV_HI20`/`LO12`, `R_RISCV_CALL`, `R_RISCV_BRANCH`, `R_RISCV_JAL`). Implémente `duneos_arch_ops_t.apply_reloc`. Enregistré via `duneos_loader_register_arch_ops` au démarrage.
- [ ] **`tools/dbt/toolchain/esp_idf.py`** : Gérer `arch: riscv32` — préfixe compilateur `riscv32-esp-elf-gcc`, CFLAGS (`-march=rv32imc_zicsr_zifencei`, `-mabi=ilp32`).
- [ ] **`boards/esp32c6-devkitc/board.yaml`** + `bspgen` : Board RISC-V de référence.

**ESP32-P4 (RISC-V RV32IMA, dual-core HP + LP)** — s'ajoute sans nouvelle architecture :

- [ ] **`boards/esp32p4-devkitm/board.yaml`** : Le board existe déjà. Valider que `arch: riscv32` + même toolchain plugin couvrent ESP32-P4. Ajuster HAL si ESP-IDF expose des différences P4 (LP core, nouveau periph).
- [ ] **Smoke test** : `hello_world.dap` RISC-V sur C6 et P4. Le loader doit rejeter un `.dap` Xtensa avec un message clair.

> **Ce que cette phase prouve** : Un chip RISC-V Espressif = uniquement `board.yaml` nouveau. L'`arch/riscv32/` couvre C3, C6, H2, P4 — pas de duplication.

---

### Phase 28.5 — ESP-IDF de-coupling de l'entry point, du build root, et de dbt

**Pourquoi cette phase :** Phase 21 a sorti `build_kernel`/`flash_kernel`/`monitor` dans le plugin toolchain, mais cinq fronts ESP-IDF résiduels restent côté kernel et dbt. Aucun ne bloque Phase 28 (qui reste full ESP-IDF, juste un autre ISA). Tous bloquent Phase 29 (premier port hors ESP-IDF). Audit fait 2026-05-19.

**Front 1 — Entry point split (`main/` → `kernel/` + `arch/`) :**

- [ ] **Extraire la logique SDK-agnostic de `main/main.c`** vers `kernel/duneos_kernel/src/duneos_main.c` exposant `void duneos_main(void)` qui fait : `vfs_init` → `loader_init` → `supervisor_init` → `launch_from_init_yaml` → `wait_all` → `kernel_idle`. Pas d'`app_main`, pas de `console_init` USB JTAG.
- [ ] **`arch/xtensa_esp32s3/entry/entry_esp_idf.c`** : implémente `void app_main(void)` qui appelle `duneos_main()`. Inclut le `console_init()` USB JTAG actuel (`driver/usb_serial_jtag.h`) — sous `#ifndef CONFIG_ESP_CONSOLE_USB_CDC`.
- [ ] **`arch/<arch>/entry/entry_<sdk>.c`** pour les ports futurs : pico_sdk → `int main(void) { duneos_main(); return 0; }` ; stm32 → idem.
- [ ] **Supprimer `main/`** : son `main.c` est vide après extraction, son `CMakeLists.txt` (`idf_component_register` + REQUIRES `esp_driver_usb_serial_jtag`) déménage vers `arch/xtensa_esp32s3/entry/`.

**Front 2 — Build root SDK-agnostic :**

- [ ] **`CMakeLists.txt` racine** : aujourd'hui appelle directement `include($ENV{IDF_PATH}/tools/cmake/project.cmake)`. Doit dispatcher selon `board.yaml`'s `sdk:` field. Pattern : lire `.duneos_board` → lire `boards/<board>/board.yaml` → `include(tools/dbt/toolchain/<sdk>/setup.cmake)` qui prend en charge l'orchestration SDK-spécifique (project.cmake pour ESP-IDF, pico_sdk_init pour pico-sdk, etc.).
- [ ] **`sdkconfig.defaults` racine** : reste, mais documenté en tête comme "ESP-IDF Kconfig fragment — only consumed when sdk: esp-idf in board.yaml". Autres SDKs ont leur équivalent dans `tools/dbt/toolchain/<sdk>/defaults.{cmake,h,...}` selon ce qu'ils acceptent.

**Front 3 — Audit dbt findings :**

- [ ] **`tools/dbt/setup.py`** (61 refs ESP-IDF) : c'est un wizard d'install IDF (clone esp-idf, install.sh, export.sh, find_idf_root, build_idf_env, etc.). Toute la logique IDF-specific déménage en méthodes `plugin.setup_wizard()`, `plugin.find_toolchain_root()`, `plugin.build_env()`. `setup.py` devient un dispatcher qui appelle le plugin actif. La logique générique (`_BOARD_FILE`, `_PORT_FILE`, board/port pickers) reste.
- [ ] **`tools/dbt/tui.py`** (51 refs) : labels hard-codés `"ESP-IDF v6.0.x"`, `"IDF v6"`, `"echo ~/esp/esp-idf > .duneos_idf"`. Remplacer par `f"{plugin.SDK} {plugin.version()}"`, `f"echo … > .duneos_{plugin.SDK_SLUG}"`. Tous les `find_idf_root()` → `plugin.find_toolchain_root()`. La fonction privée `_idf_env()` / `_idf_cmd()` deviennent `_sdk_env()` / `_sdk_cmd()` déléguant au plugin.
- [ ] **`tools/dbt/flashimg.py`** (12 refs) : `_find_esptool()` + appel direct esptool pour flasher la partition sysbin. Sur RP2040 ce serait `picotool` ; le pattern est identique (image binary → partition offset → tool de flash). Déléguer : `plugin.flash_partition(image_path, partition_offset, partition_name, port)`. L'esp-idf plugin implémente avec esptool, le pico-sdk plugin avec picotool. `flashimg.py` reste générique : construit l'image LittleFS, lit l'offset depuis `partitions.csv`, appelle `plugin.flash_partition()`.
- [ ] **`tools/dbt/kernel.py`** (4 refs) : usage propre via `plugin.find_toolchain_root()` mais écrit `.duneos_idf` en dur (`_IDF_FILE.write_text(...)`). Remplacer par `plugin.write_toolchain_path_marker(root)` qui sait quel fichier per-SDK utiliser (`.duneos_idf` pour ESP-IDF, `.duneos_pico_sdk` pour pico-sdk).
- [ ] **`tools/dbt/cli.py`** (2 refs) : juste des help texts mentionnant `idf.py` et `idf_target.txt`. Mineurs ; à reformuler en termes SDK-agnostic.

**Front 4 — Plugin interface étendue** :

Ce que le plugin toolchain doit exposer après cette phase (en plus de ce qui existe déjà depuis Phase 21) :

```python
# Méthodes nouvelles exigées par les fronts 1-3
SDK_SLUG: str                                  # "esp_idf", "pico_sdk" — pour les fichiers .duneos_<slug>
def version() -> str: ...                      # "v6.0.1" — pour l'affichage TUI
def setup_wizard(console) -> int: ...          # Phase 21 partial; généraliser
def build_env() -> dict[str, str]: ...         # env vars pour appeler le build/flash subprocess
def write_toolchain_path_marker(root): ...     # écrit .duneos_<slug>
def flash_partition(image, offset, name, port) -> int: ...  # ce que flashimg.py délègue
def setup_cmake() -> Path: ...                 # le fichier que CMakeLists.txt racine include()
```

**Front 5 — Smoke test** :

- [ ] **`dbt flash kernel --build-only` reste vert** après les fronts 1-3 sur les 5 boards actuelles (CardPuter, T-Embed, DevKitC, ESP32-C3, ESP32-P4). Aucune régression.
- [ ] **Le rebuild Phase 29 RP2040 peut démarrer** sans toucher au code du kernel core ni à dbt — uniquement ajouter `tools/dbt/toolchain/pico_sdk.py` + `arch/arm_cortex_m/`.

> **Coût estimé** : 1 semaine de travail focus. Beaucoup de refactor mécanique (sed + grep), peu de design. La complexité réelle est dans le CMakeLists.txt racine dispatch (front 2) qui mélange deux build systems incompatibles.

---

### Phase 29 — Premier port non-ESP-IDF : ARM Cortex-M (RP2040, Pico W)

**Objectif : valider que le kernel se construit sans ESP-IDF.** C'est le vrai test de portabilité du build system. Requiert Phase 26 (OSAL + PicoLibc en `third_party/`) et Phase 27 (VFS natif si l'app cible utilise des sockets ; sinon ESP-IDF socket layer absent et c'est ok pour un premier hello_world).

**Décision architecture ARM Cortex-M** — à prendre avant de coder :

M0+ (RP2040), M4F (SAMD51, nRF52), M7 (STM32H750) partagent les **mêmes relocations ELF Thumb-2**. La différence est le HAL SoC. Structure retenue :

```
arch/arm_cortex_m/
    arch.cmake                   # guard: DUNEOS_ARCH matches "arm_cortex_m*"
    reloc/loader_reloc_arm.c     # unique pour tout ARM Thumb-2
    hal/
        rp2040/                  # HAL via pico-sdk
        stm32h7/                 # HAL via STM32CubeHAL (Phase 30+)
        nrf52/                   # HAL via nRF5 SDK (Phase 30+)
        samd51/                  # HAL via CMSIS/ASF4 (Phase 30+)
```

`arch.cmake` inclut `hal/${DUNEOS_BOARD_SOC}/hal_sources.cmake` pour sélectionner le bon sous-dossier HAL.

- [ ] **`duneos_kernel/CMakeLists.txt` — mode standalone** : Guard `if(DEFINED IDF_TARGET)` → `idf_component_register()` (existant). Sinon : `add_library(duneos_kernel STATIC ...)` + `target_sources()` + `target_include_directories()`. Les `DUNEOS_KERNEL_SRCS` déjà calculés restent valides dans les deux modes.
- [ ] **`tools/dbt/toolchain/pico_sdk.py`** : Plugin — `SDK = "pico-sdk"`, `ARCH = "arm_cortex_m"`, `SOC = "rp2040"`. `build_kernel()` via CMake standalone, `flash_kernel()` via `picotool`, `DUNEOS_ARCH = arm_cortex_m`, `DUNEOS_BOARD_SOC = rp2040`.
- [ ] **`arch/arm_cortex_m/arch.cmake`** : Guard `DUNEOS_ARCH STREQUAL "arm_cortex_m"`. Inclut `loader_reloc_arm.c`. Inclut `hal/${DUNEOS_BOARD_SOC}/hal_sources.cmake`.
- [ ] **`arch/arm_cortex_m/reloc/loader_reloc_arm.c`** : Relocations ELF ARM Thumb-2 (`R_ARM_THM_CALL`, `R_ARM_ABS32`, `R_ARM_THM_MOVW_ABS_NC`, `R_ARM_THM_MOVT_ABS`). Partagé par M0+/M4/M7.
- [ ] **`arch/arm_cortex_m/hal/rp2040/hal_*.c`** : 6 fichiers HAL RP2040 via pico-sdk.
- [ ] **`boards/rp2040-pico/board.yaml`** : `arch: arm_cortex_m`, `sdk: pico-sdk`, `soc: rp2040`.
- [ ] **`boards/rp2040-pico-w/board.yaml`** : Identique + section WiFi (CYW43439 via pico_cyw43_arch en userspace). WiFi = app ouvre `/dev/spi-1` + pilote CYW43 en userspace, pas dans le kernel.
- [ ] **PicoLibc bundlée** : Vérifier que PicoLibc de `third_party/` compile pour ARM Cortex-M0+ et fournit des syscall stubs cohérents.
- [ ] **`dbt sim`** : Compiler le kernel pour Linux x86_64 via `pthread_osal.c` + PicoLibc x86. Smoke test : `hello_world.dap` pour x86 dans le simulateur.

> **Ce que cette phase prouve** : DuneOS est un OS portable. Les phases suivantes (STM32H750, nRF52-DK, Feather M4) n'ajoutent que `hal/<soc>/` + `board.yaml` — le pattern est établi.

**Cibles ARM futures (post Phase 29, même pattern)** :
- *STM32H750* : `stm32_hal.py` plugin, `arch/arm_cortex_m/hal/stm32h7/`, STM32CubeH7 HAL, `arm-none-eabi-gcc -mcpu=cortex-m7`.
- *nRF52-DK (nRF52840)* : `nrf5_sdk.py` plugin, `arch/arm_cortex_m/hal/nrf52/`. ⚠️ nRF52832 = 64 KB SRAM — trop juste pour le loader ELF dynamique ; nRF52840 (256 KB) est la cible.
- *Feather M4 (SAMD51)* : Pas de SDK dominant avec CMake propre — ASF4 ou CMSIS nu. Le plus difficile : nécessite un `samd51_cmsis.py` plugin avec startup/linker scripts manuels.

---

### Phase 30 — Audio et Multimedia

- [ ] **`/sd/board.info`** étendu aux capacités audio (présence I2S, codec).
- [ ] **`/dev/pcm`** : Pilote ALSA-lite.
- [ ] **Daemon de mixage** : Service système pour jouer plusieurs sons simultanément.
- [ ] **SDK** : `libwav.a`, `libsynth.a`.

---

### Phase 31 — Énergie et Optimisation

- [ ] **Wake Locks** : API applicative (`PM_LOCK_CPU`, `PM_LOCK_DISPLAY`). Le kernel plonge en Deep Sleep quand toutes les locks sont relâchées.
- [ ] **Input ISR** : Réécrire le scan clavier et encodeurs pour utiliser des interruptions matérielles au lieu du polling.

---

### Phase 32 — Sécurité et Écosystème

- [ ] **`/dev/crypto`** : Exposer les accélérateurs AES/SHA/TRNG via syscalls.
- [ ] **Signatures Ed25519** : Vérification asymétrique des `.dap` par le loader avant exécution.
- [ ] **App Store CLI** : `dbt system install author/repo` — télécharger et intégrer des apps communautaires dans un `system.yaml`.

---

### Phase 33 — Recherche Future

- [ ] **Shared Libraries (`.dsl`)** : Étude de faisabilité des librairies partagées (PIC/GOT) si le besoin de RAM mutualisée devient critique face à la simplicité du lien statique.
- [ ] **Support Multi-Core (SMP)** : Exploitation du second cœur sur ESP32-S3 et P4.
