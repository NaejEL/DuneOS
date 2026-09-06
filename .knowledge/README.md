# Base de connaissance rgkb

Ce dossier est **versionne**. Il porte l'index de code et les notes partagees
entre les utilisateurs du depot.

| Fichier | Role | Verite |
|---|---|---|
| `config.json` | perimetre de l'index, intention de hooks git | source |
| `config.local.json` | surcharges propres a une machine, **non versionne** | source |
| `notes.jsonl` | notes de connaissance ecrites par les humains et les agents | source |
| `files.jsonl` | fichiers indexes (chemin, langage, sha256, lignes) | derive |
| `symbols.jsonl` | definitions trouvees par ripgrep | derive |
| `graph.json` | aretes fichier vers symbole mentionne | derive |
| `manifest.json` | comptes et empreinte de l'index | derive |
| `index.sqlite` | cache de requete reconstruit depuis les fichiers texte | derive |

Les fichiers derives sont commites pour qu'un `git clone` donne une base
immediatement interrogeable, sans reindexation.

## Apres un clone ou un pull

Rien a faire. Pour verifier la fraicheur :

    python .claude/rgkb/rgkb.py status

Si l'index est perime (des fichiers ont change depuis la derniere indexation) :

    python .claude/rgkb/rgkb.py index

## En cas de conflit git

- `notes.jsonl`, `files.jsonl`, `symbols.jsonl` : conflit sur du texte, une
  ligne par element. Garder les deux cotes, puis relancer `index`.
- `graph.json`, `manifest.json`, `index.sqlite` : ne pas fusionner a la main.
  Prendre n'importe quel cote (`git checkout --ours .knowledge/`) puis relancer
  `python .claude/rgkb/rgkb.py index --force`, qui les regenere depuis les
  sources.

`index.sqlite` est declare binaire dans `.knowledge/.gitattributes` : git ne
tente pas de le fusionner ligne a ligne.

## Hooks git

`config.json` porte la cle `hooks` : `off` (defaut), `auto` (post-merge et
post-rewrite) ou `commit` (plus pre-commit, qui reindexe et met .knowledge/ a
l'index avant chaque commit). Comme elle est versionnee, elle voyage avec le
depot : le premier `status` ou `index` d'un utilisateur qui vient de cloner
installe les hooks chez lui, sans qu'il ait rien a lancer.

    python .claude/rgkb/rgkb.py hooks install --with-commit   # active pour tous
    python .claude/rgkb/rgkb.py hooks status
    python .claude/rgkb/rgkb.py hooks remove                  # desactive pour tous

`RGKB_NO_HOOK=1 git pull` neutralise les hooks ponctuellement.

## Reglages propres a une machine

`config.local.json` (ignore par git, memes cles que `config.json`) est fusionne
par-dessus `config.json`. C'est la seule facon correcte de renseigner un chemin
absolu, par exemple si ripgrep n'est pas dans le PATH :

    {"rg_path": "C:/Users/moi/scoop/shims/rg.exe"}

Ne jamais mettre un chemin absolu dans `config.json` : il casserait la base pour
les autres utilisateurs du depot.
