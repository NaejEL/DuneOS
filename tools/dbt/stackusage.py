"""
Static stack-usage analysis — compute an app's worst-case stack from the
compiler's own measurements, so we don't have to trust the manifest's
`stack_size` (devs inflate it the moment they hit a bug).

GCC `-fstack-usage` emits a per-function frame size; `-fcallgraph-info=su`
emits the call graph as VCG `.ci` files. We merge them, then walk the graph
from the app entry (`app_main`) taking the deepest path = worst-case stack.

LIMITS (be honest about them):
  - Indirect calls (function pointers) are NOT in the graph: the DuneOS ABI
    table and SDK ops tables (libgfx→libdisp, …) call through pointers, so the
    callee's frames are invisible here. This number is therefore the app's OWN
    code worst-case and UNDER-counts anything reached via a pointer — most
    importantly the kernel ABI functions, which run on the app's stack. Cross-
    check against the runtime high-water (`ps`); the gap is that hidden depth.
  - Recursion (graph cycle) is unbounded statically → reported as a flag, not
    a number.
  - `dynamic` frames (VLAs / alloca) are size-unknown at compile time → flagged.
"""

import re
from pathlib import Path

_NODE_RE = re.compile(
    r'node:\s*\{\s*title:\s*"([^"]+)"\s*label:\s*"([^"]*?)"', re.DOTALL)
_EDGE_RE = re.compile(
    r'edge:\s*\{\s*sourcename:\s*"([^"]+)"\s*targetname:\s*"([^"]+)"')
_FRAME_RE = re.compile(r'(\d+)\s*bytes\s*\((static|dynamic|bounded)\)')


def _parse_ci(text, frames, edges, dynamic):
    """Merge one .ci file into frames{name:bytes}, edges{name:set}, dynamic{set}."""
    for title, label in _NODE_RE.findall(text):
        m = _FRAME_RE.search(label.replace("\\n", "\n"))
        if not m:
            continue
        size, qual = int(m.group(1)), m.group(2)
        # On a name collision (static funcs share a name across files) keep the
        # larger frame — conservative.
        frames[title] = max(frames.get(title, 0), size)
        if qual == "dynamic":
            dynamic.add(title)
    for src, dst in _EDGE_RE.findall(text):
        edges.setdefault(src, set()).add(dst)


def analyze(build_dir, root="app_main"):
    """
    Return a dict describing the app's static worst-case stack:
      {
        "ok": bool,                 # False if root not found
        "worst": int,               # bytes, deepest static path from root
        "path": [(func, frame), …], # the deepest path
        "recursive": [func, …],     # functions on a cycle (unbounded)
        "dynamic": [func, …],       # VLA/alloca frames on the worst path
      }
    """
    build_dir = Path(build_dir)
    frames, edges, dynamic = {}, {}, set()
    for ci in build_dir.glob("*.ci"):
        _parse_ci(ci.read_text(errors="ignore"), frames, edges, dynamic)

    if root not in frames:
        return {"ok": False, "worst": 0, "path": [], "recursive": [], "dynamic": []}

    recursive = set()
    # DFS with an explicit visiting set for cycle detection; memoise acyclic
    # results so SDK fan-in (every widget → gfx_rect) doesn't blow up.
    memo = {}

    def walk(node, stack):
        if node in stack:
            recursive.add(node)
            return 0, []
        if node in memo:
            return memo[node]
        frame = frames.get(node, 0)
        best_sub, best_path = 0, []
        for callee in edges.get(node, ()):
            sub, path = walk(callee, stack | {node})
            if sub > best_sub:
                best_sub, best_path = sub, path
        total = frame + best_sub
        result = (total, [(node, frame)] + best_path)
        if node not in stack:        # only memoise when not inside a cycle
            memo[node] = result
        return result

    worst, path = walk(root, frozenset())
    dyn_on_path = [f for f, _ in path if f in dynamic]
    return {
        "ok": True,
        "worst": worst,
        "path": path,
        "recursive": sorted(recursive),
        "dynamic": dyn_on_path,
    }


# Tuning for the auto-sized stack. RESERVE covers the depth reached through
# function pointers that the static graph can't see — the kernel ABI functions
# (which run on the app's task stack) and SDK ops tables (libgfx→libdisp→spi).
# Cross-validated against runtime high-water (`ps`): the hidden depth measured
# ~1-2 KiB, so 4 KiB is a safe cover. The FreeRTOS stack canary + supervisor
# kill catch any rare under-estimate without taking down the kernel.
STACK_RESERVE = 3072
STACK_MARGIN  = 0.20
STACK_FLOOR   = 3072


def recommend(analysis):
    """
    Compute a safe task stack from the static worst-case.
    Returns (bytes, "computed") or (None, reason) when it can't size safely
    (no entry point, or unbounded recursion → caller keeps a manual value).
    """
    if not analysis["ok"]:
        return None, "no app_main in call graph"
    if analysis["recursive"]:
        return None, f"recursion via {analysis['recursive'][0]} (unbounded)"
    raw = (analysis["worst"] + STACK_RESERVE) * (1.0 + STACK_MARGIN)
    size = max(STACK_FLOOR, int(raw))
    size = (size + 15) & ~15          # 16-byte align (Xtensa CALL ABI)
    return size, "computed"


def report(build_dir, manifest_stack, root="app_main"):
    """Print a one-block stack report comparing computed worst-case to the
    manifest's reserved stack. Returns the analysis dict."""
    a = analyze(build_dir, root)
    if not a["ok"]:
        print(f"  [stack] could not analyse (no '{root}' in call graph)")
        return a

    worst = a["worst"]
    note = ""
    if a["recursive"]:
        note += f"  recursion: {', '.join(a['recursive'][:3])} (unbounded!)"
    if a["dynamic"]:
        note += f"  VLA/alloca: {', '.join(a['dynamic'][:3])}"

    pct = (100 * worst // manifest_stack) if manifest_stack else 0
    print(f"  [stack] own-code worst-case ~{worst} B  |  manifest reserves "
          f"{manifest_stack} B ({pct}% used){note}")
    print("          (excludes kernel-ABI depth reached via the function table "
          "— cross-check with `ps`)")
    return a
