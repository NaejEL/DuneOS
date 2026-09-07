/*
 * LEG-41 — the pre-parse nesting bound on `.duneos_manifest`.
 *
 * Every input is built here, in the test body: nothing is read from a build
 * directory (LEG-38). The deep cases are also handed to the vendored cJSON with
 * its stock limit, to show that what the bound rejects is exactly what cJSON
 * would otherwise have descended into.
 */

#include "duneos/manifest_depth.h"
#include "tassert.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"

/* {"a":{"a":...{"a":1}...}} nested `levels` deep. Caller frees. */
static char *nested_object(unsigned levels)
{
    size_t cap = (size_t)levels * 8 + 16;
    char  *s   = malloc(cap);
    size_t n   = 0;

    for (unsigned i = 0; i < levels; i++) n += (size_t)sprintf(s + n, "{\"a\":");
    n += (size_t)sprintf(s + n, "1");
    for (unsigned i = 0; i < levels; i++) n += (size_t)sprintf(s + n, "}");
    return s;
}

static int depth_of(const char *json, unsigned max_depth, unsigned *out)
{
    return duneos_manifest_scan_depth(json, strlen(json), max_depth, out);
}

int main(void)
{
    unsigned d;

    /* The constant is a stack budget, not a taste: changing it is changing
     * main_task's peak by 64 B per level (duneos/manifest_depth.h). Pinned so
     * that raising it has to be a deliberate act that updates this test. */
    CHECK_INT(DUNEOS_MANIFEST_MAX_DEPTH, 4);

    /* Shapes dbt actually emits — depth 1 to 3. All must pass. */
    const char *flat =
        "{\"name\":\"hello_world\",\"version\":\"0.1.0\","
        "\"required_abi_version\":1,\"permissions\":98,\"stack_size\":8192,"
        "\"arch\":\"xtensa-esp32s3\"}";
    CHECK_INT(depth_of(flat, DUNEOS_MANIFEST_MAX_DEPTH, &d), 0);
    CHECK_INT(d, 1);

    const char *with_list =
        "{\"name\":\"tetris\",\"capabilities\":[\"display\"],"
        "\"sources\":[\"$SDK/display/gfx.c\",\"$SDK/ui/ui.c\"]}";
    CHECK_INT(depth_of(with_list, DUNEOS_MANIFEST_MAX_DEPTH, &d), 0);
    CHECK_INT(d, 2);

    const char *with_data =
        "{\"name\":\"i2cscope\",\"opens\":[\".scn\"],"
        "\"data\":[{\"src\":\"scenarios\",\"dst\":\"i2cscope\"}]}";
    CHECK_INT(depth_of(with_data, DUNEOS_MANIFEST_MAX_DEPTH, &d), 0);
    CHECK_INT(d, 3);

    /* Boundary: the limit itself passes, one level past it is rejected. */
    char *at_limit = nested_object(DUNEOS_MANIFEST_MAX_DEPTH);
    CHECK_INT(depth_of(at_limit, DUNEOS_MANIFEST_MAX_DEPTH, &d), 0);
    CHECK_INT(d, DUNEOS_MANIFEST_MAX_DEPTH);
    free(at_limit);

    char *past_limit = nested_object(DUNEOS_MANIFEST_MAX_DEPTH + 1);
    CHECK_INT(depth_of(past_limit, DUNEOS_MANIFEST_MAX_DEPTH, &d), -E2BIG);
    CHECK_INT(d, DUNEOS_MANIFEST_MAX_DEPTH + 1);
    free(past_limit);

    /*
     * The case the bound exists for, at a depth no legitimate manifest reaches
     * and pinned to a literal so that raising DUNEOS_MANIFEST_MAX_DEPTH, or
     * gutting the scan, fails here instead of passing quietly.
     */
    char  *deep     = nested_object(64);
    cJSON *by_cjson = cJSON_ParseWithLength(deep, strlen(deep));
    CHECK(by_cjson != NULL);        /* stock cJSON descends all 64 levels */
    cJSON_Delete(by_cjson);
    CHECK_INT(depth_of(deep, DUNEOS_MANIFEST_MAX_DEPTH, &d), -E2BIG);
    free(deep);

    /* Arrays recurse in cJSON exactly like objects. */
    char deep_array[3 * 64 + 2];
    size_t n = 0;
    for (int i = 0; i < 64; i++) deep_array[n++] = '[';
    deep_array[n++] = '1';
    for (int i = 0; i < 64; i++) deep_array[n++] = ']';
    deep_array[n] = '\0';
    CHECK_INT(depth_of(deep_array, DUNEOS_MANIFEST_MAX_DEPTH, &d), -E2BIG);

    /* Braces inside strings are text, not nesting — including escaped quotes
     * and escaped backslashes, or the scan would desynchronise and undercount. */
    CHECK_INT(depth_of("{\"icon\":\"{{{{{{{{{{\"}", DUNEOS_MANIFEST_MAX_DEPTH, &d), 0);
    CHECK_INT(d, 1);
    CHECK_INT(depth_of("{\"icon\":\"a\\\"{{{{{{{{{{b\"}", DUNEOS_MANIFEST_MAX_DEPTH, &d), 0);
    CHECK_INT(d, 1);
    CHECK_INT(depth_of("{\"icon\":\"a\\\\\",\"x\":[[1]]}", DUNEOS_MANIFEST_MAX_DEPTH, &d), 0);
    CHECK_INT(d, 3);

    /* Siblings are not cumulative: depth is the peak, not the count. */
    CHECK_INT(depth_of("{\"a\":{},\"b\":{},\"c\":{},\"d\":{},\"e\":{}}",
                       DUNEOS_MANIFEST_MAX_DEPTH, &d), 0);
    CHECK_INT(d, 2);

    /* Only `len` bytes are scanned — the manifest section is sized, not
     * NUL-delimited, and cJSON_ParseWithLength is called the same way. */
    const char *truncated = "{\"a\":1}[[[[[[[[[[";
    CHECK_INT(duneos_manifest_scan_depth(truncated, 7,
                                         DUNEOS_MANIFEST_MAX_DEPTH, &d), 0);
    CHECK_INT(d, 1);

    /* Degenerate inputs must not trip the scan. */
    CHECK_INT(duneos_manifest_scan_depth("", 0, DUNEOS_MANIFEST_MAX_DEPTH, &d), 0);
    CHECK_INT(d, 0);
    CHECK_INT(duneos_manifest_scan_depth(NULL, 0, DUNEOS_MANIFEST_MAX_DEPTH, &d), -EINVAL);
    CHECK_INT(depth_of("}}}}}}}}}}", DUNEOS_MANIFEST_MAX_DEPTH, &d), 0);
    CHECK_INT(depth_of("{\"a\":\"unterminated", DUNEOS_MANIFEST_MAX_DEPTH, &d), 0);
    CHECK_INT(duneos_manifest_scan_depth("[[[[[[[[[[", 10,
                                         DUNEOS_MANIFEST_MAX_DEPTH, NULL), -E2BIG);

    return t_report("manifest_depth");
}
