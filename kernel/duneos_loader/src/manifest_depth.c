#include "duneos/manifest_depth.h"

#include <errno.h>
#include <stdbool.h>

int duneos_manifest_scan_depth(const char *json, size_t len,
                               unsigned max_depth, unsigned *out_depth)
{
    if (!json) return -EINVAL;

    unsigned depth = 0, peak = 0;
    bool in_string = false, escaped = false;

    for (size_t i = 0; i < len; i++) {
        const char c = json[i];

        if (in_string) {
            if (escaped)          escaped = false;
            else if (c == '\\')   escaped = true;
            else if (c == '"')    in_string = false;
            continue;
        }

        switch (c) {
        case '"':
            in_string = true;
            break;
        case '{':
        case '[':
            depth++;
            if (depth > peak) peak = depth;
            if (depth > max_depth) {
                if (out_depth) *out_depth = depth;
                return -E2BIG;
            }
            break;
        case '}':
        case ']':
            /* Underflow means malformed input, which cJSON will reject on its
             * own; clamping keeps the following levels counted from a sane
             * base instead of wrapping. */
            if (depth) depth--;
            break;
        default:
            break;
        }
    }

    if (out_depth) *out_depth = peak;
    return 0;
}
