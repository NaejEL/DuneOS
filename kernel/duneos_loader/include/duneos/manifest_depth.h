#pragma once

/*
 * Pre-parse nesting bound for the embedded `.duneos_manifest` JSON — LEG-41,
 * specs/SPEC-leg-41-manifest-json-recursion.md.
 *
 * WHY THIS EXISTS
 *
 * cJSON's descent is mutually recursive (parse_value -> parse_object/parse_array
 * -> parse_value) and stops only at CJSON_NESTING_LIMIT. The manifest is file
 * content: a `.dap` any user can drop on the SD card, whose manifest the boot
 * scan parses on main_task without the file ever having been run. So the depth
 * of that descent is chosen by the file, not by us — and an overflow of
 * main_task is the LEG-37 failure mode (silent heap corruption, watchdog reboot
 * loop, never a stack report).
 *
 * The bound is therefore checked BEFORE cJSON is called, by a single
 * non-recursive pass over the same bytes (duneos_manifest_scan_depth), so an
 * over-nested manifest costs zero descent. It lives here, in our own code,
 * rather than as a patch to third_party/cjson/cJSON.h, which a submodule update
 * would silently drop.
 *
 * WHY THE LIMIT IS 4
 *
 * Frame sizes are the `entry a1, N` immediates of the -Os xtensa-esp32s3 build
 * (windowed-register save area included); re-derive them with objdump before
 * trusting them. The manifest chain out of duneos_loader_load(), with no I/O:
 *
 *   load(160) -> extract_manifest(64) -> cJSON_ParseWithLength(32)
 *     -> cJSON_ParseWithLengthOpts(80) -> parse_value(32)        = 368 B
 *   + 64 B per nesting level (parse_object|parse_array 32 + parse_value 32)
 *   + 48 B for the parse_string leaf
 *   => 416 + 64n bytes at nesting depth n.
 *
 * The budget: this chain must not become the deepest chain on the load path,
 * i.e. it must stay inside the 672 B fixed-depth maximum that already binds
 * duneos_loader_load() (load -> apply_relocations -> symbol_address ->
 * resolve_symbol -> klog_write; see the block comment above
 * duneos_loader_load() in loader.c). Then a manifest, whatever it contains,
 * consumes NONE of main_task's usable margin — measured on the m5stack-cardputer
 * at 784 B worst case (4276 B peak of a 5120 B stack, less the 60 B
 * end-of-stack watchpoint).
 *
 *   416 + 64n <= 672  =>  n <= 4
 *
 * So 4, the largest depth the budget allows. Neither figure below the chain is
 * a floor: klog_write's own callees and parse_string's are counted in neither,
 * which is one more reason to spend nothing above the existing maximum.
 *
 * Verified on the built image (xtensa-esp32s3-elf-objdump -d build/duneos.elf,
 * `entry a1, N`): extract_manifest still opens a 64 B frame with the gate in
 * it, and duneos_manifest_scan_depth is a 32 B leaf — so the check itself costs
 * load(160) + extract_manifest(64) + scan(32) = 256 B, and the parse behind it
 * tops out at the 672 B above. Giving extract_manifest an `unsigned depth` to
 * receive *out_depth pushed its frame to 80 B, i.e. the chain to 688 B, which is
 * why the loader passes NULL there.
 *
 * Headroom against real output: the 57 `.dap` manifests dbt builds today reach
 * depth 3 at most ({"data":[{"src":...}]} — object > array > object). Raising
 * this constant is raising main_task's peak by 64 B per level; do it from a
 * re-measured budget, not from a manifest that failed to load.
 *
 * The component also builds cJSON with -DCJSON_NESTING_LIMIT (loader's
 * CMakeLists.txt) as a second line of defence for any call site that forgets
 * this gate. It is a net, not the mechanism.
 */

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define DUNEOS_MANIFEST_MAX_DEPTH 4u

/*
 * Maximum JSON nesting depth of `json[0..len)`, computed in a single pass with
 * no recursion and O(1) stack, and bailing out as soon as `max_depth` is
 * exceeded.
 *
 * Returns 0 when the input nests no deeper than `max_depth`, -E2BIG otherwise.
 * `*out_depth` (optional) receives the deepest level reached — the peak on
 * success, the first offending level on rejection.
 *
 * This is a depth scan, not a validator: syntax is cJSON's job. For any input
 * cJSON accepts the count is exact, since a `"` outside a string cannot occur
 * in well-formed JSON and `\` escapes are honoured.
 */
int duneos_manifest_scan_depth(const char *json, size_t len,
                               unsigned max_depth, unsigned *out_depth);

#ifdef __cplusplus
}
#endif
