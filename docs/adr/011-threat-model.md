# ADR 011 — Threat model: permissions are advisory, not enforced

**Status:** Accepted · 2026-05-19

## Context

DuneOS apps declare permissions in their manifest (`permissions: 96` = `FS_READ | FS_WRITE`). The loader checks these at symbol-resolution time: if an app needs `printf` (POSIX I/O) but doesn't have `FS_WRITE`, the loader refuses to bind the symbol. This is reasonable as an *intent declaration* — it tells tooling and users what the app plans to do — but it must not be confused with a *security boundary*.

DuneOS runs on microcontrollers without an MMU. There is no virtual memory, no privileged/unprivileged CPU mode separation usable for sandboxing apps from each other or from the kernel. Concretely, an app loaded as a `.dap` can:

1. **Scan kernel memory** by reading from arbitrary addresses — `*(uint32_t *)0x40000000` works.
2. **Patch kernel code** by writing to IRAM (rare permission, but `heap_caps_malloc` may return a writable IRAM block).
3. **Overwrite another app's slot** — the supervisor's slot array is at a known kernel address; an attacker app reverse-engineers offsets.
4. **Resolve unauthorised symbols by address** — the loader doesn't bind them, but a malicious app could find them via memory scan and call them via raw function pointer.
5. **Persist a compromise** by writing `/flash/init.yaml` (if `FS_WRITE` granted, which any non-trivial app needs) so the next boot launches a backdoor service.

The `check_user_ptr` / `check_app_writable_ptr` validators (Phase 20) bound `read`/`write` system calls to the app's slot — they reject pointers outside it. But they only fire for kernel-side syscalls. An app that uses its own raw pointer arithmetic to read/write the supervisor's slot table bypasses them entirely.

There is no way to fix this short of adding an MMU (impossible on target hardware) or running a software-emulated MPU-like check on every memory access (catastrophic performance). The Flipper Zero, Playdate, and similar devices accept the same trade-off.

## Decision

**The DuneOS threat model is documented as: permissions are advisory, the system trusts every app it loads with full kernel privilege. Defence-in-depth is provided by:**

1. **Provenance** (Phase 32 — Ed25519 signature on `.dap` files). The kernel verifies the signature before loading. This establishes *who* wrote the app, not what it can do at runtime. Untrusted-source apps refused; trusted-source apps run with full power.
2. **Loader-side intent checking** (existing). The manifest's `permissions` bitmask is checked at load time and exposes the app's stated needs to user tooling and `dbt system check`. An app that requests no permissions still gets none of the *named* kernel symbols, but a determined adversary works around this.
3. **Pointer-bound syscall validation** (Phase 20 — `check_app_writable_ptr`). Stops *accidental* corruption from buggy apps, not *deliberate* corruption from malicious ones.
4. **Storage hygiene** (Phase 32 planned). Boot files (`/flash/init.yaml`) read by the supervisor are immutable from userspace by convention; runtime `write()` calls that target them log a klog warning. Not enforced, just visible.

**What DuneOS does NOT promise:**

- Memory isolation between apps. Two concurrent apps share an unprotected memory space.
- Memory isolation between apps and the kernel. The supervisor's data structures are reachable by any app.
- Filesystem access control between apps. Once `FS_WRITE` is granted, the app writes anywhere.
- Network policy enforcement. `NET` permission grants full BSD socket access; no per-port or per-host filtering.
- Resource quotas (CPU, memory, fd). A malicious app can exhaust any of these.

**Target user posture:**

DuneOS is suitable for: (a) running your own apps on your own hardware, (b) trusted ecosystems (vendor-signed apps in a curated store), (c) educational/hobby contexts where the user accepts the trust model.

DuneOS is NOT suitable for: (a) running untrusted apps from arbitrary sources without signature verification, (b) multi-tenant scenarios where apps belong to different parties with adversarial relationships, (c) compliance-bound contexts where regulators demand process isolation.

This is the same posture as the Flipper Zero, the Playdate SDK, the original Pebble OS, and most MCU app platforms. It is **not** the same as Android, iOS, or Linux user accounts.

## Consequences

- Phase 32 (signature verification) is the primary security feature — it gates *what runs*, not *what running code can do*. Plan it accordingly.
- Documentation (README.md, CLAUDE.md, eventually a SECURITY.md) must state this trade-off explicitly. The current README does not warn users; this is a doc bug to fix before publishing.
- Permission system stays — it's still useful for tooling (`dbt system check` validates apps against kernel profile capabilities, see Phase 25). Don't dismantle it.
- An app store (Phase 32) MUST require signed apps; running unsigned downloads in DuneOS is a footgun that the platform doesn't catch.
- Any future feature claiming "sandbox" or "isolation" must explain the mechanism — bare-metal handwaving is not acceptable. If a phase proposes one (e.g. RISC-V PMP on ESP32-P4 LP-core), it gets a new ADR superseding the relevant parts of this one.
- Bug bounty / vulnerability disclosure scope (if ever opened): kernel bugs that allow privilege *escalation across reboots* are in scope; bugs that allow privilege escalation within a session are NOT (because there's no privilege boundary to cross — everything already runs at full privilege).

## Alternatives

- **Claim "permissions = security boundary"** — rejected. Dishonest; the first researcher to look at the source code can write a 50-line PoC that bypasses every permission. Damages trust irreversibly.
- **Software memory protection (PMP / sanitizer-style)** — rejected. The performance hit on every memory access is unaffordable on a microcontroller. RISC-V PMP on capable cores may be revisited per-arch when relevant (ESP32-P4 LP-core, certain STM32H7 configurations).
- **Forbid `FS_WRITE` to `/flash/init.yaml` specifically** — rejected as primary defence. Workaroundable in five lines (write to a sibling path, rename). Useful as a klog tripwire, not as enforcement.
- **Sandboxing via runtime relocation tables** — rejected. Would require re-architecting the loader and `libdune.a` to interpose every memory access. Wrong layer to enforce isolation.
