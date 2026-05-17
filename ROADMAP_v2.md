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

### Phase 17 — Tooling DX (DX First)

Ne plus coder d'outils jetables. Préparer le terrain pour de multiples architectures sans toucher à CMake.

- [ ] **Découpage de `dbt.py`** : Transformer le monolithe en sous-modules (`tools/dbt/cli.py`, `builder.py`, `deploy.py`, `toolchain.py`).
- [ ] **`duneos.yaml` applicatif** : Remplacer `manifest.json`. Champs : `stack`, `heap`, `permissions`, `sources`. Rétrocompatibilité `manifest.json` maintenue une phase.
- [ ] **`init.yaml`** : Migrer `/sd/init.json` vers `/sd/init.yaml`. Parser YAML au lieu de cJSON dans `init.c`.
- [ ] **`bspgen.py` universel** : Supprimer les dépendances ESP-IDF ; générer `board_config.h` pur C sans `esp_err_t`.

> **Simulateur natif** (`dbt.py run --sim`) — déféré après la Phase 19, nécessite une phase dédiée.

---

### Phase 18 — libgfx (Portabilité display)

Une app qui dessine doit compiler et tourner identiquement sur CardPuter (ST7789, pas de PSRAM) et T-Embed (ST7789, PSRAM, `/dev/fb0`) sans `#ifdef` dans le code source.

- [ ] **`/sd/board.info`** : Fichier YAML écrit par le noyau au boot depuis `board_config.h` (champs : `board`, `display`, `width`, `height`, `fb`).
- [ ] **`libgfx.h`** : API publique — `gfx_open/close/fill/pixel/text/flush/get_info`.
- [ ] **`gfx_st7789.c`** : Backend Tier A (SPI direct, wraps `libst7789.c`).
- [ ] **`gfx_fb.c`** : Backend Tier B (wraps `/dev/fb0`, boards PSRAM seulement).
- [ ] **`dbt.py build`** : Sélection automatique du backend `libgfx` depuis `.duneos_board`.
- [ ] **Demo app** `gfx_demo.dap` — dessine formes + texte, tourne sur toutes les boards sans recompilation du source.

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

---

### Phase 20 — Hardening mémoire + Dette technique

Garantir qu'une application ne peut pas crasher le système. Purger la dette technique accumulée.

- [ ] **Stack canary** par task applicative.
- [ ] **Per-app exception handler** : Intercepter le crash → logger dans `/dev/klog` → unload propre sans reboot du kernel.
- [ ] **Task WDT** : Le supervisor nourrit le WDT pour l'app ; kick de l'app en cas de timeout.
- [ ] **Pools RAM monolithiques** : Un bloc contigu unique par app (Code + Data + BSS + Stack + Heap). Fin de la fragmentation du tas noyau.
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

### Phase 22 — Syscalls et Migration PicoLibc

Vitesse et légèreté : fiabiliser l'ABI.

- [ ] **Table de vecteurs syscall** : Remplacer la recherche par chaîne dans `symbols.c` par un tableau de pointeurs de fonctions passé à l'app au démarrage. Résolution O(1).
- [ ] **Migration PicoLibc** : Abandonner Newlib au profit de PicoLibc (conçu pour l'embarqué, TLS natif).
- [ ] **`libdune.a`** : Archive statique wrappant les appels POSIX (`open`, `read`, `malloc`) vers les bons indices de la table de syscalls.

---

### Phase 23 — The Flipper DX (USB Device Subsystem)

L'expérience Plug and Play ultime.

- [ ] **TinyUSB** : Brancher la stack USB open-source sur la HAL DuneOS.
- [ ] **USB MSC (Mass Storage)** : Exposer `/sd` ou `/flash` au PC. Glisser-déposer `.dap` comme sur une clé USB.
- [ ] **USB CDC (Console)** : Exposer le shell sur un port série virtuel USB, libérant l'UART physique.

---

### Phase 24 — Refonte VFS et Stabilisation Réseau

Préparer la stack réseau avant d'affronter le découplage WiFi.

- [ ] **VFS natif (`duneos_vfs`)** : Remplacer `esp_vfs` pour gérer nativement `poll()`, `select()` et les sockets.
- [ ] **Ethernet RMII Lab** : Intégrer LwIP (ou PicoTCP) nativement via RMII sans dépendre du blob WiFi.
- [ ] **VFS Sockets** : Router les appels réseau BSD vers la nouvelle stack interne.

---

### Phase 25 — L'Émancipation (OSAL et WiFi)

Le boss final : se passer du scheduler FreeRTOS tout en gardant la radio.

- [ ] **Scheduler DuneOS** : Ordonnanceur POSIX préemptif maison (ticks, context switch, priorités).
- [ ] **OSAL FreeRTOS** : Couche implémentant les APIs FreeRTOS (`xTaskCreate`, `xQueueSend`) qui redirigent vers les primitives DuneOS.
- [ ] **Confinement du Blob** : Lier les blobs fermés Espressif (WiFi/BT) contre l'OSAL. Le blob WiFi croit tourner sur FreeRTOS, mais passe ses trames L2 à la stack réseau de la Phase 24.

---

### Phase 26 — Audio et Multimedia

- [ ] **`/sd/board.info`** étendu aux capacités audio (présence I2S, codec).
- [ ] **`/dev/pcm`** : Pilote ALSA-lite.
- [ ] **Daemon de mixage** : Service système pour jouer plusieurs sons simultanément.
- [ ] **SDK** : `libwav.a`, `libsynth.a`.

---

### Phase 27 — Énergie et Optimisation

- [ ] **Wake Locks** : API applicative (`PM_LOCK_CPU`, `PM_LOCK_DISPLAY`). Le kernel plonge en Deep Sleep quand toutes les locks sont relâchées.
- [ ] **Input ISR** : Réécrire le scan clavier et encodeurs pour utiliser des interruptions matérielles au lieu du polling.

---

### Phase 28 — Sécurité et Écosystème

- [ ] **`/dev/crypto`** : Exposer les accélérateurs AES/SHA/TRNG via syscalls.
- [ ] **Signatures Ed25519** : Vérification asymétrique des `.dap` par le loader avant exécution.
- [ ] **App Store CLI** : `dbt.py install author/repo` — télécharger et lier des librairies/apps communautaires.

---

### Phase 29 — Recherche Future

- [ ] **Shared Libraries (`.dsl`)** : Étude de faisabilité des librairies partagées (PIC/GOT) si le besoin de RAM mutualisée devient critique face à la simplicité du lien statique.
- [ ] **Support Multi-Core (SMP)** : Exploitation du second cœur sur ESP32-S3 et P4.
- [ ] **Simulateur natif** : `dbt.py run --sim` — compiler le noyau pour Linux/macOS (VFS mocké, SDL pour l'écran).
