#pragma once

#include <stddef.h>

/*
 * /dev/shellpipe — a sink-backed character device for live-streaming a captured
 * app's stdout to its shell (ADR: captured-output streaming).
 *
 * A shell that runs an app captured registers a sink, then the loader points the
 * app's fd 1 at /dev/shellpipe. Every write the app makes is handed to the sink
 * as it happens — no /tmp spool, no read-back, no whole-output buffer. The sink
 * runs in the shell's own task (captured apps share it), so it may re-enter the
 * VFS to write to the console or render into a textview.
 */

/* Invoked with successive chunks of the captured app's output. Not NUL-
 * terminated; `len` is authoritative. `ctx` is the pointer passed to the setter. */
typedef void (*duneos_shell_sink_fn)(const char *data, int len, void *ctx);

/* Route /dev/shellpipe writes to `sink` (NULL discards). Set before a streamed
 * captured run and cleared after; not nestable — captured runs don't nest. */
void duneos_shellpipe_set_sink(duneos_shell_sink_fn sink, void *ctx);
