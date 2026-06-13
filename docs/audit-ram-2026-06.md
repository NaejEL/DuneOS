# Audit RAM kernel — DRAM / IRAM (CardPuter, juin 2026)

Audit réalisé le 2026-06-09 sur la branche `feature/contest-2026` (post-c296ec9),
build du jour (`build/duneos.map`), board `m5stack-cardputer` (ESP32-S3FN8,
512 KiB SRAM, pas de PSRAM). Aucune modification appliquée — ce document liste
les constats et les actions recommandées, priorisées par ratio gain/risque.

Rappel S3 : IRAM et DRAM sont la même SRAM physique (D/IRAM) vue par deux bus.
Chaque KiB d'IRAM statique économisé redevient de la DRAM heap. C'est le fil
conducteur de tout l'audit.

---

## 1. État des lieux mesuré

### 1.1 Image statique (esp_idf_size sur `build/duneos.map`, 2026-06-09)

| Région | Utilisé | Détail | Reste |
| --- | ---: | --- | ---: |
| **DIRAM** (334 KiB) | **143 644 B (42 %)** | .text **60 451** + .bss **60 128** + .data **23 065** | **198 116 B** |
| IRAM dédiée (16 KiB) | 16 384 B (100 %) | vectors 1 028 + .text 15 356 | 0 |
| Flash code | 753 742 B | | |
| Flash rodata | 170 996 B | | |

Lecture : il reste **~193 KiB** de heap au boot, dans lesquels doivent tenir
arena (64 K) + exec pool (64 K) + WiFi/lwIP (~70 K post-ADR 030) ≈ **198 K**.
Le budget est exactement à l'équilibre — c'est pour ça que tout passe ou casse
à quelques KiB près. Les deux postes compressibles de l'image statique :

- **`.text` DIRAM = 60,4 KiB** : code kernel/IDF placé en IRAM, compilé en
  `-Og`. C'est la cible des actions P1 (§3.1).
- **`.bss` = 60,1 KiB** : dont klog 16 K (`klog.c:30`), ring GPIO 4 K
  (`drv_gpio.c:22`), chunk DMA FB 4 K (`drv_fb_st7789.c` — **probablement absent
  sur CardPuter : `DUNEOS_DRV_FB` dépend de `SPIRAM`, off sans PSRAM ; à
  confirmer sur la `.map`**), tables tmpfs/devfs/vfs ~5 K. Cible des actions P2 (§3.2).

### 1.2 Réservations runtime du kernel

| Poste | Taille | Source |
| --- | ---: | --- |
| Arena apps | 64 KiB (`CONFIG_DUNEOS_APP_ARENA_KB=64`) | `supervisor.c:78-98`, multi_heap dédié, réservé pré-WiFi, repli binaire jusqu'à 16 K |
| Exec pool (.text apps) | 64 KiB (`CONFIG_DUNEOS_EXEC_POOL_KB=64`) | `loader.c:1043-1070`, `MALLOC_CAP_EXEC`, bump allocator, reclaim LIFO seulement (`loader.c:1591`) |
| Stack supervisor | 24 KiB | `supervisor.c` (~755) |
| WiFi + lwIP | ~70 KiB à l'association | mesuré via `free` (ADR 030 déjà appliqué) |

### 1.3 Coût par app résidente (daemons compris)

| Poste | Taille | Note |
| --- | ---: | --- |
| `duneos_app_t` | **~4,2 KiB** | dont **4 KiB** de `section_bases[MAX_SECTIONS=1024]` (`loader.c:101,108`) — voir P2.1 |
| Mailbox queue | ~1 KiB | 8 × `duneos_msg_t`, créée à chaque launch même sans IPC |
| Slot + TCB | ~600 B | slot ~360 B (grow-only, ADR 025) + `StaticTask_t` 232 B |
| Stack | auto (ADR 029) | 3-8 KiB, watermarks 26-65 % — bien dimensionné maintenant |
| Data pool | variable | .data/.bss/.rodata de l'app, dans l'arena |

---

## 2. Avis sur l'arena

**L'approche est la bonne, à conserver.** Quatre raisons :

1. **Réservation pré-WiFi** = le foreground récupère ses gros blocs contigus
   (canvas tetris 20 K, data pool waves 18 K) quel que soit l'état de
   fragmentation de la heap générale. C'est la seule garantie possible sans MMU.
2. **`multi_heap` d'ESP-IDF est un TLSF** (depuis IDF 4.3). Le pilier 3 de
   l'ADR 008 (« TLSF dans le pool app ») est donc **de facto déjà couvert** —
   l'allocateur de l'arena et des heap pools per-app est déjà O(1) à faible
   fragmentation. Recommandation : mettre à jour l'ADR 008 pour acter que le
   pilier 3 est clos, plutôt que d'embarquer un second TLSF dans `libdune.a`.
3. **Free-on-exit = défrag implicite** (pilier 2), renforcé par le handoff
   ADR 031 qui borne l'occupation à daemons + 1 foreground.
4. Le repli binaire au boot (64→32→16 K) dégrade proprement au lieu d'échouer.

**Mais l'arène est aujourd'hui bloquée — pas par sa conception, par le loader.**
Réactivée à 64 K, le launcher échoue au chargement (`ESP_ERR_NO_MEM`, mesuré
2026-06-09) : non par manque de place pour ses pools (data 12,8 K < arène libre
38 K), mais parce que la **lecture des en-têtes du `.dap` exige ~28 K contigus
dans le tas général** que l'arène vient justement de réduire (`largest` tombé à
27,6 K). C'est le verrou **P0** (§3) ; une fois levé, les quatre raisons ci-dessus
tiennent et l'arène redevient la stratégie par défaut.

Deux faiblesses à corriger (peu de code, voir P2.5) :

- **Fallback silencieux vers la heap générale** (`supervisor.c:111-113`) : un
  débordement d'arena re-fragmente la heap générale *sans aucune trace*, en
  contradiction avec la politique tripwire de l'ADR 008 (« toute alloc kernel
  qui échoue ou déborde est loggée comme fragmentation potentielle »). Un
  `klog_w` à cet endroit suffit.
- **Pas de visibilité du pic d'occupation** : `multi_heap_get_info()` expose
  `minimum_free_bytes`, mais `duneos_meminfo()` ne le remonte pas pour l'arena
  (`supervisor.c:163-171`). Exposer `arena_min_free` dans `meminfo.h` + `free`
  permettrait de dimensionner les 64 K sur données réelles (cf. P3.2).

---

## 3. Actions recommandées, priorisées

### P0 — clé de voûte : le transient de chargement (débloque l'arène ET la vitesse)

**Constat (ajout 2026-06-09, non vu dans la v1 de l'audit).** À *chaque*
chargement de `.dap`, le loader alloue depuis le **tas général** quatre buffers,
vivants simultanément pendant la relocation et libérés ensuite
(`loader.c:1128-1166`) :

| Buffer | Taille (launcher ~700 sections) | Ligne |
| --- | ---: | --- |
| `shdrs` (table des en-têtes de sections) | `e_shnum × 40` ≈ **28 K** | `loader.c:1128` |
| `shstrtab` (noms de sections) | ~8 K | `loader.c:1138` |
| `symtab` | ~15 K | `loader.c:1152` |
| `strtab` (noms de symboles) | ~8 K | `loader.c:1160` |

≈ **60 K de transient par load**, dont un bloc contigu de 28 K. Conséquences :
(1) **casse l'arène** — le tas général ne peut plus fournir 28 K contigus ;
(2) **ralentit chaque launch / boot / retour-handoff** — 60 K à lire depuis la SD
(FatFS/SPI) + malloc/free ; (3) se paie à *chaque* chargement, pas une fois.
À distinguer du `section_bases[1024]` de P2.1 : celui-ci est le coût *runtime*
(4 K résident) ; le transient ci-dessus est le coût *de chargement* (~60 K
éphémère) — c'est lui le vrai verrou.

**Cause.** Le `.dap` est ET_REL (`LDFLAGS = -r`, `constants.py:44`), et `ld -r`
**ne fait pas de gc-sections**. Or les apps compilent avec `-ffunction-sections
-fdata-sections` (`constants.py:17-19`) → ~1 section par fonction. Sans gc, ces
options n'éliminent **rien** : elles ne font que **multiplier les sections**
(~700 pour le launcher), donc gonfler les quatre buffers, pour zéro bénéfice.

**Action.** Retirer `-ffunction-sections -fdata-sections`, ou fusionner les
sections via un linker script en `ld -r` (`.text : { *(.text* .literal*) }`, etc.
— ADR 022 option C). Résultat : ~40 sections au lieu de ~700, **mêmes octets de
code chargés** (aucun gc à perdre), transient **~60 K → ~12 K**, chargement plus
rapide, et **l'arène redevient viable** (le tas général n'a plus besoin du bloc
de 28 K).

**Validation.** `dbt info` (compter les sections avant/après), taille de `shdrs`
au boot, chrono du launch (boot → launcher visible) ; puis réactiver
`CONFIG_DUNEOS_APP_ARENA_KB=64` et confirmer que le launcher charge. Risque
faible (ET_REL = pas de gc à perdre ; le loader classe déjà par préfixe de nom,
§ « Section classification », donc des sections fusionnées `.text`/`.rodata` se
chargent à l'identique). **Meilleur ratio de tout l'audit, à faire avant P1/P2.**

### P1 — sdkconfig, gain fort, risque faible, zéro code

Cible : les 60,4 KiB de `.text` DIRAM. Chaque action se valide par
`idf.py size` avant/après (et `fullclean` obligatoire après changement).

| # | Action | Gain estimé | Risque / condition |
| --- | --- | ---: | --- |
| 1 | **`CONFIG_COMPILER_OPTIMIZATION_SIZE=y`** (le kernel est en `-Og`, `sdkconfig:979` — décision déjà validée) | 10-30 K (IRAM + flash) | Debug moins confortable (variables optimisées). Les apps `.dap` sont déjà en `-Os` (`tools/dbt/constants.py:24`) |
| 2 | **`CONFIG_SPI_FLASH_ROM_IMPL=y`** (`sdkconfig:3281`, non set) | ~7-10 K IRAM | Driver flash ROM ≠ IDF : valider LittleFS + OTA éventuel sur device |
| 3 | `CONFIG_FREERTOS_PLACE_FUNCTIONS_INTO_FLASH=y` | ~8 K IRAM | Vérifier le nom exact en IDF v6 (absent du sdkconfig généré — a pu être renommé/absorbé) |
| 4 | `CONFIG_HEAP_PLACE_FUNCTION_INTO_FLASH=y` + `CONFIG_RINGBUF_PLACE_ISR_FUNCTIONS_INTO_FLASH=y` (`sdkconfig:2433,1829`) | 2-5 K IRAM | Interdit malloc/ringbuf en ISR avec cache flash off — DuneOS n'en fait pas (CDC RX passe par StreamBuffer) |
| 5 | Newlib **nano format** — la ROM S3 l'a (`CONFIG_ESP_ROM_HAS_NEWLIB_NANO_FORMAT=y`, `sdkconfig:862`) ; retrouver l'option IDF v6 (renommée, probablement `CONFIG_LIBC_*`) | quelques K .data/flash | Casse `%f`/`%lld` dans printf — auditer klog et les apps avant |
| 6 | **Dcache 32 K → 16 K** (`CONFIG_ESP32S3_DATA_CACHE_16KB`, `sdkconfig:1898`) | **16 K SRAM** | **À mesurer, pas à faire d'office** : pénalise les accès rodata-en-flash (fonts libgfx, icônes launcher). Tester le rendu des jeux avant d'adopter. Icache déjà au minimum (16 K) |
| 7 | Stacks système : main 3584, timer 3584, event 2304, TCPIP 3072 | 2-4 K cumulés | Uniquement avec watermarks mesurées. Priorité basse, dernier de la liste |

Ordre conseillé : 1 seul changement à la fois, `idf.py size` + boot complet
(launcher → jeu → retour) entre chaque, pour attribuer les gains et isoler
toute régression.

### P2 — code kernel, gain fort, effort faible

| # | Action | Gain estimé | Où |
| --- | --- | ---: | --- |
| 1 | **`section_bases[1024]` → allocation à `e_shnum` réel** (typiquement <100 sections, soit <400 B au lieu de 4 K). Meilleur ratio gain/effort de tout l'audit : le tableau est payé par **chaque app résidente**, daemons compris | **~3,9 K × N apps ≈ 15-20 K** | `loader.c:101,108` ; bornage déjà en place (`loader.c:1119,1334`) |
| 2 | `KLOG_RING_SIZE` 16 K → option Kconfig, 8 K sur CardPuter | 8 K .bss | `klog.c:30` (garder une puissance de 2, le code utilise `KLOG_RING_MASK`) |
| 3 | `GPIO_EV_RING` 512 → Kconfig ou 128 entrées | 3 K .bss | `drv_gpio.c:22` — 512 était dimensionné pour absorber la latence select() ; 128 reste large pour un clavier |
| 4 | **Mailbox lazy** : créer la queue au premier `duneos_send`/`recv` au lieu de chaque launch — tous les daemons n'utilisent pas l'IPC | 3-5 K | `supervisor.c` (~881) |
| 5 | Tripwire arena (`klog_w` sur fallback) + `arena_min_free` dans `meminfo`/`free` (cf. §2) | visibilité | `supervisor.c:111-113,163-171`, `meminfo.h`, `free.c` |

### P3 — chantiers structurants (post-contest, sauf blocage)

1. **Exec pool two-zone** : daemons alloués bottom-up, foreground top-down.
   C'est le seul vrai risque de fragmentation restant : le bump allocator ne
   reclaim qu'en LIFO (`loader.c:1591`) — un daemon déchargé au milieu laisse
   un trou irrécupérable jusqu'au reboot. La compaction est impossible
   (relocations déjà appliquées, le code ne peut pas bouger) ; two-zone est la
   réponse simple et suffisante. Avec le handoff ADR 031 le foreground est
   déjà LIFO par construction, donc non bloquant pour le contest.
2. **Re-dimensionner exec pool et arena sur données réelles** : une fois P2.5
   en place, relever `exec_pool_used` max et `arena_min_free` avec les 5 apps
   contest. Si l'exec pool plafonne à ~40 K, redescendre
   `CONFIG_DUNEOS_EXEC_POOL_KB` à 48 rend 16 K à la heap générale.
3. **Stack supervisor 24 K** : re-mesurer la watermark maintenant que le
   handoff ADR 031 a remplacé les chaînes d'observation profondes ;
   potentiellement 8-12 K à reprendre.
4. **Fuite des fds `/dev` au crash d'app** (backlog, confirmée 2026-06-05) :
   pas un gain RAM direct, mais `DEVFS_MAX_FDS=16` s'épuise en ~2 crash-loops
   — conditionne la stabilité des restarts et donc la démo. À garder en haut
   du backlog.

---

## 4. Tableau de suivi

À remplir au fil de l'implémentation ; chaque gain se mesure par
`idf.py size` (statique) et `free` sur device (runtime, 3 baselines
entry/kernel/services).

| Action | Gain estimé | Gain mesuré | Risque | Statut |
| --- | ---: | ---: | --- | --- |
| **P0 merge sections** (drop `-ffunction-sections`) | transient 60→12 K/load + débloque arène + vitesse | | faible (ET_REL = pas de gc) | **clé de voûte, à faire en 1er** |
| P1.1 `-Os` | 10-30 K | 8,8 K IRAM (+90 K flash) | **moyen (révisé)** | **REVERTÉ 2026-06-10** — gain mesuré -Os vs -Og même code (`.iram0.text` 75831→67039), mais le kernel **ne boote plus** en `-Os`. Probable UB latent exposé par l'optimisation dans du code bas-niveau (loader écrit l'IRAM via l'alias DRAM, exec-install, barrières mémoire). À ne PAS réactiver avant d'avoir isolé l'UB (manque de `volatile`/barrière). Le `-Os` est donc un gain conditionnel, pas acquis. |
| P1.2 `SPI_FLASH_ROM_IMPL` | 7-10 K | | moyen (LittleFS) | |
| P1.3 FreeRTOS→flash | ~8 K | | faible | nom IDF v6 à vérifier |
| P1.4 heap/ringbuf→flash | 2-5 K | | faible | |
| P1.5 newlib nano | qq K | | moyen (%f) | |
| P1.6 dcache 16 K | 16 K | | **perf rendu** | à mesurer |
| P2.1 `section_bases` | 15-20 K | **24,6 K** (mesuré `free`) | faible | **fait + validé 2026-06-10** — `free` 42296→66952, low-water 17K→38K. Mieux que l'estimation (5 apps résidentes × ~4K + reload). |
| P2.2 klog 8 K | 8 K | | nul | |
| P2.3 ring GPIO | 3 K | | faible | |
| P2.4 mailbox lazy | 3-5 K | | faible | |
| P2.5 tripwire + min_free | visibilité | | nul | |

Potentiel cumulé P1+P2 (hors dcache) : **~50-90 KiB**, soit de quoi passer
d'un budget « à l'équilibre exact » (§1.1) à ~25 % de marge — ou réactiver du
confort (WiFi daemon résident, klog 16 K) si la marge le permet.

**P0 est à part et passe avant tout** : il ne libère pas de la RAM résidente mais
**supprime le pic de ~60 K à chaque chargement** (donc débloque l'arène) et
**accélère les launches**. Conclusion de l'audit révisée : la stratégie arène est
la bonne (§2) ; son unique blocage est le transient de chargement (P0), causé par
l'explosion de sections d'un ET_REL sans gc. Ordre d'attaque : **P0 → réactiver
l'arène → P1 → P2**.
