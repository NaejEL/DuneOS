# Audit aliases: translation of the arguments of an old audit skill into the
# arguments of /audit.
#
# - AX-04: legacy-audit, vibecode-audit and ai-lab-audit are light aliases that
#   invoke the Skill tool on `audit` with the arguments translated here
#   (`audit alias-args`).
# - AX-05: translation table, data in ALIASES below (option names, the profile
#   forced, the lens set standing for "all", the cap and its bound). The lens
#   set, the lenses of an axis and the bounds are read from the profiles.
# - AX-06: --profile forces the profile, even if the stamp carries others: the
#   behavior of each alias stays limited to its original lenses.
# - MD-06: the short model aliases (math, neuro, ...) are passed through
#   unchanged; /audit resolves them.

from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from . import models, profiles


class AliasError(ValueError):
    """Unknown alias or invalid argument (exit 2)."""


class Alias(NamedTuple):
    profile: str
    lens_set: str
    units_option: str     # option of the old skill that selects lenses (or axes)
    cap_option: str


ALIASES: Dict[str, Alias] = {
    "legacy-audit": Alias("software", "legacy", "--categories", "--max-specs"),
    "vibecode-audit": Alias("software", "vibecode", "--axes", "--max-findings"),
    "ai-lab-audit": Alias("research", "research", "--lenses", "--max-questions"),
}


def _tokens(text: str) -> List[str]:
    lexer = shlex.shlex(text, posix=False)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        raw = list(lexer)
    except ValueError as exc:
        raise AliasError("unbalanced quotes in the arguments: %s" % exc)
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in raw]


def _quote(value: str) -> str:
    return '"%s"' % value if re.search(r"[\s;\"']", value) else value


def translate(skill: str, arguments: str, profiles_dir=None) -> Dict[str, Any]:
    alias = ALIASES.get(skill)
    if alias is None:
        raise AliasError("unknown alias: %r (expected one of %s)" % (skill, ", ".join(ALIASES)))
    prof = profiles.get(alias.profile, profiles_dir)
    lset = next(s for s in prof["lens_sets"] if s["name"] == alias.lens_set)
    by_name = {l["name"]: l for l in prof["lenses"]}
    try:
        rest, override = models.extract_option(arguments or "")
    except models.ModelsError as exc:
        raise AliasError(str(exc))
    tokens = _tokens(rest)
    repo = None
    if tokens and not tokens[0].startswith("--"):
        repo, tokens = tokens[0], tokens[1:]
    values: Dict[str, str] = {}
    autonomy: Optional[str] = None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--autonomy":                       # AP-03: passed through to /audit
            if i + 1 >= len(tokens) or tokens[i + 1] not in ("gate", "auto"):
                raise AliasError("--autonomy expects gate or auto")
            autonomy = tokens[i + 1]
            i += 2
            continue
        if token not in (alias.units_option, alias.cap_option):
            raise AliasError("unknown option or argument %r (expected %s, %s or --models)"
                             % (token, alias.units_option, alias.cap_option))
        if i + 1 >= len(tokens) or tokens[i + 1].startswith("--"):
            raise AliasError("%s expects a value" % token)
        if token in values:
            raise AliasError("%s given twice" % token)
        values[token] = tokens[i + 1]
        i += 2
    lenses = list(lset["lenses"])
    if alias.units_option in values:
        try:
            wanted = profiles.split_list(values[alias.units_option])
        except profiles.SelectionError as exc:
            raise AliasError("%s: %s" % (alias.units_option, exc))
        chosen: List[str] = []
        for item in wanted:
            if item in lset["lenses"]:
                names = [item]
            else:
                names = [n for n in lset["lenses"] if by_name[n].get("axis") == item]
            if not names:
                raise AliasError("%s: unknown value %r (expected among %s)"
                                 % (alias.units_option, item, ", ".join(lset["lenses"])))
            chosen += [n for n in names if n not in chosen]
        lenses = [n for n in lset["lenses"] if n in chosen]
    out: List[str] = []
    if repo is not None:
        out.append(_quote(repo))
    out += ["--profile", alias.profile, "--lenses", _quote(";".join(lenses))]
    if alias.cap_option in values:
        raw = values[alias.cap_option]
        if not re.fullmatch(r"\d+", raw) or int(raw) < 1:
            raise AliasError("%s expects an integer >= 1, got %r" % (alias.cap_option, raw))
        out += [alias.cap_option, str(min(int(raw), lset["cap"]))]
    if override:
        out += ["--models", _quote(override)]
    if autonomy:
        out += ["--autonomy", autonomy]
    out += ["--via", skill]
    return {"alias": skill, "arguments": " ".join(out), "profile": alias.profile, "lenses": lenses}
