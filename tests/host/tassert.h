#pragma once

#include <stdio.h>
#include <string.h>

static int t_run, t_fail;

#define CHECK(cond) do {                                                      \
    t_run++;                                                                  \
    if (!(cond)) {                                                            \
        t_fail++;                                                             \
        fprintf(stderr, "  FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);     \
    }                                                                         \
} while (0)

#define CHECK_INT(got, want) do {                                             \
    t_run++;                                                                  \
    long _g = (long)(got), _w = (long)(want);                                 \
    if (_g != _w) {                                                           \
        t_fail++;                                                             \
        fprintf(stderr, "  FAIL %s:%d  %s == %ld (want %ld)\n",               \
                __FILE__, __LINE__, #got, _g, _w);                            \
    }                                                                         \
} while (0)

#define CHECK_STR(got, want) do {                                             \
    t_run++;                                                                  \
    const char *_g = (got), *_w = (want);                                     \
    if (strcmp(_g, _w) != 0) {                                                \
        t_fail++;                                                             \
        fprintf(stderr, "  FAIL %s:%d  %s == \"%s\" (want \"%s\")\n",         \
                __FILE__, __LINE__, #got, _g, _w);                            \
    }                                                                         \
} while (0)

static int t_report(const char *name)
{
    fprintf(t_fail ? stderr : stdout, "%s: %d checks, %d failed\n",
            name, t_run, t_fail);
    return t_fail != 0;
}
