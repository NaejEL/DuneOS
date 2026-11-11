# hcore - deterministic core of the claude-skills skills.
#
# Python standard library only, 3.9 compatible. This package holds the
# deterministic rules taken out of the SKILL.md files (parsing of JSON blocks,
# counters, run and project registries, cycle journal, relaunch of a dead
# agent, gates, models per role, finding schema, stack detection, T-shirt sizes,
# file merges, ship-pr gh/git sequences, shared audit engine). The prompts keep
# only the judgment.
# Deliverable templates (shared format) are data files under templates/.
#
# Single entry point: harness.py (CLI), next to this package.

CORE_VERSION = "4.0.1"
