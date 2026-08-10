# SPEC — App « radar » (capteur HLK-LD2450)

Statut : APPROUVEE

## Contexte

L'utilisateur a acheté un capteur mmWave HLK-LD2450 (radar 24 GHz, UART 256000 bauds 8N1) et veut une app DuneOS `.dap` « radar » pour le M5Stack CardPuter : visualiser en temps réel les cibles en mouvement détectées (jusqu'à 3), avec le maximum d'informations fournies par le capteur — position X/Y (mm), donc distance et angle dérivables, et vitesse radiale (cm/s) — dans une GUI de type « scope radar militaire » (PPI : arcs de portée, balayage animé, blips).

**Protocole capteur (référence datasheet HLK-LD2450).** Trame de rapport périodique (~10 Hz, ~30 octets) : en-tête `AA FF 03 00`, 3 blocs cible de 8 octets (X int16 mm, Y int16 mm, vitesse int16 cm/s, résolution de distance uint16 — encodage de signe spécifique à vérifier sur la datasheet par le Builder), queue `55 CC`. Le capteur accepte aussi des commandes de configuration ; seule la commande « multi-target tracking » est dans le périmètre (décision PO Q6).

**Décisions Product Owner (2026-08-09) :**

| # | Question | Décision |
|---|---|---|
| Q1 | Câblage | Port Grove (GPIO1/GPIO2) réaffecté en UART capteur à 256000 bauds. Perte de `/dev/i2c-0` (i2cscope) sur cette board **actée** tant que le radar est câblé. |
| Q2 | Variante UART | **B** : nouvelle entrée `uart: id 1` dans `board.yaml` + généralisation de `drv_uart.c` → `/dev/uart1` dédié, sans `use_as_console`. |
| Q3 | Sweep | **Oui** : trait tournant + rémanence des blips, borné à la zone scope. |
| Q4 | Portée | Zoom clavier **2/4/6 m**. |
| Q5 | Mode démo | **Oui** : cibles simulées / trame de référence rejouée, activable sans capteur. |
| Q6 | Mode capteur | L'app **envoie la commande multi-target tracking** au démarrage. |
| Q7 | Unités | Distance en **m (1 décimale)**, vitesse en **km/h**, angle en **degrés signés** par rapport à l'axe du capteur. |
| Q8 | Parseur | **`sdk/sensor/libld2450.c`** (bibliothèque SDK réutilisable, pattern `libbq27220.c`). |

**État actuel du code concerné :**

- **UART kernel** : `kernel/duneos_kernel/src/drivers/drv_uart.c` n'expose que `/dev/uart0`, câblé en dur sur `DUNEOS_UART0_TX_PIN/RX_PIN/BAUD` de `board_config.h`, avec `use_as_console = true`. Pas de callback `ioctl` ni `readable` (`dev_driver.h`) : `select()` sur ce fd le considère « toujours lisible ». La lecture HAL a un timeout de 100 ms et retourne 0 octet si RX inactif (`arch/xtensa_esp32s3/hal/hal_uart.c`).
- **Brochage CardPuter** (`boards/m5stack-cardputer/board.yaml`) : UART0 déclaré sur GPIO43/44 — pins physiquement inaccessibles (Hard-Won Lesson). Port Grove HY2.0 = GPIO1/GPIO2, actuellement I2C0.
- **bspgen** (`tools/duneos-bspgen.py`, l.190-201) émet déjà `DUNEOS_UART<id>_*` pour plusieurs entrées `uart:` du YAML ; seul `drv_uart.c` ne consomme que l'entrée 0.
- **Affichage/UI apps** : `sdk/display/gfx.h` (libgfx, mode `GFX_MODE_STREAM` sans back-buffer + `gfx_canvas_new()` pour compositing anti-flicker), `sdk/ui/ui.h` (libui), `sdk/game/game.h` (`game_wait()` : boucle cadencée `select()` sur `/dev/input/event0`). Apps modèles : `apps/user/snake`, `apps/user/waves`, `apps/user/i2cscope`.
- **Manifeste/dispatch** : `duneos.yaml` avec `heap_size > 0` ⇒ mode spawned (tâche + slot dédiés). Mode BUFFERED = 63 KiB de back-buffer, non viable (pas de PSRAM, ~50-80 KiB heap kernel libre).
- **Launcher** : scanne `/sd/apps`, lit manifeste embarqué + `icon.dr` (ADR 023). Aucune modification requise.
- **Capabilities** : `tools/dbt/capabilities.py` — `capabilities: [display]` résout `libdisp.c` + `libst7789.c` (ADR 014).

## Périmètre

1. **`boards/m5stack-cardputer/board.yaml`** : supprimer/neutraliser la section I2C0 Grove, ajouter une entrée `uart: id 1` sur GPIO1/GPIO2 à `default_baud: 256000` (sens TX/RX : TX CardPuter → RX capteur, RX CardPuter ← TX capteur ; le Builder documente le brochage Grove retenu dans le YAML). Régénération via `python tools/duneos-bspgen.py boards/m5stack-cardputer/board.yaml` — jamais d'édition manuelle des fichiers générés. Vérifier que le profil `cardputer-net` (`profiles/`) n'est pas impacté.
2. **`kernel/duneos_kernel/src/drivers/drv_uart.c`** : généraliser pour enregistrer `/dev/uart1` quand `DUNEOS_UART1_*` est défini dans `board_config.h`, sans `use_as_console`. Cadre driver existant (`DUNEOS_DRIVER_REGISTER`), pas de nouveau symbole ABI.
3. **`sdk/sensor/libld2450.c` + `sdk/sensor/include/duneos/ld2450.h`** : bibliothèque SDK — ouverture du device série, envoi de la commande multi-target tracking au démarrage, synchronisation sur les trames (resync sur flux partiel/désaligné), décodage des 3 cibles (x mm, y mm, vitesse cm/s, résolution), état « cible absente ». La logique de décodage est une **fonction pure** (octets → structure), séparée de l'I/O, exerçable sur vecteur de test.
4. **`apps/user/radar/`** : `radar.c`, `duneos.yaml` (nom `radar`, `required_abi_version: 1`, `heap_size` > 0 ⇒ spawned, `capabilities: [display]`, permissions minimales, `icon: radar`), `icon.png`. GUI :
   - vue « scope » PPI (demi-plan devant le capteur) : cibles positionnées d'après X/Y, arcs/graduations de portée, sweep animé avec rémanence des blips borné à la zone scope ;
   - zoom clavier 2/4/6 m ;
   - panneau d'information par cible : distance (m, 1 décimale), angle (° signés), vitesse (km/h) ;
   - mode démo activable sans capteur (cibles simulées ou trame de référence rejouée) ;
   - rafraîchissement au rythme des trames (~10 Hz) sans scintillement plein écran (STREAM et/ou canvas partiel, au choix du Builder dans le budget mémoire) ;
   - touche de sortie (convention des apps existantes) avec fermeture propre des fd et retour launcher.
5. **Aucune modification** de : launcher, loader, table de symboles ABI, libgfx/libui (sauf manque bloquant découvert — à remonter, pas à contourner).

## Critères d'acceptation

1. **Build app** : `python ../../../tools/dbt.py build` dans `apps/user/radar/` (board `m5stack-cardputer`) se termine avec succès et produit `radar.dap` + `icon.dr`. *Vérif : exécution, code retour 0.*
2. **Manifeste** : `python ../../../tools/dbt.py info` affiche `name: radar`, `required_abi_version: 1`, `heap_size > 0`, arch `xtensa`. *Vérif : sortie commande / section `.duneos_manifest`.*
3. **Build kernel** : `python tools/duneos-bspgen.py boards/m5stack-cardputer/board.yaml` puis `idf.py build` passent sans erreur ni nouveau warning. *Vérif : codes retour + log de build.*
4. **Device présent** : après flash, `/dev/uart1` apparaît et s'ouvre sans erreur klog ; la console reste sur son routage actuel (aucun octet console vers le capteur). *Vérif : session shell USB CDC.*
5. **Décodage sur vecteur de test** : la fonction de parsing pure, alimentée avec une trame de référence documentée de la datasheet précédée d'octets parasites (preuve de resynchronisation), produit les valeurs X/Y/vitesse attendues. Vecteur et valeurs attendues cités en commentaire avec référence datasheet. *Vérif : chemin d'exécution observable (mode démo ou trace klog/dlog de test).*
6. **Fonctionnel sur matériel** : LD2450 câblé sur Grove, lancement de `radar` depuis le launcher : une personne se déplaçant devant le capteur apparaît comme cible mobile à l'écran en moins de 1 s, distance/angle/vitesse affichés et mis à jour ; distances plausibles (±20 % à 1 m et 3 m). *Vérif : test manuel scripté (procédure décrite dans la PR).*
7. **3 cibles** : plusieurs personnes dans le champ → jusqu'à 3 blips distincts ; les slots sans cible n'affichent pas de blip fantôme. *Vérif : test manuel.*
8. **Zoom** : la touche de zoom bascule l'échelle 2/4/6 m, graduations mises à jour, cibles repositionnées en conséquence. *Vérif : test manuel (mode démo accepté).*
9. **Sweep** : le balayage tourne continûment sur la zone scope avec rémanence des blips, sans scintillement plein écran ni chute visible de réactivité clavier. *Vérif : test visuel.*
10. **Mode démo** : sans capteur branché, l'activation du mode démo affiche des cibles simulées animées avec panneau d'info cohérent. *Vérif : test manuel sans matériel.*
11. **Sortie propre** : la touche de sortie ramène au launcher ; 5 cycles lancement→sortie sans fuite observable ni crash. *Vérif : test manuel + moniteur klog.*
12. **Launcher** : après `dbt deploy`, l'app apparaît dans le carrousel avec son icône. *Vérif : visuel + comptage du scan.*
13. **Budget mémoire** : mode spawned avec le `heap_size` déclaré, sans back-buffer plein écran 63 KiB ; pas d'échec `gfx_open`. *Vérif : `dbt info` + `gfx_last_error` sur cible.*
14. **i2cscope** : l'indisponibilité de `/dev/i2c-0` sur CardPuter est actée (décision PO Q1) et documentée dans `board.yaml` (commentaire) ; i2cscope affiche une erreur propre, pas un crash. *Vérif : lancement d'i2cscope sur cible.*

## Hors-périmètre

- Modification manuelle des fichiers générés par bspgen (`sdkconfig.board`, `board_config.h`, `partitions.csv`, `idf_target.txt`).
- Phases gelées 26-29 (`docs/contest-2026.md`) : pas d'OSAL, pas de VFS natif, pas de refonte réseau.
- Bump de `DUNEOS_ABI_VERSION` : aucun nouveau symbole exporté ni changement de struct ABI attendu.
- Commandes de configuration LD2450 autres que multi-target tracking (zones de filtrage, baud capteur, Bluetooth, firmware).
- Enregistrement/export des pistes, historique persistant sur SD.
- Support d'autres boards que le CardPuter (utiliser néanmoins les helpers responsive `ui_pct_*`/`gfx_get_info`, ADR 024).
- Toute modification du launcher, du loader, de `vfs_dev.c` hors enregistrement driver standard.

## Risques

- **`use_as_console`** : `/dev/uart1` ne doit jamais être candidat console ; vérifier le comportement avec `console: none`.
- **Pool exec 64 KiB (IRAM)** : radar.c + gfx + ui + libld2450 + libst7789 doivent tenir dans le pool partagé (raison du rejet LVGL). Surveiller `.text` au `dbt build`.
- **Heap sans PSRAM** (~50-80 KiB kernel libre) : canvas partiel borné (zone scope seule) ; `heap_size` spawned prélevé sur le heap kernel.
- **Pièges Hard-Won Lessons applicables** : pas de busy-poll (`while + usleep`) ; pas de `select()` seul sur le fd UART (pas de callback `readable` → toujours « prêt » → spin) ; buffers de lecture sur la pile, pas en `static` ; timestamps via `duneos_hal_monotonic_us()` ; UART0 GPIO43/44 inaccessible.
- **Flux 256000 bauds** : trames fragmentées/désalignées (lecture HAL timeout 100 ms) — le parseur doit resynchroniser sur l'en-tête sans bloquer l'UI.
- **Profil `cardputer-net`** : vérifier qu'un changement de `board.yaml` UART n'impacte pas ce profil (actuellement modifié dans l'arbre de travail).

## Questions ouvertes

Aucune — les 8 questions du brouillon ont été tranchées par le PO (tableau des décisions en Contexte).

---

**Fichiers clés pour le Builder** : `boards/m5stack-cardputer/board.yaml`, `kernel/duneos_kernel/src/drivers/drv_uart.c`, `kernel/duneos_kernel/include/duneos/dev_driver.h`, `sdk/display/include/duneos/gfx.h`, `sdk/ui/include/duneos/ui.h`, `sdk/game/include/duneos/game.h`, `apps/user/snake/` et `apps/user/i2cscope/` (modèles), `tools/dbt/capabilities.py`, `tools/duneos-bspgen.py` (l.190-201, émission `DUNEOS_UART<id>_*`).
