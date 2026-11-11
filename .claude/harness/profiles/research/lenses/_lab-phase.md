## Phase 2 — Parallel audit by lens

Launch the auditors **in a single message** (read-only, AA-05), one per retained lens:

Each prompt contains: the map, its lens **and nothing else**, the reading budget, the sentence "**zero findings is a normal and acceptable answer**", the ban on writing and executing, verifiable evidence, the distinction between the two natures, and a single final JSON block (AA-07):

```json
{"findings": [{"id": "VAL-01", "lens": "validite", "nature": "correction|question",
  "severity": "critical|major|minor", "title": "short summary",
  "location": {"file": "relative/path or null", "line": 12, "journal_date": "YYYY-MM-DD or null"},
  "evidence": "excerpt ≤ 3 lines or citation", "impact": "what it changes for the project's conclusions",
  "proposal": "one-sentence fix, OR one-sentence falsifiable research question",
  "size": "XS|S|M|L|XL", "depends_on": []}]}
```

`correction` = something wrong or inconsistent to repair; `question` = something unknown that only an experiment settles. `critical` is reserved for leaks that invalidate a result published in the table, violations of a non-negotiable constraint, and headline conclusions resting on insufficient evidence. `size`: scale XS < 30 min, S < 2 h, M half a day, L 1-2 days, XL to be split; for a question, cost of the experiment on the declared hardware (XS a few GPU minutes, S < 1 h, M half a day of runs, L several days, XL out of reach). Each return: `K audit auditor --run <run> --unit <lens> --output <file> [--dead] --duration-ms <ms> --tokens <n>` (`relaunch`: once; `not-audited`; `validate` with `to_validate`; duplicates — AA-08: same file and line, or same document and date, and same lens, including REJECTED, `TRANCHEE` and pre-existing audit — discarded **without a validator**).
