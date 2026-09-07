"""LEG-41 — the pre-parse manifest nesting bound must not reject dbt's own output.

The bound lives in kernel/duneos_loader/include/duneos/manifest_depth.h and is
enforced on the kernel side; what this suite owns is the other half of the
contract: every manifest dbt embeds today nests inside it, so tightening the
kernel could not brick a legitimate app.

The manifests are rebuilt here from the tracked sources (apps/**/duneos.yaml,
plus the legacy manifest.json) through the same json.dumps builder.py uses —
never read out of apps/**/build/, which is a gitignored artefact (LEG-38).
"""

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
HEADER = REPO_ROOT / "kernel" / "duneos_loader" / "include" / "duneos" / "manifest_depth.h"


def _declared_limit() -> int:
    m = re.search(r"#define\s+DUNEOS_MANIFEST_MAX_DEPTH\s+(\d+)u?",
                  HEADER.read_text(encoding="utf-8"))
    assert m, f"DUNEOS_MANIFEST_MAX_DEPTH not found in {HEADER}"
    return int(m.group(1))


def _json_depth(value, level: int = 1) -> int:
    """Nesting depth as the kernel's scan counts it: a flat object is 1."""
    if isinstance(value, dict) and value:
        return max(_json_depth(v, level + 1) for v in value.values())
    if isinstance(value, list) and value:
        return max(_json_depth(v, level + 1) for v in value)
    if isinstance(value, (dict, list)):
        return level
    return level - 1


def _manifest_sources():
    out = []
    for base in (REPO_ROOT / "apps" / "system", REPO_ROOT / "apps" / "user"):
        if not base.exists():
            continue
        for path in sorted(base.rglob("duneos.yaml")):
            out.append((path, yaml.safe_load(path.read_text(encoding="utf-8"))))
        for path in sorted(base.rglob("manifest.json")):
            if (path.parent / "duneos.yaml").exists():
                continue
            out.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return out


MANIFESTS = _manifest_sources()


def test_repo_has_manifests():
    assert len(MANIFESTS) >= 50, f"only {len(MANIFESTS)} app manifests found"


@pytest.mark.parametrize("path,manifest",
                         MANIFESTS,
                         ids=[str(p.relative_to(REPO_ROOT)) for p, _ in MANIFESTS])
def test_embedded_manifest_nests_within_the_bound(path, manifest):
    # builder.py embeds the manifest dict with the two scalars it resolves at
    # build time added; scalars cannot change the depth, they are added so the
    # blob under test is byte-shaped like the embedded one.
    embedded = dict(manifest)
    embedded["stack_size"] = 8192
    embedded["arch"] = "xtensa-esp32s3"
    blob = json.dumps(embedded, separators=(",", ":"))

    depth = _json_depth(json.loads(blob))
    assert depth <= _declared_limit(), (
        f"{path.relative_to(REPO_ROOT)} nests {depth} deep, over the kernel's "
        f"DUNEOS_MANIFEST_MAX_DEPTH={_declared_limit()} — the app would be "
        f"rejected at load"
    )


def test_bound_leaves_headroom_over_the_deepest_real_manifest():
    deepest = max(_json_depth(m) for _, m in MANIFESTS)
    assert deepest < _declared_limit(), (
        f"the deepest real manifest is {deepest} and the bound is "
        f"{_declared_limit()}: no headroom left for a new manifest shape"
    )
