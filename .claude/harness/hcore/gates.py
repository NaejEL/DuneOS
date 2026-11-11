# Human gates (AskUserQuestion points) of the skills: policy, decision,
# headless fallback, journal events.
#
# - GT-01: the policy is read from .claude/harness.json, key "autonomy" in
#   {gate, auto}. Missing file or key -> gate. Invalid value or unreadable
#   JSON -> usage error (exit 2).
# - AP-01 (replaces GT-02 and the Phase 1 guard GT-03): decision table. gate: ask
#   for kinds a, b and c. auto: default for b and c (stop when the gate has no
#   default); for a, the core evaluates the declared `stop_when` on the inputs
#   given by the skill: true -> stop, false -> default. The guard "auto applied
#   as gate" and the Phase 2 exemption mark are gone: AR-02 is the general case.
# - AP-10: every gate carries a one-line `justification` and, for kind a, a
#   `stop_when`: "always", "never" or "input:<name>" (true when the skill passes
#   <name>=true).
# - GT-04: a dialogue gate (brainstorm.*) always returns ask.
# - GT-05: a gate without a default whose decision would be "default" ->
#   stop (exit 1, message).
# - GT-06: every gate declares a headless fallback (stop | default | skip),
#   returned with the decision; the skill applies it when AskUserQuestion is
#   unavailable and declares it as such (answer source "headless").
# - GT-07: two "gate" events are logged, the decision then the answer: id,
#   kind, policy, decision, default, answer, source in user|default|headless.
# - GT-08: single catalog (core gates, plus the gates the profiles declare: PF-05);
#   an unknown id -> exit 2.
# - AR-02: a gate declared `proposal` (scout verdict) takes its default under auto
#   when the proposal is clear; an ambiguous one stops (GT-05).
# - GT-09: upgrade.* gates are journaled with software-factory.
# - Closed options: a user answer to a gate with closed options must be one of
#   its options. A gate asking several questions in one call ("questions",
#   separated by " ; " in the catalog) takes exactly one valid option per
#   question, in order, joined by " / "; a gate asking the same question for
#   each of up to `repeat` themes takes 1 to `repeat` options of that list.
#   Any other answer (a free "Other" text, typically) is not an error: the
#   CLI returns action "reask" and logs nothing, the skill asks again. The
#   gates declared "free" (free input, options of a draft) keep a free answer.
#
# Options are quoted as written in the SKILL.md files (closed answer tokens
# shared with the skills, in English; the former French tokens are still accepted,
# see FRENCH_TOKENS); rationales are one line each.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from . import profiles

POLICY_FILE = Path(".claude") / "harness.json"
POLICY_KEY = "autonomy"
POLICIES = ("gate", "auto")
KINDS = ("a", "b", "c")
ASK, DEFAULT, STOP = "ask", "default", "stop"
DECISIONS = (ASK, DEFAULT, STOP)
SKIP = "skip"
HEADLESS = (STOP, DEFAULT, SKIP)
SOURCES = ("user", "default", "headless")
PHASES = ("decision", "answer")
AUTONOMY_SOURCES = ("flag", "stamp", "absent")


# Answer tokens are English. The former French tokens are still accepted from a user answer
# (a skill or a script written before the translation): `canonical` maps them, option by
# option, to the English token, so both forms give the same decision and the same journal.
FRENCH_TOKENS = {
    "approbation": "approval", "itérer": "iterate", "saisie": "input", "saisie libre": "free input",
    "installer": "install", "refus": "refuse", "livrer et lancer ship-pr": "deliver and run ship-pr",
    "ne pas livrer": "do not deliver", "commiter maintenant": "commit now", "abandonner": "abandon",
    "résoudre maintenant": "resolve now", "continuer la revue quand même": "continue the review anyway",
    "Merger": "Merge", "Laisser la PR ouverte": "Leave the PR open", "commande retenue": "chosen command",
    "repli local": "local fallback", "Tout hérité": "All inherited", "interprétation": "interpretation",
    "Sur-mesure": "Custom", "Questions pour le PI": "Questions for the PI", "risque d'abord": "risk first",
    "fondations": "foundations", "ordre proposé": "proposed order", "Jalon 1": "Milestone 1",
    "Jalon 2": "Milestone 2", "Jalon dernier": "Last milestone", "Exclure de la roadmap": "Exclude from roadmap",
    "Générer roadmap + specs": "Generate roadmap + specs", "Réajuster": "Readjust", "Abandonner": "Abandon",
    "lister": "list", "ignorer les mineures": "ignore minors", "fiabiliser": "make reliable",
    "moins cher": "cheaper", "feuille de route": "roadmap", "Toutes": "All",
    "Seulement la mieux classée": "Only the best ranked", "Aucune": "None",
    "Écrire AUDIT-lab.md": "Write AUDIT-lab.md", "Écrire AUDIT-vibecode.md": "Write AUDIT-vibecode.md",
    "Écrire AUDIT.md (+ specs)": "Write AUDIT.md (+ specs)", "confirmer le retrait": "confirm removal",
    "garder": "keep", "Gravité d'abord": "Severity first", "Gains rapides": "Quick wins",
    "Tout ce qui bloque": "Everything that blocks", "Garder l'ordre": "Keep the order",
    "Les détailler": "Detail them", "Les lister en une ligne": "List them in one line",
    "Les omettre": "Omit them", "profils proposés": "proposed profiles", "lancer la file": "run the queue",
    "ne pas lancer": "do not run", "Conserver tel quel": "Keep as is",
    "Convertir (sauvegarde)": "Convert (backup)", "Lancer l'upgrade": "Run the upgrade",
    "Ne rien faire": "Do nothing", "Écrire la synthèse": "Write the synthesis",
    "Ne rien écrire": "Write nothing", "Creuser": "Dig", "Écarter": "Discard", "Amender": "Amend",
    "continuer": "continue", "converger": "converge",
}


def canonical(value: str) -> str:
    """The answer with every former French option mapped to its English token (other text untouched)."""
    return " / ".join(FRENCH_TOKENS.get(p.strip(), p.strip()) for p in value.split(" / "))


class GateError(ValueError):
    """Unknown gate, invalid policy or out-of-sequence answer (exit 2)."""


class OffOptions(GateError):
    """User answer outside the closed options of a gate: ask again (never a stop)."""


class GateStop(RuntimeError):
    """Explicit stop decided by a gate (exit 1)."""


class Gate(NamedTuple):
    id: str
    source: str
    kind: str
    options: Tuple[str, ...]
    default: Optional[str]
    justification: str
    headless: str
    dialogue: bool = False
    free: bool = False
    questions: Tuple[Tuple[str, ...], ...] = ()
    repeat: int = 1
    proposal: bool = False
    stop_when: Optional[str] = None

    @property
    def rationale(self) -> str:
        return self.justification

    @property
    def skill(self) -> str:
        head = self.id.split(".", 1)[0]
        return SKILL_OF.get(head, head)

    @property
    def multi(self) -> bool:
        return len(self.questions) > 1 or self.repeat > 1


# GT-09: the journal of an `upgrade.*` gate is the one of the skill that upgrades.
SKILL_OF = {"upgrade": "software-factory"}


def _g(gid: str, source: str, kind: str, options: str, default: Optional[str], justification: str,
       headless: str, dialogue: bool = False, free: bool = False, repeat: int = 1,
       proposal: bool = False, stop_when: Optional[str] = None) -> Gate:
    questions = tuple(tuple(o.strip() for o in q.split(" / ")) for q in options.split(" ; "))
    flat = tuple(o for q in questions for o in q)
    return Gate(gid, source, kind, flat, default, justification, headless, dialogue, free, questions, repeat,
                proposal, stop_when)


CATALOG: Dict[str, Gate] = {g.id: g for g in (
    _g("software-factory.code-index", "software-factory:92", "b", "install / refuse", "refuse",
       "the knowledge base adds a versioned folder; installation stays possible at any time", SKIP),
    _g("factory-run.questions", "software-factory:54", "b", "draft options", "Planner default",
       "the planner's declared default for each non-blocking question; nothing is invented, a blocking "
       "question blocks the spec", STOP, free=True),
    _g("factory-run.approval", "software-factory:54", "c", "approval / iterate", "approval",
       "roadmap 4.2: approved without waiting, a blocking question having blocked the spec before", STOP),
    _g("factory-run.need", ".claude/skills/factory-run/SKILL.md:16", "c", "free input", None,
       "a need cannot be guessed", STOP, free=True),
    _g("factory-run.ship", "SPEC-phase4-5 CH-02", "c", "deliver and run ship-pr / do not deliver",
       "deliver and run ship-pr", "roadmap 4.2: a green build is delivered when CH-02 holds", SKIP),
    _g("ship-pr.target", "ship-pr:29", "c", "input", None,
       "the target comes from ship_pr.target of the stamp, else it cannot be guessed", STOP, free=True),
    _g("ship-pr.dirty", "ship-pr:46", "a", "commit now / stash / abandon", "abandon",
       "committing or stashing changes the cycle did not make is outside the accepted scope: abandon, "
       "the only option that acts on nothing unknown", DEFAULT, stop_when="always"),
    _g("ship-pr.conflict", "ship-pr:53", "b",
       "resolve now / continue the review anyway / abandon", "continue the review anyway",
       "non destructive, condition C5 then leaves the PR open", DEFAULT),
    _g("ship-pr.merge", "ship-pr:98", "a", "Merge / Leave the PR open", "Leave the PR open",
       "decided by SM-01: merge if and only if the five conditions hold, never a stop", DEFAULT,
       stop_when="never"),
    _g("code-index.rg-install", "code-index:38", "c", "chosen command / local fallback", "local fallback",
       "a system dependency is not installed unasked; local fallback reported", SKIP),
    _g("code-index.hooks", "code-index:113", "b", "hooks install / --with-commit / refuse", "hooks install",
       "the default stated by code-index:113", SKIP),
    _g("ai-lab.models", "ai-lab:137-142", "b", "All inherited / experts / interpretation / Custom",
       "All inherited", "no extra cost", DEFAULT),
    _g("ai-lab.code-index", "ai-lab:225", "b", "install / refuse", "refuse",
       "the knowledge base adds a versioned folder", SKIP),
    _g("lab-run.pi-questions", "ai-lab:180", "b", "Questions for the PI", None,
       "nothing to invent in the PI's place", STOP, free=True),
    _g("lab-run.preregistration", "ai-lab:180", "a", "approval / iterate", "approval",
       "roadmap 4.2 applied to the research artefact: pre-registered unless the protocol declares an "
       "unanswered blocking question", STOP, stop_when="input:blocking_unanswered"),
    _g("lab-run.consignation", "ai-lab:188", "c", "approval", "approval",
       "logged, marked 'to be reviewed' (ai-lab:188)", DEFAULT),
    _g("legacy-audit.framing", "legacy-audit:84", "b",
       "2 / 3 / 4 ; risk first / quick wins / foundations / proposed order", "3 / risk first",
       "close to 'make it reliable first' (roadmap 4.2)", STOP),
    _g("legacy-audit.assignment", "legacy-audit:85", "b",
       "Milestone 1 / Milestone 2 / Last milestone / Exclude from roadmap", None,
       "the computation belongs to Phase 4", STOP, repeat=4),
    _g("legacy-audit.final", "legacy-audit:87", "c", "Generate roadmap + specs / Readjust / Abandon",
       "Generate roadmap + specs", "the deliverable the audit was run for", STOP),
    _g("ai-lab-audit.framing", "ai-lab-audit:92", "b",
       "specs / list / ignore minors ; make reliable / cheaper / roadmap / proposed order",
       "specs / make reliable", "specs when a factory is present, otherwise list; make it reliable first",
       STOP),
    _g("ai-lab-audit.selection", "ai-lab-audit:93", "b", "All / Only the best ranked / None",
       "All", "the cap applies afterwards", STOP, repeat=4),
    _g("ai-lab-audit.final", "ai-lab-audit:95", "c", "Write AUDIT-lab.md / Readjust / Abandon",
       "Write AUDIT-lab.md", "the deliverable the audit was run for", STOP),
    _g("vibecode-audit.drop-security", "vibecode-audit:20", "b", "confirm removal / keep", "keep",
       "security is the priority axis", DEFAULT),
    _g("vibecode-audit.order", "vibecode-audit:115-116", "b",
       "Severity first / Quick wins / Everything that blocks / Keep the order ; Detail them / "
       "List them in one line / Omit them", "Severity first / Detail them",
       "marked default at :115; detailing loses nothing", DEFAULT),
    _g("vibecode-audit.final", "vibecode-audit:120", "c", "Write AUDIT-vibecode.md / Readjust / Abandon",
       "Write AUDIT-vibecode.md", "the deliverable the audit was run for", DEFAULT),
    # Phase 2: profiles, entry point and unified audit (AR-01, LP-05, AD-08).
    _g("software-factory.profiles", "SPEC-phase2 AR-01", "b", "proposed profiles / Abandon", "proposal",
       "a clear scout proposal is taken; an ambiguous one is never guessed", DEFAULT, free=True,
       proposal=True),
    _g("factory-run.profile", "SPEC-phase2 LP-05", "b", "profile of the need", "first profile of the stamp",
       "the first profile of the stamp is the first one retained at installation", STOP, free=True),
    _g("audit.profiles", "SPEC-phase2 AD-02", "b", "proposed profiles / Abandon", "proposal",
       "read-only scout verdict, used for this audit and never written to the stamp", DEFAULT, free=True,
       proposal=True),
    _g("audit.drop-security", "SPEC-phase2 AD-08", "b", "confirm removal / keep", "keep",
       "security is the priority axis", DEFAULT),
    _g("audit.final", "SPEC-phase2 AD-08", "c", "Write AUDIT.md (+ specs) / Readjust / Abandon",
       "Write AUDIT.md (+ specs)", "the deliverable the audit was run for", STOP),
    _g("audit.chain", "SPEC-phase4-5 CH-04", "c", "run the queue / do not run", "run the queue",
       "roadmap 4.2: the specs written are queued when a software factory is present", SKIP),
    # Phase 3: upgrade and doctor (CV-04, PR-02, PR-03, GT-09).
    _g("upgrade.convert", "SPEC-phase3 CV-04", "b", "Keep as is / Convert (backup)",
       "Keep as is", "a file with local changes is converted to blocks only when asked", DEFAULT),
    _g("harness-doctor.upgrade-blocks", "SPEC-phase3 PR-02", "b", "Run the upgrade / Do nothing",
       "Run the upgrade", "roadmap 4.2: a diff that only touches harness blocks is applied", DEFAULT),
    _g("harness-doctor.upgrade-other", "SPEC-phase3 PR-03", "b", "Run the upgrade / Do nothing",
       "Do nothing", "roadmap 4.2 reserves the automatic launch to diffs of blocks", DEFAULT),
    _g("harness-doctor.upgrade", "SPEC-phase4-5 UA-01", "b", "Run the upgrade / Do nothing",
       "Run the upgrade", "applied when UA-01 holds (blocks, stamp or untouched core only), otherwise proposed",
       SKIP),
    _g("brainstorm.subject", "brainstorm:16", "c", "free input", None,
       "a subject cannot be guessed", STOP, dialogue=True, free=True),
    _g("brainstorm.round", "brainstorm:67", "b", "Dig / Discard / Amend / continue / converge", None,
       "the user steers every round", STOP, dialogue=True, free=True),
    _g("brainstorm.write", "brainstorm:103", "c", "Write the synthesis / Write nothing", "Write nothing",
       "nothing is written unasked", STOP, dialogue=True),
)}


def profile_gates(profiles_dir=None) -> Dict[str, Gate]:
    """PF-05: the gates declared by the profiles (same fields as the core ones)."""
    found: Dict[str, Gate] = {}
    for prof in profiles.discover(profiles_dir).values():
        for g in prof.get("gates", []):
            found[g["id"]] = _g(g["id"], g.get("source", prof["name"]), g["kind"], g["options"],
                                 g.get("default"), g.get("justification") or g.get("rationale", ""),
                                 g["headless"], bool(g.get("dialogue")), bool(g.get("free")),
                                 int(g.get("repeat", 1)), bool(g.get("proposal")), g.get("stop_when"))
    return found


def all_gates(profiles_dir=None) -> Dict[str, Gate]:
    merged = dict(profile_gates(profiles_dir))
    merged.update(CATALOG)
    return merged


def get(gate_id: str) -> Gate:
    """GT-08: catalog lookup; an unknown id is a usage error."""
    gate = CATALOG.get(gate_id) or profile_gates().get(gate_id)
    if gate is None:
        raise GateError("unknown gate: %r (see the GT-08 catalog in hcore/gates.py)" % (gate_id,))
    return gate


def read_stamp_policy(root: Path) -> Tuple[str, str]:
    """GT-01: (policy, source) of the project: source `stamp` when the key is
    written, `absent` otherwise (missing file or key -> gate)."""
    path = Path(root) / POLICY_FILE
    if not path.exists():
        return "gate", "absent"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise GateError("%s is not readable JSON: %s" % (POLICY_FILE.as_posix(), exc))
    if not isinstance(data, dict):
        raise GateError("%s must contain a JSON object" % POLICY_FILE.as_posix())
    if POLICY_KEY not in data:
        return "gate", "absent"
    value = data[POLICY_KEY]
    if value not in POLICIES:
        raise GateError("%s: %s must be one of %s, got %r"
                        % (POLICY_FILE.as_posix(), POLICY_KEY, ", ".join(POLICIES), value))
    return value, "stamp"


def read_policy(root: Path) -> str:
    """GT-01: autonomy policy of the project (missing file or key -> gate)."""
    return read_stamp_policy(root)[0]


def effective_policy(root: Path, flag: Optional[str] = None) -> Tuple[str, str]:
    """AP-03: (value, source): the option wins over the stamp, which wins over
    the absent default. The flag never writes the stamp."""
    if flag is not None:
        if flag not in POLICIES:
            raise GateError("--autonomy must be one of %s, got %r" % ("|".join(POLICIES), flag))
        return flag, "flag"
    return read_stamp_policy(root)


def stamp_ship_target(root: Path) -> Optional[str]:
    """`ship_pr.target` of the stamp, when written."""
    try:
        data = json.loads((Path(root) / POLICY_FILE).read_text(encoding="utf-8-sig"))
        target = data["ship_pr"]["target"]
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
        return None
    return target if isinstance(target, str) and target.strip() else None


def decision(kind: str, policy: str) -> str:
    """AP-01, kinds b and c (and a under gate): the table without stop_when."""
    if kind not in KINDS:
        raise GateError("unknown gate kind: %r" % (kind,))
    if policy not in POLICIES:
        raise GateError("unknown policy: %r" % (policy,))
    if policy == "auto" and kind in ("b", "c"):
        return DEFAULT
    return ASK


def stop_condition(gate: Gate, inputs: Dict[str, Any]) -> bool:
    """AP-01: evaluation of the `stop_when` of a kind a gate on the skill's inputs."""
    rule = gate.stop_when or "never"
    if rule == "always":
        return True
    if rule == "never":
        return False
    if rule.startswith("input:"):
        return bool(inputs.get(rule[len("input:"):]))
    raise GateError("gate %s: unknown stop_when %r" % (gate.id, rule))


def decide(gate_id: str, policy: str, ambiguous: bool = False, inputs: Optional[Dict[str, Any]] = None,
           default: Optional[str] = None) -> Dict[str, Any]:
    """Decision for a gate under a policy: AP-01, GT-04 to GT-06 and AP-06.
    Returns the 'decision' journal event (policy, decision, default,
    justification, and for a kind a gate stop_when and its result)."""
    gate = get(gate_id)
    chosen = default if default is not None else gate.default
    if policy not in POLICIES:
        raise GateError("unknown policy: %r" % (policy,))
    result = None
    if gate.dialogue:
        choice = ASK
    elif gate.kind == "a" and policy == "auto":
        result = stop_condition(gate, inputs or {})
        choice = STOP if result else DEFAULT
    else:
        choice = decision(gate.kind, policy)
    if choice == DEFAULT and (chosen is None or (ambiguous and gate.proposal)):
        choice = STOP
    event: Dict[str, Any] = {
        "event": "gate", "phase": "decision", "id": gate.id, "kind": gate.kind, "policy": policy,
        "decision": choice, "default": chosen, "answer": None, "source": None,
        "headless": gate.headless, "justification": gate.justification,
    }
    if gate.kind == "a" and policy == "auto":
        event["stop_when"] = gate.stop_when
        event["stop_result"] = result
    if ambiguous and gate.proposal:
        event["ambiguous"] = True
    return event


def stop_message(gate: Gate, why: str) -> str:
    return "gate %s: %s: explicit stop" % (gate.id, why)


def check_option(gate: Gate, value: str) -> str:
    """A user answer to a gate with closed options: one option per question,
    in order, joined by " / " (OffOptions otherwise)."""
    if gate.free:
        return value
    original, value = value, canonical(value)
    parts =[p.strip() for p in value.split(" / ")] if gate.multi else [value]
    if gate.repeat > 1:
        expected = [gate.questions[0]] * len(parts) if 1 <= len(parts) <= gate.repeat else []
    else:
        expected = list(gate.questions) if len(parts) == len(gate.questions) else []
    if not expected or any(p not in options for p, options in zip(parts, expected)):
        shape = " ; ".join(" / ".join(q) for q in gate.questions)
        if gate.repeat > 1:
            shape = "1 to %d of: %s" % (gate.repeat, shape)
        elif len(gate.questions) > 1:
            shape = "one per question, in order, joined by ' / ': " + shape
        raise OffOptions("gate %s: answer %r is not one of the options (%s)" % (gate.id, original, shape))
    return value


def answer(gate_id: str, last_decision: Optional[Dict[str, Any]], source: str,
           text: Optional[str]) -> Dict[str, Any]:
    """'answer' journal event for the last decision of a gate (GT-06, GT-07).
    Raises GateError on an out-of-sequence or inconsistent answer."""
    gate = get(gate_id)
    if last_decision is None:
        raise GateError("gate %s: no pending decision in this run: call 'gate decide' first" % gate.id)
    choice = last_decision["decision"]
    if choice == STOP:
        raise GateError("gate %s: the decision was stop, no answer is expected" % gate.id)
    if source not in SOURCES:
        raise GateError("unknown answer source: %r" % (source,))
    if source == "default":
        if choice != DEFAULT:
            raise GateError("gate %s: decision is %s, a default answer is not allowed" % (gate.id, choice))
        value: Optional[str] = last_decision.get("default") or gate.default
    elif choice != ASK:
        raise GateError("gate %s: decision is %s, apply the default (--default)" % (gate.id, choice))
    elif source == "headless":
        # AR-01: an ambiguous proposal is never taken by default, headless or not.
        fallback = STOP if last_decision.get("ambiguous") else gate.headless
        value = {STOP: STOP, SKIP: SKIP, DEFAULT: gate.default}[fallback]
    else:
        value = (text or "").strip()
        if not value:
            raise GateError("gate %s: empty answer" % gate.id)
        value = check_option(gate, value)
    return dict(last_decision, phase="answer", answer=value, source=source)


def applied(gate_id: str, event: Dict[str, Any]) -> str:
    """What the skill applies after an answer: 'answer', 'default', 'skip' or 'stop'."""
    if event["source"] != "headless":
        return "default" if event["source"] == "default" else "answer"
    return STOP if event.get("answer") == STOP else get(gate_id).headless


def validate_event(obj: Dict[str, Any]) -> List[str]:
    """JR-02: schema of a 'gate' journal event (GT-07)."""
    errors: List[str] = []
    for key in ("phase", "id", "kind", "policy", "decision", "default", "answer", "source"):
        if key not in obj:
            errors.append("gate event: missing field: %s" % key)
    if errors:
        return errors
    if not isinstance(obj["id"], str) or not obj["id"].strip():
        errors.append("gate event: id must be a non-empty string")
    checks = (("phase", PHASES), ("kind", KINDS), ("policy", POLICIES), ("decision", DECISIONS))
    for key, allowed in checks:
        if obj[key] not in allowed:
            errors.append("gate event: %s must be one of %s" % (key, "|".join(allowed)))
    if obj["default"] is not None and not isinstance(obj["default"], str):
        errors.append("gate event: default must be a string or null")
    if obj["phase"] == "decision":
        if obj["answer"] is not None or obj["source"] is not None:
            errors.append("gate event: a decision carries neither answer nor source")
    elif obj["phase"] == "answer":
        if obj["source"] not in SOURCES:
            errors.append("gate event: source must be one of %s" % "|".join(SOURCES))
        if not isinstance(obj["answer"], str) or not obj["answer"].strip():
            errors.append("gate event: answer must be a non-empty string")
        if obj["decision"] == STOP:
            errors.append("gate event: a stop decision has no answer")
    return errors


def last_decision(records: List[Dict[str, Any]], gate_id: str) -> Optional[Dict[str, Any]]:
    """Last decision of `gate_id` not followed by its answer, or None."""
    for record in reversed(records):
        if record.get("event") == "gate" and record.get("id") == gate_id:
            return record if record.get("phase") == "decision" else None
    return None
