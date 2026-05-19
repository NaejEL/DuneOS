# DuneOS Roadmap V2

## 1. Le Manifeste

NuttX et Zephyr offrent une conformité POSIX incroyable, mais au prix d'une courbe d'apprentissage brutale (enfer des Kconfig, Device Trees complexes). DuneOS vise le Sweet Spot : La puissance d'un RTOS POSIX avec l'expérience développeur (DX) fluide d'une console comme la Playdate ou le Flipper Zero.

L'utilisateur ne doit jamais toucher à CMake ou aux internals du noyau. Un simple fichier YAML et du code C doivent suffire pour compiler, lier et déployer une application dynamiquement sur n'importe quel matériel supporté.

---

## 2. Héritage du POC (Phases 1 à 16 validées)

DuneOS n'est plus une simple idée — les fondations ont été prouvées sur le hardware (ESP32-S3, M5Stack, LilyGo T-Embed) :

- **Exécution et ABI** : Loader ELF (ET_REL) Xtensa/RISC-V fonctionnel, cycle de vie (load/run/unload), résolution POSIX par table de symboles.
- **VFS et Périphériques** : Montage SD (FatFS), tmpfs en RAM, `/dev/uart0`, `/dev/gpiochip0`, `/dev/i2c-0`, `/dev/spi-1`, framebuffer `/dev/fb0`, events `/dev/input/event0`.
- **Display** : `/dev/disp0` streaming driver + `/dev/fb0` PSRAM back-buffer (T-Embed); `libst7789.c` userspace SDK.
- **Écosystème** : Shell interactif modulaire (`system/bin/`), tooling (`dbt.py`, `bspgen.py`), init system (`/sd/init.yaml`), politiques de restart.
- **Réseau** : Démon WiFi, `duneos_netif_wait_ip()`, injection de trames brutes 802.11 (`/dev/raw80211`), exports BSD socket complets.

**Objectif V2** : Transformer ce POC fortement couplé à ESP-IDF/FreeRTOS en un véritable OS indépendant, sécurisé sans MMU, avec un outillage de classe mondiale.

---

## 3. Architecture Cible

```text
DuneOS/
├── apps/                 (Applications tierces .dap téléchargeables)
├── system/               (Utilitaires système standard : shell, wifi_daemon)
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
- [x] **`libgfx.h`** : API publique — `gfx_open/close/fill/pixel/text/flush/get_info`.
- [x] **`gfx_st7789.c`** : Backend Tier A (SPI direct, wraps `libst7789.c`).
- [x] **`gfx_fb.c`** : Backend Tier B (wraps `/dev/fb0`, boards PSRAM seulement).
- [x] **`dbt.py build`** : Sélection automatique du backend `libgfx` depuis `.duneos_board`.
- [x] **Demo app** `gfx_demo.dap` — dessine formes + texte, tourne sur toutes les boards sans recompilation du source.

---

### Phase 19 — Flash storage (Boot sans SD) ✅

DuneOS doit booter et être utilisable même sans carte SD insérée.

- [x] **Partition `sysbin` LittleFS** dans `partitions.csv` (~1 MB) ; monter sur `/flash` dans `vfs.c`.
- [x] **Embed des apps vitales** (`shell.dap`, commandes `system/bin/`) en blobs firmware via `COMPONENT_EMBED_FILES`.
- [x] **First-boot provisioning** : Le noyau copie les blobs manquants vers `/flash/bin/` au premier démarrage.
- [x] **Cascade loader** : Chercher `/flash/bin/` → `/sd/bin/` → `/sd/apps/`.
- [x] **BSP YAML** : Champ `has_sd: false` ; `vfs.c` skip le montage SD et lit `init.yaml` depuis `/flash`.
- [x] **`dbt.py flashimg`** : Produit une image LittleFS flashable directement via `esptool` (port depuis `.duneos_port` / `--port` / `DUNEOS_PORT`).
- [x] **`bspgen.py`** : Génère `partitions.csv` par board depuis `flash_size_mb` ; `sdkconfig.board` remplace les `sdkconfig.defaults` manuscrits.
- [x] **Init dedup par nom d'app** : `init.c` déduplique les services par nom (basename sans `.dap`) — évite le double-lancement quand un même service apparaît dans `/flash/init.yaml` et `/sd/init.yaml`. L'entrée flash gagne (chargée en premier).
- [x] **`boards/<board>/init.yaml`** : Fichier par board versionné dans le repo, contrôlé par l'utilisateur. `dbt flashimg` le copie tel quel (fini la liste hardcodée `_BOOT_SERVICES`). Éditable via TUI (`i` → Init Config) : sélection des apps + politique de restart (`always`/`on-failure`/`no`).

---

### Phase 20 — Hardening mémoire + Dette technique

Garantir qu'une application ne peut pas crasher le système. Purger la dette technique accumulée.

- [ ] **Stack canary** par task applicative.
- [x] **Per-app exception handler** : Intercepter le crash → logger dans `/dev/klog` → unload propre sans reboot du kernel.
- [x] **Task WDT** : Le supervisor nourrit le WDT pour l'app ; kick de l'app en cas de timeout.
- [x] **Per-app heap** : Pool DRAM dédié par app via `heap_caps_malloc` (bloc contigu Code+Data+Heap). Bloc monolithique TLSF différé Phase 22.
- [ ] **TLSF userspace allocator** : `libdune.a` gère son propre `malloc()` uniquement dans ce pool.
- [ ] **Validation syscall** : Le noyau vérifie que les pointeurs d'arguments (read/write buffers) appartiennent à la zone mémoire autorisée de l'app.

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

- [x] **`duneos_api_t` — table d'API typée (ABI v3)** : `components/duneos_kernel/include/duneos/api.h` définit une struct de pointeurs de fonctions couvrant `fs`, `mem`, `thread`, `time`, `sys`. Le loader injecte l'adresse du singleton noyau dans le symbole `__duneos_api_ptr` de l'app avant d'appeler `app_main`. Résolution O(1), nul besoin de recherche par chaîne.
- [x] **`DUNEOS_ABI_VERSION` → 3** : `abi.h` bumped ; compatibilité amont garantie (apps v1/v2 sans `__duneos_api_ptr` utilisent toujours la table de symboles `duneos_symbol_table_get()`).
- [x] **`components/duneos_kernel/src/api.c`** : Instance statique `s_api` + `duneos_api_get()`. Wrappers minces pour `read` (check_app_writable_ptr), `write` (check_user_ptr), `dup`/`dup2`, `dprintf`, `opendir`/`readdir`/`closedir`, loader et IPC via `void *` (évite la dépendance circulaire noyau↔loader).
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
- [x] **USB CDC (Console)** : `drv_usb_cdc.c` — TX mutex-sérialisé (un seul `write_flush` non-bloquant, pas de collision), RX via ring buffer + sémaphore → `/dev/ttyUSB0`. `system/usb_shell/` + `system/shell_core/` remplacent `system/shell/`.
- [x] **`console: none`** : `bspgen.py` émet `CONFIG_ESP_CONSOLE_NONE=y` pour les boards OTG — UART0 reste libre pour les apps. Le klog est redirigé vers CDC à l'exécution via `esp_log_set_vprintf`.

---

### Phase 24 — DHI (DuneOS Hardware Interface) ✅ DONE

Isoler le noyau pour amorcer la sortie du framework Espressif. Objectif concret : aucun `esp_err_t` ni type propriétaire dans les headers publics des drivers.

- [x] **Headers DHI** : `hal_uart.h`, `hal_gpio.h`, `hal_i2c.h`, `hal_spi.h`, `hal_adc.h`, `hal_time.h` dans `components/duneos_kernel/include/duneos/` — types purs (`uint32_t`, `int`, callbacks C standards). Aucune dépendance ESP-IDF dans les headers publics.
- [x] **Implémentations ESP-IDF** : `arch/xtensa_esp32s3/hal/hal_uart.c`, `hal_gpio.c`, `hal_i2c.c`, `hal_spi.c`, `hal_adc.c`, `hal_time.c` — ESP-IDF types **uniquement en interne** (jamais dans les headers). Compilés conditionnellement via `CONFIG_IDF_TARGET_ARCH_XTENSA` dans `CMakeLists.txt`.
- [x] **Migration des backends** : `drv_uart.c`, `drv_gpio.c`, `drv_i2c.c`, `drv_spi.c`, `i2c_bus.c`, `drv_battery_adc_simple.c` réécrits pour déléguer au HAL. Drivers input (`btn_gpio.c`, `enc_quadrature.c`, `kb_iomatrix.c`) migrés de `driver/gpio.h` + `esp_rom_delay_us` vers `hal_gpio` + `hal_time`. `dev_driver.h` et `i2c_bus.h` purgés de `esp_err_t` (retours `int`). Tous les callbacks `init()` retournent `int`.
- [x] **Abstraction des interruptions** : `duneos_hal_gpio_set_intr()` disponible pour usage kernel-interne. `GPIOCHIP_SET_IRQ` retourne `ENOSYS` — la livraison userspace via signal n'est pas encore conçue (Phase 26).
- [x] **Arch dans le manifest** : Champ `arch[32]` dans `duneos_app_manifest_t` (`abi.h`). `loader.c` parse le champ JSON et rejette proprement les `.dap` cross-ISA avec un message klog. `dbt builder.py` injecte `arch` depuis le plugin toolchain. Ancien manifest sans `arch` accepté (rétrocompatibilité).
- [x] **SPI host IDs numériques** : `bspgen.py` émet des valeurs entières (`spi_host_device_t` enum values) au lieu de noms ESP-IDF (`SPI2_HOST`). ADC unit IDs également numériques (1=ADC1, 2=ADC2). Tous les `board_config.h` régénérés.

**Périmètre exact de la Phase 24** — L'objectif est : *aucun `esp_err_t` ni type propriétaire dans les **headers publics** (`include/duneos/`)*. Cela ne couvre PAS tout le code source. Les includes ESP-IDF dans les `.c` privés restent jusqu'à leur phase cible :

- `supervisor.c` utilise `esp_rom_printf` (pas `esp_rom_delay_us`) dans les handlers exception/WDT/stack-overflow — c'est un output de dernier recours qui fonctionne quand le VFS/UART est mort. **Ne pas remplacer par `klog_e()`** (risque de crash dans un contexte crashé). Phase 27 : `osal_panic_print()`.
- `vfs.c`, `st7789_hw.c` : `driver/spi_master.h` → Phase 26 (VFS natif + hal_spi migration).
- `supervisor.c`, `task.c`, `symbols.c`, `klog.c` : `freertos/*.h` → Phase 27 (OSAL).

**Dette HAL connue** — adressée dans les phases suivantes (aucun include ESP-IDF "hardware" dans les headers publics, mais les implémentations restent à migrer) : `vfs.c` + `st7789_hw.c` (Phase 26), `drv_wifi.c` + `drv_raw80211.c` (Phase 26), `drv_fb_st7789.c` + noyau FreeRTOS (Phase 27). Voir les sections Phase 26 et Phase 27 pour le détail fichier par fichier.

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

### Phase 26 — Refonte VFS et Stabilisation Réseau

Préparer la stack réseau avant d'affronter le découplage WiFi.

**Migrations DHI** — purge des includes ESP-IDF hardware restants (seuls `freertos/*.h` et `esp_vfs.h` sont reportés à Phase 27) :

- [ ] **`vfs.c` init SD** : Remplacer `driver/spi_master.h` + `driver/gpio.h` (init bus SPI de la carte SD) par `hal_spi.h` + `hal_gpio.h` — les implémentations existent déjà, migration seule.
- [ ] **`display/st7789_hw.c/.h`** : Remplacer `driver/spi_master.h` + `esp_err.h` par `hal_spi.h` — types `spi_device_handle_t` encapsulés dans l'implémentation, header public purgé.
- [ ] **`hal_net.h`** : Créer `components/duneos_kernel/include/duneos/hal_net.h` — interface pure C pour WiFi/Ethernet (`duneos_hal_net_sta_connect`, `duneos_hal_net_get_ip`, callbacks d'événements). Implémenter dans `arch/xtensa_esp32s3/hal/hal_net.c` avec `esp_wifi.h`, `esp_netif.h`, `esp_event.h`.
- [ ] **`drv_wifi.c`, `drv_raw80211.c`** : Migrer de `esp_wifi.h` + `esp_netif.h` + `esp_event.h` directs vers `hal_net.h`.
- [ ] **VFS natif (`duneos_vfs`)** : Remplacer `esp_vfs.h` dans `vfs.c`, `vfs_dev.c`, `vfs_tmp.c` — nécessaire pour gérer nativement `poll()`, `select()` et les sockets. Libère la dépendance sur `esp_vfs_register` et `esp_vfs_fat_*`.
- [ ] **Ethernet RMII Lab** : Intégrer LwIP (ou PicoTCP) nativement via RMII sans dépendre du blob WiFi. (implique l'ajout de l'arch esp32, le RMII n'étant pas disponible sur esp32s3)
- [ ] **VFS Sockets** : Router les appels réseau BSD vers la nouvelle stack interne.

---

### Phase 27 — OSAL et Portabilité Scheduler

Abstraire FreeRTOS derrière une interface propre. **DuneOS ne réimplémente pas de scheduler** — FreeRTOS reste le scheduler sur toutes les targets qui le supportent. L'OSAL permet aux targets sans FreeRTOS natif (simulateur Linux, architectures futures) d'utiliser une implémentation alternative.

- [ ] **`duneos_osal.h`** : Interface pure (`osal_task_create`, `osal_queue_send`, `osal_mutex_lock`, `osal_sem_post`, `osal_malloc`, `osal_free`…). Aucun type FreeRTOS dans les headers kernel publics après cette phase.
- [ ] **`freertos_osal.c`** : Implémentation de `duneos_osal.h` pour toutes les platforms FreeRTOS (ESP32-S3 Xtensa, ESP32-C6 RISC-V, RP2040…). Un seul fichier réutilisé à travers les toolchain plugins.
- [ ] **`pthread_osal.c`** : Implémentation de `duneos_osal.h` via pthreads — permet `dbt sim` (simulateur natif Linux/macOS). Prérequis : PicoLibc en `third_party/` (voir ci-dessous).
- [ ] **Confinement du Blob WiFi** : Isoler la dépendance du blob WiFi Espressif derrière `freertos_osal.c`.

**Migrations DHI** — dernière purge des includes framework dans le cœur du kernel :

- [ ] **`supervisor.c`, `task.c`, `symbols.c`, `klog.c`, `api.c`** : Remplacer tous les `freertos/*.h` + `esp_heap_caps.h` + `esp_system.h` par les primitives `duneos_osal.h`. C'est la migration la plus large — couvre la création de tasks, les queues/mutexes/sémaphores, l'allocation mémoire interne.
- [ ] **`esp_rom_printf` → `osal_panic_print()`** : `supervisor.c` utilise `esp_rom_printf` dans les handlers exception/WDT/stack-overflow (output de dernier recours quand le VFS est mort). Abstraire derrière `osal_panic_print()` — implémenté par `esp_rom_printf` sur ESP32, `fprintf(stderr,...)` sur le simulateur Linux.
- [ ] **`vfs_dev.c`, `vfs_tmp.c`** : Remplacer `freertos/*.h` (ring buffers, semaphores) par `duneos_osal.h`. `esp_vfs.h` déjà supprimé en Phase 26.
- [ ] **`drv_fb_st7789.c`** : Remplacer `esp_heap_caps.h` (allocation PSRAM) par `osal_mem_alloc_caps()` ou équivalent `duneos_osal.h` — nécessite que l'OSAL mémoire expose des capacités (SPIRAM, DMA-capable).

**Prérequis Libc — PicoLibc en `third_party/`** :

Jusqu'à cette phase, c'est `esp_libc` (composant ESP-IDF) qui fournit PicoLibc + ses stubs syscall. Dès que `pthread_osal.c` est implémenté pour le simulateur Linux, il n'y a plus d'ESP-IDF pour fournir la libc. C'est le **point de déclenchement obligatoire** pour bundler PicoLibc :

- [ ] **`third_party/picolibc`** : Ajouter PicoLibc comme git submodule (même stratégie que cJSON/LittleFS). Compilé pour la target courante par le toolchain plugin.
- [ ] **`tools/dbt/toolchain/esp_idf.py`** : Retirer la dépendance implicite sur `esp_libc` — passer l'include path et le linker script PicoLibc explicitement via le plugin.
- [ ] **Cohérence multi-SDK** : pico-sdk fournit newlib (pas PicoLibc) — comportements `stdio`/`errno` légèrement différents. Utiliser la PicoLibc bundled sur **toutes** les targets garantit un comportement stdlib identique. Décision à prendre au moment du premier port non-ESP-IDF (Phase 29).

---

### Phase 28 — Ports RISC-V Espressif : ESP32-C6 + ESP32-P4

**Objectif : valider que `arch/*/arch.cmake` fonctionne réellement sur un second ISA.** ESP32-C6 et ESP32-P4 sont RISC-V mais restent sous ESP-IDF — même build system, nouvelle arch. Si quelque chose casse, c'est une lacune dans l'abstraction, pas dans le tooling.

**Unification des guards `arch.cmake`** — problème design à corriger en premier :

- [ ] **Variable `DUNEOS_ARCH`** : Introduire `DUNEOS_ARCH` comme variable CMake définie par le toolchain plugin (ESP-IDF : depuis `CONFIG_IDF_TARGET_ARCH_*` ; non-ESP-IDF : passée explicitement par le SDK plugin). Chaque `arch.cmake` vérifie `DUNEOS_ARCH` **en plus** de la variable ESP-IDF. Exemple : `if(NOT CONFIG_IDF_TARGET_ARCH_XTENSA AND NOT DUNEOS_ARCH STREQUAL "xtensa_esp32s3") return() endif()`. Sans ce changement, les guards ne fonctionnent pas hors ESP-IDF.
- [ ] **`arch/xtensa_esp32s3/arch.cmake`** : Mettre à jour le guard pour vérifier `DUNEOS_ARCH` en fallback.

**ESP32-C6 (RISC-V, RV32IMC)** :

- [ ] **`arch/riscv32/arch.cmake`** : Remplir — `DUNEOS_KERNEL_SRCS` + `DUNEOS_KERNEL_REQUIRES` ESP-IDF RISC-V. Guard : `DUNEOS_ARCH STREQUAL "riscv32"`.
- [ ] **`arch/riscv32/hal/hal_*.c`** : 6 fichiers HAL ESP32-C6 via ESP-IDF. APIs souvent identiques à Xtensa sauf ADC (unit/channel différents sur C6).
- [ ] **`arch/riscv32/reloc/loader_reloc_riscv.c`** : Relocations ELF RISC-V (`R_RISCV_32`, `R_RISCV_HI20`/`LO12`, `R_RISCV_CALL`). Exigé par le loader pour charger des `.dap` RISC-V.
- [ ] **`tools/dbt/toolchain/esp_idf.py`** : Gérer `arch: riscv32` — préfixe compilateur `riscv32-esp-elf-gcc`, CFLAGS (`-march=rv32imc_zicsr_zifencei`, `-mabi=ilp32`).
- [ ] **`boards/esp32c6-devkitc/board.yaml`** + `bspgen` : Board RISC-V de référence.

**ESP32-P4 (RISC-V RV32IMA, dual-core HP + LP)** — s'ajoute sans nouvelle architecture :

- [ ] **`boards/esp32p4-devkitm/board.yaml`** : Le board existe déjà. Valider que `arch: riscv32` + même toolchain plugin couvrent ESP32-P4. Ajuster HAL si ESP-IDF expose des différences P4 (LP core, nouveau periph).
- [ ] **Smoke test** : `hello_world.dap` RISC-V sur C6 et P4. Le loader doit rejeter un `.dap` Xtensa avec un message clair.

> **Ce que cette phase prouve** : Un chip RISC-V Espressif = uniquement `board.yaml` nouveau. L'`arch/riscv32/` couvre C3, C6, H2, P4 — pas de duplication.

---

### Phase 29 — Premier port non-ESP-IDF : ARM Cortex-M (RP2040, Pico W)

**Objectif : valider que le kernel se construit sans ESP-IDF.** C'est le vrai test de portabilité du build system. Requiert Phase 27 (OSAL + PicoLibc en `third_party/`).

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
