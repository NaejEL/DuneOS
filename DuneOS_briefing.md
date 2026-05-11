# DuneOS — Briefing projet pour Claude Code

## Vue d'ensemble

**DuneOS** est un système d'exploitation minimaliste pour la famille ESP32, construit sur ESP-IDF.
Objectifs principaux :
- Charger dynamiquement des applications (`.elf`) depuis une carte SD à l'exécution (comme les `.fap` du Flipper Zero)
- Exposer une couche POSIX partielle (fichiers, threads, time) via newlib + VFS ESP-IDF
- Configuration de board déclarative (YAML → header C généré), inspirée de Zephyr Device Tree

**Board de développement actuelle :** M5Stack CardPuter (ESP32-S3FN8 — 8 Mo flash embarquée, **pas de PSRAM**).
**Board de référence :** ESP32-S3-DevKitC (avec PSRAM OPI).
**Toolchain :** ESP-IDF v5.5.1, CMake, `xtensa-esp32s3-elf-gcc`.

---

## État actuel — Phase 1 TERMINÉE ✅

**Premier boot réussi le 3 mai 2026 sur M5Stack CardPuter.**

```
I duneos: DuneOS 0.1.0 (ABI v1)
I duneos/vfs: SD mounted at /sd — 1.9 GB
I duneos/loader: manifest: 'test_exit' v0.1.0 (ABI>=1)
I duneos/loader: scan: 1 app(s) found in /sd/apps
I duneos/loader: autoboot: 'test_exit'
I duneos: launching 'test_exit' v0.1.0
I duneos/loader: app_main @ 0x3fcea698
I duneos/loader: jumping to app_main @ 0x3fcea698
```

Tout le pipeline fonctionne : build app → deploy SD → boot kernel → découverte ELF → lecture manifest embarqué → chargement en DRAM → relocations Xtensa → saut dans `app_main`.

---

## Architecture générale

```
┌─────────────────────────────────────┐
│         Applications (.elf)          │  ET_REL ELF buildés séparément, SD card
├─────────────────────────────────────┤
│         SDK / ABI (table fixe)       │  table de pointeurs de fonctions
├──────────────┬──────────────────────┤
│  ELF Loader  │  VFS + POSIX layer   │  loader.c / vfs.c
├──────────────┴──────────────────────┤
│         FreeRTOS (via ESP-IDF)       │
├─────────────────────────────────────┤
│     BSP — board_config.h            │  sélectionné via .duneos_board
├─────────────────────────────────────┤
│         ESP-IDF / Hardware           │
└─────────────────────────────────────┘
```

---

## Décisions techniques structurantes

| Décision | Détail |
|---|---|
| ABI = table de pointeurs de fonctions | Pas de syscalls CPU — sans MMU pas de changement de mode. Même approche que Flipper Zero. |
| Format ELF = ET_REL | Objet relocatable, pas ET_DYN. Même format que les `.fap` Flipper Zero. |
| Manifest embarqué dans l'ELF | Section `.duneos_manifest` (JSON). Pas de `manifest.json` sur la SD. |
| VFS = ESP-IDF `esp_vfs_register` | Réutilisé, pas réinventé. Apps → `write()` → VFS → UART/SD. |
| Board via `.duneos_board` | Fichier gitignore à la racine. CMakeLists.txt le lit. Pas de variable CMake à passer. |
| PSRAM auto | `#ifdef CONFIG_SPIRAM` dans loader.c. Boards avec PSRAM → SPIRAM, sinon DRAM. |
| `printf` non exporté | Apps passent par newlib → `_write()` → VFS. Permet contrôle stdout par app. |
| `vTaskDelay` non exporté | FreeRTOS est un détail d'implémentation. Apps utilisent `nanosleep()`/`usleep()`. |

---

## Memory map

| Zone | Taille | Usage |
|---|---|---|
| IRAM | ~400 KB | Code kernel critique (`IRAM_ATTR`), ISR |
| DRAM | ~300 KB dispo | Kernel data, stacks FreeRTOS, heap kernel, **app sections (CardPuter sans PSRAM)** |
| PSRAM | 2–8 MB (si présent) | App `.text`/`.data`/`.bss`/`.rodata`, heap apps |
| Flash XIP | 8 MB (CardPuter) | Code kernel froid, `.rodata` kernel |

---

## Composants clés

### `components/duneos_kernel/include/duneos/abi.h`
- `DUNEOS_ABI_VERSION 1`
- `DUNEOS_MANIFEST_SECTION ".duneos_manifest"`
- `duneos_app_manifest_t` : name, version, required_abi_version, permissions (bitmask)
- `duneos_symbol_t` : {name, ptr} — table NULL-terminée

### `components/duneos_kernel/src/symbols.c`
Table d'export POSIX : open/read/write/close/lseek/stat/fstat/unlink/rename, opendir/readdir/closedir, malloc/free/realloc/calloc, pthread_*, sem_*, clock_gettime/gettimeofday/usleep/sleep/nanosleep(→duneos_nanosleep), utilitaires string purs, `duneos_exit`.

### `components/duneos_loader/src/loader.c`
- `duneos_loader_scan()` : walk `/sd/apps/*.elf` (strcasecmp), read manifest depuis section ELF
- `duneos_loader_select()` : lit `/sd/autoboot`, fallback sur list[0]
- `duneos_loader_load()` : parse ELF, charge sections en RAM, applique relocations RELA
- Relocations : R_XTENSA_32 (word32 absolu), R_XTENSA_SLOT0_OP (patch L32R), R_XTENSA_ASM_EXPAND (ignoré)
- `psram_alloc()` : `#ifdef CONFIG_SPIRAM` → SPIRAM ou DRAM

### `tools/dbt.py` — DuneBuild Tool
- Préfixe toolchain : `xtensa-esp32s3-elf-` en priorité (little-endian), fallback `xtensa-esp-elf-` (big-endian par défaut — ne pas utiliser seul)
- CFLAGS : `-mlongcalls -ffunction-sections -fdata-sections -fno-builtin -fno-common -ffreestanding -nostdlib -Os -std=c17`
- Link : `gcc -r -nostdlib` (driver gcc pour héritage de l'endianness cible)
- Manifest embarqué via `_manifest.c` généré avec `__attribute__((section(".duneos_manifest")))`
- Commandes : `new`, `build`, `info`, `deploy`, `clean`

---

## Leçons apprises (pièges à ne pas répéter)

| Piège | Solution |
|---|---|
| `xtensa-esp-elf-gcc` produit du big-endian Xtensa | Toujours utiliser `xtensa-esp32s3-elf-gcc`. `dbt.py` essaie le préfixe target-specific en premier. |
| `idf.cmakeAdditionalArgs` ignoré par l'extension VS Code | Board via fichier `.duneos_board` lu par CMakeLists.txt |
| `sdkconfig` à la racine mémorise les settings de la board précédente | Full Clean obligatoire en changeant de board |
| M5Stack CardPuter = ESP32-S3**FN8** = pas de PSRAM | `CONFIG_SPIRAM=n` dans son sdkconfig.defaults |
| FAT retourne les noms en majuscules 8.3 | `strcasecmp` pour filtrer `.elf` |
| `manifest.json` sur la SD = mauvaise approche | Manifest embarqué dans l'ELF (pattern Flipper Zero) |
| `printf` dans la table d'export bypass le VFS | Exporter uniquement `write()`, apps via newlib |
| `nanosleep` absent de ESP-IDF newlib | `duneos_nanosleep()` implémenté via `usleep()` |
| Commentaires inline dans `partitions.csv` parsés comme flags | Supprimer tous les commentaires inline |
| `CMAKE_SOURCE_DIR` non fiable en phase requirements | Utiliser `CMAKE_CURRENT_LIST_DIR` |
| `-EL` n'est pas un flag GCC | Utiliser gcc comme driver de link (pas ld directement) |

---

## Prochaines étapes (Phase 2)

Voir ROADMAP.md pour le détail complet. Priorités immédiates :

1. **`/tmp`** — tmpfs RAM-backed (apps écrivent des fichiers temporaires)
2. **`/dev/uart0`** — `read()`/`write()` mappe sur le driver UART → stdout des apps vers terminal série
3. **Shell app** — lit des commandes depuis `/dev/uart0`, lance des apps par nom

---

## Références

- Flipper Zero FAP loader : https://github.com/flipperdevices/flipperzero-firmware
- ESP-IDF VFS : https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/storage/vfs.html
- Zephyr Device Tree : https://docs.zephyrproject.org/latest/build/dts/intro.html
- newlib POSIX sur ESP32 : https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/newlib.html
- Xtensa ISA Reference : encodages d'instructions pour les relocations SLOT*_OP
