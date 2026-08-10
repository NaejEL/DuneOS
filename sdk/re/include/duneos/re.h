#pragma once

/*
 * re.h — tiny regular-expression engine for DuneOS CLI tools (grep, sed, find).
 *
 * A compact backtracking matcher, no malloc, compiled into a fixed static
 * buffer. Supported syntax (a useful POSIX-BRE subset):
 *
 *   .        any character
 *   ^ $      start / end of text anchors
 *   * + ?    greedy quantifiers (apply to the preceding atom)
 *   [abc]    character class      [^abc] negated      [a-z] range
 *   \d \D    digit / non-digit    \w \W word / non-word    \s \S space / non-space
 *   \<c>     escape a metachar to its literal (\. \* \[ …)
 *
 * Not supported (out of scope for an embedded coreutil): groups (), alternation
 * |, backreferences, lazy quantifiers. Use the scripting interpreter for those.
 *
 * Single static compile buffer → NOT reentrant; compile-then-match one pattern
 * at a time, which is exactly how the tools use it.
 */

typedef struct re_node *re_t;

/* Compile `pattern` into the engine's static buffer. Returns a handle, or NULL
 * if the pattern is malformed or too long. */
re_t re_compile(const char *pattern);

/* Find the first match of a compiled pattern in `text`. Returns the byte offset
 * of the match (>= 0) and writes its length to *matchlen, or -1 if no match. */
int re_matchp(re_t pattern, const char *text, int *matchlen);

/* Convenience: compile + match in one call (shares the static buffer). */
int re_match(const char *pattern, const char *text, int *matchlen);
