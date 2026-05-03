# DuneOS — Briefing projet pour Claude Code

## Vue d'ensemble

**DuneOS** est un système d'exploitation minimaliste pour la famille ESP32, construit sur ESP-IDF.  
Objectifs principaux :
- Permettre de **builder des applications séparément**, les déposer sur une carte SD, et les **charger dynamiquement** à l'exécution (à la manière des `.fap` du Flipper Zero)
- Exposer une **couche POSIX partielle** (fichiers, threads, sockets, time) via newlib + VFS ESP-IDF
- Offrir un **système de configuration de board** déclaratif (fichier YAML → header C généré), inspiré des device trees de Zephyr OS

**Plateforme cible prioritaire** : ESP32-S3 avec PSRAM (contraintes mémoire plus favorables).  
**Toolchain** : ESP-IDF (framework officiel Espressif), CMake, xtensa-esp32-elf.

---

## Architecture générale

```
┌─────────────────────────────────────┐
│           Applications (.elf)        │  ← buildées séparément, stockées sur SD
├─────────────────────────────────────┤
│         SDK applicatif (ABI fixe)    │  ← table de symboles exportés par le kernel
├──────────────┬──────────────────────┤
│  ELF Loader  │  VFS + POSIX layer   │  ← cœur du projet
├──────────────┴──────────────────────┤
│         Kernel / Scheduler           │  ← wrapper FreeRTOS (déjà dans ESP-IDF)
├─────────────────────────────────────┤
│      BSP (Board Support Package)     │  ← généré depuis YAML de board
├─────────────────────────────────────┤
│         ESP-IDF / Hardware           │
└─────────────────────────────────────┘
```

---

## Memory map ESP32-S3

Point fondamental à poser dès le début, car il conditionne toutes les décisions d'allocation :

| Zone | Taille | Usage DuneOS |
|---|---|---|
| **IRAM** | ~400 KB | Code kernel critique (`IRAM_ATTR`), ISR, hot-path |
| **DRAM** | ~300 KB dispo après ESP-IDF | Kernel data, stacks FreeRTOS, heap kernel |
| **PSRAM** | 2–8 MB (accès via cache SPI) | Sections `.text`/`.data`/`.bss` des apps, heap apps |
| **Flash XIP** | 4–16 MB (accès via cache) | Code kernel froid, `.rodata` kernel |

**Règle principale :** les apps vivent en PSRAM, le kernel en DRAM/IRAM.  
Allocation PSRAM : `heap_caps_malloc(size, MALLOC_CAP_SPIRAM)`.

---

## Plan d'implémentation (ordre logique)

### Phase 1 — Fondations kernel

**But : booter de façon structurée et poser les abstractions de base.**

1. Définir la **memory map statique** (cf. tableau ci-dessus) : zones kernel, heap apps, stack, buffers partagés.
2. Implémenter le **kernel init / app supervisor** (s'exécute comme une app ESP-IDF standard) :
   - **Attention :** ne pas confondre avec le bootloader ESP-IDF (ROM + app bootloader). DuneOS tourne au-dessus, dans le contexte applicatif.
   - Monter la SD au boot
   - Lire un fichier `manifest.json` à la racine de `/sd/apps/`
   - Décider quelle app lancer (menu ou auto-boot)
3. Abstraire FreeRTOS derrière une **API de tâches interne** (`duneos_task_create`, `duneos_task_yield`, etc.)
4. Définir l'**ABI kernel ↔ app** :
   - **Table de pointeurs de fonctions** à adresse fixe en RAM (pas de vrais syscalls CPU — sans MMU il n'y a pas de changement de mode privilégié). Même approche que Flipper Zero (`api_symbols` table).
   - Convention d'appel (registres, stack frame Xtensa)
   - Point d'entrée des apps (`app_main`)
   - Numéro de version ABI dans le manifest (refus de chargement si mismatch)
5. Stratégie **watchdog** : ESP-IDF expose deux WDT :
   - *Task WDT* : surveille les tâches FreeRTOS qui ne yielden pas → une app qui boucle le déclenche
   - *Interrupt WDT* : surveille les ISR
   - Décision à prendre : les apps appellent-elles `esp_task_wdt_reset()` elles-mêmes, ou le kernel le fait-il depuis la tâche superviseur ?

---

### Phase 2 — VFS et couche POSIX

**But : `open()`, `read()`, `write()` fonctionnent sur la SD et les devices.**

1. Brancher **FatFS sur la SD** via `esp_vfs_fat_sdspi_mount()` (support natif ESP-IDF).
2. Utiliser le **VFS ESP-IDF existant** (`esp_vfs_register`) comme couche de dispatch — ne pas réinventer un VFS from scratch. DuneOS enregistre ses propres drivers VFS-compatibles dedans.
   - Arbre de montage : `/sd`, `/tmp`, `/dev`
   - Dispatch automatique vers le bon driver selon le préfixe de chemin
3. Exposer les **syscalls POSIX fichiers** : `open`, `read`, `write`, `close`, `stat`, `opendir`, `readdir`, `lseek`.
4. Ajouter des **abstractions device** sous forme de fichiers VFS :
   - `/dev/uart0`, `/dev/gpio`, `/dev/spi0`, etc.
   - Modèle lecture/écriture + ioctl minimaliste
5. Exposer les **POSIX threads** (déjà partiellement disponibles via newlib + FreeRTOS) : `pthread_create`, `pthread_mutex_*`, `sem_*`.
6. Exposer **POSIX time** : `clock_gettime`, `gettimeofday`.

> Note : full POSIX est impossible sans MMU. Le subset ciblé est suffisant pour la grande majorité des applications embarquées.

---

### Phase 3 — ELF Loader (cœur du projet)

**But : poser un `.elf` relocatable sur la SD et le lancer.**

C'est la partie la plus complexe. L'ESP32 n'a pas de MMU, donc pas d'isolation mémoire vraie.

1. Définir le **format d'application** :
   - ELF de type **ET_REL** (relocatable object, pas ET_DYN/shared object) — même approche que le Flipper Zero FAP loader, qui est la référence principale
   - Manifest JSON embarqué dans une section ELF dédiée (nom, version, version ABI requise, permissions)
2. Écrire l'**ELF loader** :
   - Parser les headers ELF et les sections (`.text`, `.data`, `.bss`, `.rodata`)
   - Allouer en PSRAM (`heap_caps_malloc(..., MALLOC_CAP_SPIRAM)`) et y copier les sections
   - **Section `.bss` : ne pas la copier depuis l'ELF** (elle n'y est pas stockée, juste une taille). Le loader doit l'allouer et la **zero-initialiser explicitement** (`memset(bss_ptr, 0, bss_size)`).
   - Appliquer les **relocations** — la complexité principale. Il existe ~30 types de relocation Xtensa. Les plus fréquents :
     - `R_XTENSA_32` : relocation 32-bit simple (addend + adresse symbole)
     - `R_XTENSA_SLOT0_OP` (et variantes SLOT1, SLOT2…) : modification de champs bitfield à l'intérieur d'instructions Xtensa encodées — consulter la spec Xtensa ISA et le code Flipper Zero pour chaque type
   - Résoudre les **symboles externes** via la table de pointeurs de fonctions du kernel
3. Définir et versionner la **table des symboles exportés** par le kernel (API publique stable) :
   - Fonctions VFS/POSIX
   - API tâches
   - Drivers bas niveau (GPIO, UART, SPI, I2C)
   - Utilitaires (malloc/free depuis le heap apps, printf, etc.)
4. Gérer le **cycle de vie de l'app** :
   - Lancement (jump to `app_main`)
   - Retour propre (l'app appelle `duneos_exit()`)
   - Libération mémoire (sections en PSRAM, stacks FreeRTOS créés par l'app)
   - Gestion des crashs : watchdog dédié, exception handler pour isoler le crash sans rebooter le kernel

#### Modèle de permissions (sans MMU)

Le manifest déclare des permissions (ex : accès réseau, accès GPIO). Sans MMU, l'enforcement est **purement logiciel** : le kernel refuse de résoudre les symboles sensibles si la permission correspondante n'est pas déclarée. C'est un modèle de **capabilities** sans isolation hardware — la sécurité repose sur l'intégrité du kernel, pas sur le CPU.

---

### Phase 4 — Board Support Package (BSP)

**But : configurer une nouvelle carte sans toucher au kernel.**

1. Définir un **schéma de descripteur de board** en YAML :
   ```yaml
   board:
     name: "esp32s3-devkitc"
     cpu: esp32s3
     flash_size_mb: 8
     psram_size_mb: 8
     uart:
       - id: 0
         tx_pin: 43
         rx_pin: 44
     spi:
       - id: 2
         mosi_pin: 11
         miso_pin: 13
         clk_pin: 12
         cs_pin: 10
         role: master
     sd_card:
       interface: spi
       spi_id: 2
   ```
2. Écrire un **outil de génération** (Python ou Go) : `duneos-bspgen board.yaml → board_config.h`
3. Le kernel lit une **structure de config compilée** (header généré) ou un bloc en flash à adresse fixe.
4. Fournir des **BSP de référence** pour les boards courantes :
   - ESP32-DevKitC
   - ESP32-S3-DevKitC
   - TTGO T-Display
   - Lilygo T7-S3

---

### Phase 5 — SDK applicatif et toolchain

**But : permettre à un développeur tiers de créer une app facilement.**

1. Fournir un **template CMake** pour compiler une app en ET_REL ciblant l'ABI DuneOS.
2. Fournir les **headers de l'API publique** (`duneos/vfs.h`, `duneos/task.h`, `duneos/gpio.h`, etc.).
3. Documenter et automatiser le **workflow complet** :
   ```
   cmake . && make → app.elf → copier sur /sd/apps/ → reboot → l'OS la détecte et la lance
   ```
4. Écrire des **apps de démo** :
   - `hello_world` (printf + exit)
   - `blink` (GPIO via API kernel)
   - `file_reader` (open/read/printf)
5. Documenter le **portage d'une app C existante**.

---

## Points de vigilance techniques

| Risque | Mitigation |
|---|---|
| Pas de MMU → pas d'isolation réelle | Conventions strictes + watchdog + stack canaries + exception handler par app + modèle capabilities |
| Mémoire contrainte (320 KB SRAM ESP32 classique) | Cibler ESP32-S3 + PSRAM dès le début ; apps allouées via `MALLOC_CAP_SPIRAM` |
| ~30 types de relocation Xtensa, dont SLOT0/1/2_OP complexes | Étudier et porter le code de relocation du Flipper Zero FAP loader (référence directe) |
| Versionning ABI | Numéro de version dans manifest + refus au chargement si mismatch |
| Exécution depuis PSRAM lente (cache miss) | Sections `.text` critiques du kernel épinglées en IRAM via `IRAM_ATTR` |
| Section `.bss` non stockée dans l'ELF | Allouer + `memset(..., 0, bss_size)` explicitement dans le loader |
| Watchdog déclenché par apps qui boucient | Définir qui nourrit le Task WDT (app ou superviseur kernel) |
| Confusion avec le bootloader ESP-IDF | DuneOS kernel init est une app ESP-IDF standard, pas un vrai bootloader |

---

## Références et inspirations

- **Flipper Zero FAP loader** : https://github.com/flipperdevices/flipperzero-firmware — référence principale pour l'ELF loader ET_REL sur micro sans MMU, notamment la gestion des relocations Xtensa
- **ESP-IDF VFS** : https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/storage/vfs.html — VFS à réutiliser, pas à réinventer
- **Zephyr Device Tree** : https://docs.zephyrproject.org/latest/build/dts/intro.html — inspiration pour le BSP YAML
- **newlib POSIX sur ESP32** : https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/newlib.html
- **Xtensa ISA Reference** : pour les encodages d'instructions nécessaires à l'application des relocations SLOT*_OP

---

## Premier jalon suggéré

> Kernel init qui lit `/sd/apps/manifest.json`, charge un ELF ET_REL minimal en PSRAM, zero-init sa section `.bss`, résout 3 symboles (`printf`, `vTaskDelay`, `duneos_exit`) via la table de pointeurs de fonctions du kernel, et saute dans `app_main`.

Tout le reste s'empile sur cette fondation.

---

## Contraintes et choix de style

- Langage kernel : **C** (C17, pas de C++, pas d'exceptions)
- Langage tooling (bspgen, etc.) : **Go** ou Python
- Pas de dépendances externes au kernel au-delà d'ESP-IDF
- API publique stable dès la Phase 3 — aucun breaking change sans bump de version ABI
- Tests unitaires sur host (via CMock/Unity) pour les parties non hardware-dépendantes (parser ELF, logique de relocation, parser YAML BSP)
