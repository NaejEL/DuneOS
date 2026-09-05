Status: PROPOSED

# SPEC-leg-20 — Documentation consistency and versioning batch

## Context

Grouping of milestone 3 findings, all concerning the gap between what the repository claims and
what it contains. One numbered acceptance criterion per finding.

- **LEG-20** (minor, XS) — `apps/user/g_shell/font8x8.h` and `sdk/display/include/duneos/font8x8.h`
  are identical byte for byte (md5 `a12ac3a4db3b56a50bd785a12f67899d`, 110 lines each). Any glyph fix
  applied on one side only produces divergent rendering with nothing to signal it.
- **LEG-21** (minor, XS) — the `## Build & Test Commands` section of `CLAUDE.md` (l.17-46) contains
  no test command, and the README has no Testing section. `make -C tests/host test` appears only in
  `.github/workflows/ci.yml`: CI knows how to test the project, the documentation aimed at humans and
  agents does not.
- **LEG-22** (minor, XS) — `README.md:23` states "(17 ADRs)" and `CLAUDE.md:274` "17 ADRs as of
  2026-05", while `docs/adr/` contains 40.
- **LEG-23** (minor, S) — no git tag across 213 commits, no VERSION file and no CHANGELOG, even
  though application manifests already expose a version contract (`version: "0.1.0"`,
  `required_abi_version: 3`) and `CMakeLists.txt:72` declares `project(duneos)` with no VERSION
  clause.

### LEG-20.5 (minor, XS) — `main/main.c:72` describes paths the code does not scan

Migrated here from SPEC-leg-33 during that spec's re-verification: it is a documentation defect,
not a build-system one, and it belongs with the wider `/flash/...` sweep described below.

```c
main/main.c:72   /* Legacy single-app boot: scan /sd/apps/, honour /sd/autoboot if present. */
main/main.c:73   static int launch_autoboot(void)
main/main.c:77       duneos_loader_scan(apps, DUNEOS_MAX_APPS, &count);
```

**CRITICAL — the obvious correction is itself wrong.** The instinct is to replace `/sd/apps/` with
"`/flash/bin` → `/sd/bin` → `/sd/apps`", which is what CLAUDE.md's "Boot order" says. That is
**also wrong**. The real scan list is:

```c
kernel/duneos_loader/src/loader.c:1358   /* Search order: flash (root /bin) takes priority over SD, bin/ over apps/. */
kernel/duneos_loader/src/loader.c:1359   static const char *const s_scan_dirs[] = {
kernel/duneos_loader/src/loader.c:1360       "/bin",
kernel/duneos_loader/src/loader.c:1361       "/sd/bin",
kernel/duneos_loader/src/loader.c:1362       "/sd/apps",
kernel/duneos_loader/src/loader.c:1363       NULL,
kernel/duneos_loader/src/loader.c:1364   };
```

`"/bin"`, **not `/flash/bin`** — because the flash LittleFS mounts at the **root**:
`kernel/duneos_kernel/src/vfs.c:31` defines `FLASH_MOUNT_POINT ""`, so `FLASH_MOUNT_POINT "/bin"`
concatenates to `"/bin"`. The function's own next log line already states the correct list and is
the text the comment must match:

```c
main/main.c:80   klog_w(TAG, "no apps found in /bin, /sd/bin or /sd/apps");
```

The `/sd/autoboot` clause is **CORRECT and stays**: `duneos_loader_select()`
(`kernel/duneos_loader/src/loader.c:1422-1447`) opens `DUNEOS_AUTOBOOT_FILE`, defined as
`"/sd/autoboot"` at `kernel/duneos_loader/include/duneos/loader.h:27`, reads an app name from it
and returns the matching entry, falling back to `list[0]`.

### LEG-20.6 (minor, XS) — the same stale `/flash/...` path convention survives across `docs/`

`main/main.c:72` is one instance of a repo-wide convention that predates the root mount. CLAUDE.md
was already corrected on `main` (commit `f705803`, "docs: correct the flash filesystem paths in
CLAUDE.md"); the rest was not swept. Verified remaining instances:

- `docs/qemu-test-bench.md:7` — "`/flash` mounted → `/flash/bin` scanned"
- `docs/qemu-test-bench.md:220-221` — "no `/flash/init.yaml`" / "scans `/flash/bin`"
- `docs/qemu-test-bench.md:259` — "`/flash` mounted, `/flash/bin` scanned"
- `docs/backlog.md:39` — "Threat model: `/flash/bin` is writable by any FS_WRITE app"
- `docs/cli-audit-2026-06.md:12` — "an ET_REL `.dap` in `/flash/bin` or `/sd/bin`"

**The distinction that keeps this from over-correcting:** the *concept* "/flash" remains valid as
the mount's name — `kernel/duneos_kernel/src/vfs.c:32` defines `FLASH_MOUNT_NAME` and
`vfs.c:148-149` registers and logs the mount under it. **Only literal filesystem paths are wrong.**
A sentence saying "the flash filesystem" or "`/flash` (LittleFS, `sysbin` partition)" as a name is
fine; `open("/flash/bin/foo.dap")` and "scans `/flash/bin`" are not.

> Note for the implementer: `FLASH_MOUNT_NAME` is `"/"`, not `"/flash"`
> (`kernel/duneos_kernel/src/vfs.c:32`). The registry/log name is the root itself. Do not "fix" a
> document by asserting that `/flash` is the registered mount name.

Three further tracked instances live in C comments rather than `docs/`, and are listed here so the
sweep is complete rather than left half-done: `kernel/duneos_kernel/src/init.c:160`, `init.c:205`
and `apps/user/launcher/launcher.c:49` all use `/flash/...` example paths. Whether they are in this
spec's diff is criterion 7's business; what matters is that they are not discovered later as a
fourth wave.

## Scope

Remove the font duplication, document the test commands, correct the ADR count, put a versioning
mechanism in place, and correct the stale boot-path documentation in `main/main.c` and across
`docs/`.

## Acceptance criteria

1. **LEG-20** — only one definition of `font8x8` remains in the repository. `apps/user/g_shell`
   consumes the SDK display header. `g_shell` builds and its on-screen text rendering is unchanged.
2. **LEG-21** — the `## Build & Test Commands` section of `CLAUDE.md` contains the host test command
   and the Python test command, and `README.md` has a section describing how to run the tests. Every
   documented command is executable as written from a clean checkout and returns a zero exit code on
   the current commit.
3. **LEG-22** — the ADR counts in `README.md` and `CLAUDE.md` match the actual number of files in
   `docs/adr/`. The chosen wording will not go stale at the next ADR added (either it quotes no
   number, or the number is verified by an automated check).
4. **LEG-23** — `CMakeLists.txt` declares an explicit version (`project(duneos VERSION x.y.z)`), a
   `CHANGELOG.md` exists and describes at minimum the current version, and the chosen versioning
   convention is documented, including its relationship with `DUNEOS_ABI_VERSION` (currently 3): the
   document must state what an ABI increment implies for the project version.
5. None of the documentation fixes introduces a new unverifiable claim: every number or path added
   matches the actual state of the repository at the time of writing.
6. **LEG-20.5** — `main/main.c:72`'s comment names the real scan order and contains **no path the
   function does not scan**. Specifically it names `/bin`, `/sd/bin` and `/sd/apps`, matching
   `main/main.c:80`'s existing log string; it contains no `/flash/bin`; and it keeps the
   `/sd/autoboot` clause, which is correct. `launch_autoboot()`'s body is byte-for-byte unchanged —
   the diff for this criterion touches comment lines only, no logic, no refactor of `main.c`.
7. **LEG-20.6** — the five listed `/flash/...` literal paths are corrected:
   `docs/qemu-test-bench.md:7`, `:220-221`, `:259`; `docs/backlog.md:39`;
   `docs/cli-audit-2026-06.md:12`. After the sweep, `grep -rn '/flash/' docs/` returns no line
   asserting a runtime filesystem path (occurrences describing the mount by name, or quoting a
   historical document verbatim, are permitted and must be individually justified in the change's
   record). Nothing that names `/flash` as the flash filesystem's *name* is changed — see the
   distinction in Context.

## Out of scope

Actually placing git tags on past history (a maintainer decision, outside a code change); rewriting
the README; a consistency review of the 40 ADRs (only the count is handled here); realigning ADR 012
(handled by LEG-17); adopting any particular changelog format; any change to `main/main.c`'s boot
logic or to any other comment in it; the REQUIRES over-declaration that shipped alongside LEG-20.5
in the original SPEC-leg-33 (that spec keeps it).

## Risks

Criterion 3 is the most fragile of the batch: changing "17" to "40" reproduces exactly the defect it
claims to fix, since the number will go stale at the next ADR. The wording must be chosen so as to
need no maintenance, failing which this finding will resurface at the next audit.

**Criteria 6 and 7 have a failure mode that is worse than the defect: replacing one wrong path with
another wrong path.** The obvious correction — writing `/flash/bin` — is exactly what the code does
*not* do, and it is what CLAUDE.md itself said until commit `f705803`. A freshly-touched comment is
trusted more than a visibly old one, so a wrong fix here costs more than the original. Both criteria
must be verified against `kernel/duneos_loader/src/loader.c:1359-1364` and
`kernel/duneos_kernel/src/vfs.c:31`, not against CLAUDE.md and not against each other. This exact
mistake already cost real diagnosis time during the QEMU bench work, when a reader of
`main/main.c:72` concluded that a `has_sd: false` board could not find its app — false, on a board
that finds it in `/bin` and always did.

## Open questions

Which initial version should be declared for the project, and what exact relationship with
`DUNEOS_ABI_VERSION`? The decision belongs to the maintainer and governs criterion 4.
