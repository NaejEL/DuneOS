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

### Phase 24 — DHI (DuneOS Hardware Interface)

Isoler le noyau pour amorcer la sortie du framework Espressif. Objectif concret : aucun `esp_err_t` ni type propriétaire dans les headers publics des drivers.

- [ ] **Headers DHI** : Définir `hal_uart.h`, `hal_gpio.h`, `hal_i2c.h`, `hal_spi.h` avec des types purs (`uint32_t`, `int`, callbacks C standards).
- [ ] **Migration des backends** : Réécrire les implémentations de `drv_uart.c`, `drv_gpio.c`, `drv_i2c.c`, `drv_spi.c` pour n'utiliser les types ESP-IDF qu'*en interne*.
- [ ] **Abstraction des interruptions** : Callbacks C standards enregistrés par le kernel, mappés par `arch/xtensa/` ou `arch/riscv/` sur l'allocateur matériel ESP-IDF.
- [ ] **Arch dans le manifest** : Champ `arch` dans `duneos_app_manifest_t` (`abi.h`). Le loader vérifie `app.arch == kernel_arch` au chargement — rejection propre avec message klog au lieu d'un crash silencieux sur mauvaise ISA.

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

- [ ] **VFS natif (`duneos_vfs`)** : Remplacer `esp_vfs` pour gérer nativement `poll()`, `select()` et les sockets.
- [ ] **Ethernet RMII Lab** : Intégrer LwIP (ou PicoTCP) nativement via RMII sans dépendre du blob WiFi.
- [ ] **VFS Sockets** : Router les appels réseau BSD vers la nouvelle stack interne.

---

### Phase 27 — OSAL et Portabilité Scheduler

Abstraire FreeRTOS derrière une interface propre. **DuneOS ne réimplémente pas de scheduler** — FreeRTOS reste le scheduler sur toutes les targets qui le supportent. L'OSAL permet aux targets sans FreeRTOS natif (simulateur Linux, architectures futures) d'utiliser une implémentation alternative.

- [ ] **`duneos_osal.h`** : Interface pure (`osal_task_create`, `osal_queue_send`, `osal_mutex_lock`, `osal_sem_post`…). Aucun type FreeRTOS dans les headers kernel publics après cette phase.
- [ ] **`freertos_osal.c`** : Implémentation de `duneos_osal.h` pour toutes les platforms FreeRTOS (ESP32-S3 Xtensa, ESP32-C6 RISC-V, RP2040…). Un seul fichier réutilisé à travers les toolchain plugins.
- [ ] **`pthread_osal.c`** : Implémentation de `duneos_osal.h` via pthreads — permet `dbt sim` (simulateur natif Linux/macOS).
- [ ] **Confinement du Blob WiFi** : Isoler la dépendance du blob WiFi Espressif derrière `freertos_osal.c`.

---

### Phase 28 — Audio et Multimedia

- [ ] **`/sd/board.info`** étendu aux capacités audio (présence I2S, codec).
- [ ] **`/dev/pcm`** : Pilote ALSA-lite.
- [ ] **Daemon de mixage** : Service système pour jouer plusieurs sons simultanément.
- [ ] **SDK** : `libwav.a`, `libsynth.a`.

---

### Phase 29 — Énergie et Optimisation

- [ ] **Wake Locks** : API applicative (`PM_LOCK_CPU`, `PM_LOCK_DISPLAY`). Le kernel plonge en Deep Sleep quand toutes les locks sont relâchées.
- [ ] **Input ISR** : Réécrire le scan clavier et encodeurs pour utiliser des interruptions matérielles au lieu du polling.

---

### Phase 30 — Sécurité et Écosystème

- [ ] **`/dev/crypto`** : Exposer les accélérateurs AES/SHA/TRNG via syscalls.
- [ ] **Signatures Ed25519** : Vérification asymétrique des `.dap` par le loader avant exécution.
- [ ] **App Store CLI** : `dbt system install author/repo` — télécharger et intégrer des apps communautaires dans un `system.yaml`.

---

### Phase 31 — Recherche Future

- [ ] **Shared Libraries (`.dsl`)** : Étude de faisabilité des librairies partagées (PIC/GOT) si le besoin de RAM mutualisée devient critique face à la simplicité du lien statique.
- [ ] **Support Multi-Core (SMP)** : Exploitation du second cœur sur ESP32-S3 et P4.
- [ ] **Simulateur natif** : `dbt sim` — compiler le noyau pour Linux/macOS via `pthread_osal.c` (débloqué par Phase 27).
