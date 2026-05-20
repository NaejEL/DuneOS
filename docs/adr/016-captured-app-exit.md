# ADR 016 — Captured-app exit semantics

**Status:** Accepted · 2026-05-20

## Context

DuneOS runs apps in two modes:

1. **Spawned** — `duneos_supervisor_launch(path)` creates a dedicated FreeRTOS task and slot for the app. The app owns its task; `duneos_exit(N)` ends the task and releases the slot. This is how `init.yaml` services run.

2. **Captured** — `duneos_loader_run_captured(app, &buf, &len)` loads the app into RAM, redirects `stdout` to `/tmp/.duneos_stdout`, calls `app->entry()` **as a regular function in the caller's task**, then reads back the captured output. This is how a shell runs `ls`, `gpio`, `i2cscope`, or any other bin-style command — the shell task itself executes the app's `app_main()`, no new task is created.

The two modes share the same `app_main()` entry, the same `<duneos/libdune.h>` API, the same `duneos_exit(N)` function. **The semantics of `duneos_exit(N)` differ catastrophically between them**, and that's the bug:

```c
void duneos_exit(int code) {
    duneos_supervisor_app_exited(code);
    while (1) {}
}

void duneos_supervisor_app_exited(int code) {
    /* ... xQueueSend to s_exit_queue ... */
    vTaskDelete(NULL);   // "kill the CURRENT task"
    while (1) {}
}
```

In a spawned app, "the current task" is the app's own task — correct.

In a captured app, "the current task" is the **shell task**. `duneos_exit(N)` kills the shell. The supervisor sees the shell die, restart-policy `always` kicks in, the shell respawns, and from the user's terminal the symptom is *"every time I run gfx_demo, my shell restarts."* The root cause that surfaced this — `malloc(64800)` failing in `gfx_demo` because no `heap_size:` was declared — was correct behaviour from `gfx_demo`, but the resulting `duneos_exit(1)` propagated as a shell kill instead of an error return.

This is a footgun. Apps written assuming POSIX `exit()` semantics ("end my process, leave my parent alone") get a destructive surprise the first time they fail under capture.

## Decision

`duneos_exit(N)` detects captured mode and unwinds back to the loader via `setjmp`/`longjmp` instead of killing the task. The shell survives; the app's exit code propagates to `loader_run_captured()`'s caller; output capture still works.

### Mechanism

```c
// kernel/duneos_loader/src/loader.c
static jmp_buf  *s_captured_jmp  = NULL;   // protected by s_captured_lock
static int       s_captured_code = 0;

int duneos_loader_run_captured(duneos_app_t *app, char **buf, size_t *len)
{
    jmp_buf env;
    osal_mutex_lock(&s_captured_lock);     // prevent nested captured runs
    s_captured_jmp  = &env;
    s_captured_code = 0;

    int val = setjmp(env);
    if (val == 0) {
        /* First entry: redirect stdout, run app. */
        capture_redirect_stdout();
        app->entry();
        s_captured_code = 0;               // app returned normally
    }
    /* setjmp returns non-zero when duneos_exit() longjmps back to us; the
     * exit code was stored in s_captured_code before the jump. */

    capture_restore_stdout();
    s_captured_jmp = NULL;
    osal_mutex_unlock(&s_captured_lock);

    return read_capture_buffer(buf, len, s_captured_code);
}

// kernel/duneos_kernel/src/supervisor.c
void duneos_exit(int code)
{
    if (s_captured_jmp != NULL) {
        s_captured_code = code;
        longjmp(*s_captured_jmp, 1);       // never returns; unwinds to setjmp
    }
    duneos_supervisor_app_exited(code);    // spawned mode — kills the task
    while (1) {}
}
```

`s_captured_jmp` is a single global because **captured runs do not nest**: a captured app calling `duneos_loader_run_captured` from inside its `app_main` would corrupt the jmp_buf and is therefore explicitly forbidden by `s_captured_lock`. The mutex returns `EBUSY` to a nested attempt; the inner caller sees the error and reports it.

### Shell auto-dispatch (spawned vs captured)

A subtler form of the same footgun: an app with a non-trivial heap allocation runs in captured mode and crashes silently because captured runs *don't honour `heap_size:` in the manifest* — they have no per-slot heap. The malloc falls back to the global kernel heap, which on a 320 KiB DRAM board is too fragmented to satisfy a 64 KiB request.

The shell's bin dispatch (`try_run_bin` in `apps/system/shell_core/shell_cmds.c`) therefore inspects the manifest after loading and **promotes the app to spawned mode if `manifest->heap_size > 0`**:

```c
const duneos_app_manifest_t *m = duneos_loader_get_manifest(app);
if (m && m->heap_size > 0) {
    duneos_loader_unload(app);
    duneos_supervisor_launch(path);   // spawned mode honours heap_size
    /* wait for the spawned app to exit */
    return;
}
duneos_loader_run_captured(app, ...);  // captured fast-path
```

This makes `heap_size: 81920` in `gfx_demo`'s manifest the user-facing toggle that switches the app to spawned execution. Side effect: spawned apps don't capture stdout — which is fine for graphical apps that draw rather than print, and is implicit in the very fact that they declared they need a heap.

The captured fast-path (no `heap_size`) keeps the cheap-and-fast model for shell utilities (`ls`, `cat`, `gpio`, …) that fit in the global heap.

### App-author contract

Captured apps must respect three constraints because the implementation cannot enforce them safely:

1. **No `pthread_create()` from captured mode.** A spawned thread inside the shell's task does not stop when `duneos_exit()` longjmps back — it keeps running, references heap that the loader is about to free, and corrupts the shell. The exit hook detects this if `s_thread_count > 0` and panics with a clear message rather than corrupting silently.

2. **Free what you allocate before exiting.** longjmp does not run destructors or `atexit` handlers. Heap allocations made from `app_main` and not freed before `duneos_exit` (or `return`) leak into the shell's slot heap. The shell's per-app heap eventually OOMs after enough captured runs misbehave. The loader's `app_unload()` releases the .dap-loaded sections (code, data, bss) regardless, so the leak is bounded to what the app `malloc`'d.

3. **Close every fd you open.** Same logic: longjmp keeps the fds alive. The shell's fd table fills up.

These are documented in `<duneos/libdune.h>`. Apps that need to spawn long-running work should be launched via `duneos_supervisor_launch` (spawned mode), not captured.

### Spawned mode is unchanged

`duneos_supervisor_launch` apps continue to call `duneos_exit(N)` and get the existing semantics: task deletion, slot cleanup, restart policy applied. `s_captured_jmp` is NULL in their context — `duneos_exit` falls through to `duneos_supervisor_app_exited` as before.

## Consequences

- **The footgun is removed.** A captured `gfx_demo` that fails to alloc its back-buffer prints an error, exits with code 1, the shell prints `bash: gfx_demo: exit 1` (or equivalent), and life continues. No restart loop, no surprise.

- **Output capture remains functional.** `loader_run_captured` always restores stdout after the call returns *or longjmps* — both code paths go through the same `capture_restore_stdout`.

- **Captured-app authors have a clear contract.** Documented in libdune.h header + a short blurb in CLAUDE.md. The list (no pthread, free your stuff, close your fds) is short enough to remember.

- **Implementation cost is small.** ~30 lines in loader.c + ~5 lines in supervisor.c. No change to the ABI, no change to apps that don't misbehave. `setjmp`/`longjmp` are part of PicoLibc; no extra dependency.

- **Tracking malloc/fds for automatic cleanup is left for later.** A future enhancement could record what an app opens/allocates during a captured run and reverse it on longjmp. That's a separate ADR if/when the leak becomes a problem in practice. Today the small per-shell heap caps the worst case; the shell auto-recovers via restart if it OOMs.

- **`atexit` is not supported in captured mode.** PicoLibc registers atexit handlers in a global table; longjmp bypasses them. We don't expose `atexit` to apps anyway, so this is just a documentation note.

## Alternatives

- **Forbid `duneos_exit()` in captured apps, document it as undefined behaviour** — rejected. Apps share source between captured and spawned use; making `duneos_exit` mode-dependent is exactly the trap we're trying to remove. The same code that fails as a spawned daemon should fail predictably as a captured command.

- **Force apps to use a new `duneos_app_return(N)` for captured exit** — rejected. Doubles the API surface; apps end up checking which mode they're in and dispatching. The whole point is that `duneos_exit` should Just Work.

- **Move the longjmp into a kernel-side syscall via `duneos_api_t`** — rejected. setjmp/longjmp live in the libc; the kernel-side state (`s_captured_jmp` pointer) is set inside the kernel by the loader before calling app code. The arrangement above already has the kernel/userspace boundary clean: app calls `duneos_exit` (kernel symbol), kernel reads its own state, kernel calls libc's longjmp on the kernel-stored env. No new ABI.

- **Always spawn a task for every app, kill captured mode** — rejected. Captured mode exists to let the shell read an app's output cheaply (no IPC). Replacing it with spawn + pipe + wait works but turns every `ls` into a multi-task dance with measurable latency. Captured is the right model; it just needs correct exit semantics.

- **Implement `wait()` POSIX-style and let captured apps run in a sub-task** — rejected for now. Demands a real process model (lightweight task + dedicated heap + fd table per app). Significant work, not necessary to fix the immediate bug. Could become a real Phase if multi-task captured runs ever matter.
