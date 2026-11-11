/*
 * libinput_fd — the fd side of libinput: open, query, read, translate.
 *
 * Separate TU so the core stays host-compilable (duneos/input.h).
 */

#include <fcntl.h>
#include <unistd.h>
#include <string.h>

#include "duneos/input.h"

#ifndef DUNEOS_INPUT_DEV
/* boardgen emits DUNEOS_INPUT_DEV only for a board declaring an input source.
 * The SDK must build and behave with it absent, so name the path the kernel
 * uses and let open() fail honestly on a board that has no such device. */
#define DUNEOS_INPUT_DEV "/dev/input/event0"
#endif

int duneos_input_open(duneos_input_t *in)
{
    if (!in) return -1;
    memset(in, 0, sizeof(*in));
    in->fd = open(DUNEOS_INPUT_DEV, O_RDONLY);
    if (in->fd < 0) return -1;

    /* A kernel built without the input driver answers ENOTTY. memset above
     * already left the empty map, which is the correct answer: nothing
     * translates, and no caller has to special-case the board. */
    if (ioctl(in->fd, INPUT_GET_TOPOLOGY, &in->topo) != 0 ||
        in->topo.version != INPUT_TOPOLOGY_VERSION) {
        memset(&in->topo, 0, sizeof(in->topo));
    }
    return 0;
}

void duneos_input_close(duneos_input_t *in)
{
    if (!in || in->fd < 0) return;
    close(in->fd);
    in->fd = -1;
}

int duneos_input_fd(const duneos_input_t *in)
{
    return in ? in->fd : -1;
}

int duneos_input_read(duneos_input_t *in, duneos_intent_t *out, int max_out)
{
    if (!in || in->fd < 0 || !out || max_out <= 0) return -1;

    input_event_t ev;
    if (read(in->fd, &ev, sizeof(ev)) != (int)sizeof(ev)) return -1;

    return duneos_input_translate(&in->topo, &in->st, &ev, out, max_out);
}
