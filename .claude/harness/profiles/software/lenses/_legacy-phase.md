## Phase 2 — Parallel audit by smell family

Launch the auditors **in a single message**, one per retained family (LA-01):

Each prompt contains: the map, its family **and nothing else to look for**, the reading budget, the sentence "**zero findings is a normal and acceptable answer**", the ban on writing, verifiable evidence (file + line + excerpt ≤ 3 lines, or a structural observation), the T-shirt scale (XS < 30 min, S < 2 h, M half a day, L 1-2 days, XL to be split) and a single final JSON block (LA-05):

```json
{"findings": [{"id": "HYG-01", "category": "gitignore-absent", "severity": "critical|major|minor",
  "title": "short summary", "location": {"file": "relative/path or null", "line": 12},
  "evidence": "excerpt ≤ 3 lines or structural observation", "impact": "concrete consequence",
  "remediation": "proposed fix in one sentence", "size": "XS|S|M|L|XL", "depends_on": []}]}
```

`critical` is reserved for committed secrets, proven bugs and potential data loss. Each return: `K audit auditor --run <run> --unit <family> --output <file> [--dead] --duration-ms <ms> --tokens <n>`; `action`: `relaunch` (relaunch that family **once**), `not-audited`, `validate` (`to_validate`; `duplicates` discarded **without a validator**, including REJECTED from the project registry, `previous=<status>@<source>`), `none`.
