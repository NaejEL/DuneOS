---
name: factory-run
description: Lance un cycle de l'usine logicielle (Planner → gate humaine sur la spec → Builder → Verifier avec boucle de correction) sur un besoin ou une spec approuvée.
disable-model-invocation: true
argument-hint: "<besoin | chemin d'une spec approuvée>"
---

Pilote un cycle complet de l'usine logicielle DuneOS. Trois rôles à contextes séparés : `factory-planner` (lecture seule), `factory-builder` (implémente), `factory-verifier` (adversarial, ne modifie rien). L'utilisateur est le Product Owner : aucune construction ne démarre sans spec approuvée par lui.

## 1. Entrée

Examine l'argument fourni :

- Si c'est le chemin d'un fichier **existant** sous `specs/` dont la ligne de statut (première ligne non-titre du fichier) vaut exactement `Statut : APPROUVEE` → passe directement à l'étape 3 (Build).
- Si le fichier existe mais que le statut n'est pas `APPROUVEE` → traite-le comme une spec à re-présenter : reprends à l'étape 2, sous-étape « présentation à l'utilisateur ».
- Sinon → traite l'argument comme un **besoin** en langage naturel, étape 2.
- Argument vide → demande le besoin à l'utilisateur (AskUserQuestion) ; en headless, échoue immédiatement avec le message de l'étape 2.

## 2. Phase Plan — gate humaine obligatoire

**Si aucune interaction utilisateur n'est possible (exécution headless, `claude -p`), échoue immédiatement avec ce message et un exit non-zéro : « Cycle CI : fournir le chemin d'une spec déjà APPROUVEE sous specs/. La phase Plan exige une session interactive. » Ne construis jamais sans spec approuvée.**

1. Lance le sous-agent `factory-planner` (Agent, `subagent_type: "factory-planner"`, `run_in_background: false`) avec le besoin verbatim et cette consigne : produire le brouillon de spec au format imposé par sa définition (Contexte / Périmètre / Critères d'acceptation / Hors-périmètre / Risques / Questions ouvertes).
2. À son retour : si la section *Questions ouvertes* n'est pas « Aucune », pose chaque question à l'utilisateur via AskUserQuestion. Intègre les réponses dans la spec (contexte, périmètre ou critères selon la nature de la réponse).
3. Écris la spec dans `specs/SPEC-<slug-du-besoin>.md` (slug : minuscules, mots-clés du besoin joints par des tirets, court). Première ligne après le titre : `Statut : PROPOSEE`. Crée le dossier `specs/` si absent.
4. Présente la spec à l'utilisateur (résumé + critères d'acceptation in extenso) et demande son **approbation explicite** (AskUserQuestion : Approuver / Modifier).
   - Approbation → remplace `Statut : PROPOSEE` par `Statut : APPROUVEE` dans le fichier, passe à l'étape 3.
   - Demande de modification → intègre les retours, ré-écris le fichier, re-présente. Itère jusqu'à approbation ou abandon explicite de l'utilisateur.

## 3. Phase Build

Lance le sous-agent `factory-builder` (contexte frais, `run_in_background: false`) avec pour entrée : le chemin de la spec approuvée et la consigne de l'implémenter intégralement, build et tests passants avant de rendre la main. Rappelle-lui l'environnement : builds ESP-IDF/dbt via PowerShell (`idf.py build`, `python tools/dbt.py buildall`), tests Python via `python -m pytest tools/dbt/tests -q`.

## 4. Phase Verify — boucle de correction, 3 itérations maximum

Initialise `iteration = 1`.

1. Lance un sous-agent `factory-verifier` **neuf** (contexte vierge, indépendant du Builder, `run_in_background: false`) avec le chemin de la spec. Récupère le bloc JSON final de sa réponse (`verdict`, `tests_passed`, `issues`).
2. Si `verdict == "APPROVED"` **et** `tests_passed == true` → succès, sors de la boucle, va au rapport final.
3. Sinon : si `iteration == 3`, **échoue explicitement** — publie la liste complète des issues restantes dans le rapport final avec le statut ÉCHEC. N'approuve jamais par épuisement.
4. Sinon : relance `factory-builder` avec (a) le chemin de la spec, (b) la **liste complète des `issues`** du Verifier — fichier, sévérité, description, verbatim — et (c) la consigne : corriger chaque issue sans régresser sur les critères déjà satisfaits, build et tests passants avant de rendre la main. Puis `iteration += 1` et retour à la sous-étape 1 avec un **nouveau** Verifier.

## 5. Rapport final

Présente à l'utilisateur, en texte (pas de fichier de rapport) :

- fichiers modifiés : sortie de `git status --short` ;
- résultat des builds/tests (commandes exécutées et exit codes rapportés par le dernier Verifier) ;
- verdict final du Verifier et nombre d'itérations Build↔Verify consommées ;
- prochaine action suggérée : revue du diff par l'utilisateur (`git diff`), puis commit **par lui** — ne commite jamais à sa place.

En cas d'échec (gate headless, 3 itérations sans APPROVED, ou abandon utilisateur), le rapport l'énonce clairement avec les issues restantes, et la session se termine en erreur.
