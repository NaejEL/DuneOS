#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

#include <duneos/input_ioctl.h>

static void out(const char *s) { write(STDOUT_FILENO, s, strlen(s)); }
static void outn(const char *s) { out(s); out("\r\n"); }
static void outf(const char *fmt, ...)
{
    char buf[64]; va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap); out(buf);
}

static const char *code_name(uint16_t code)
{
    switch (code) {
    case KEY_BACKSPACE: return "BACKSPACE";
    case KEY_TAB:       return "TAB";
    case KEY_ENTER:     return "ENTER";
    case KEY_ESC:       return "ESC";
    case KEY_DELETE:    return "DELETE";
    case KEY_CTRL:      return "CTRL";
    case KEY_SHIFT:     return "SHIFT";
    case KEY_ALT:       return "ALT";
    case KEY_OPT:       return "OPT";
    case KEY_FN:        return "FN";
    case KEY_UP:        return "UP";
    case KEY_DOWN:      return "DOWN";
    case KEY_LEFT:      return "LEFT";
    case KEY_RIGHT:     return "RIGHT";
    default:            return NULL;
    }
}

void app_main(void)
{
    int fd = open("/dev/input/event0", O_RDONLY);
    if (fd < 0) {
        outn("input: /dev/input/event0 not available");
        return;
    }

    outn("[reading events -- press ESC to stop]");

    input_event_t ev;
    while (read(fd, &ev, sizeof(ev)) == (ssize_t)sizeof(ev)) {
        if (ev.type == INPUT_EV_KEY) {
            const char *name = code_name(ev.code);
            const char *action = (ev.value == INPUT_VAL_PRESS)  ? "press"   :
                                 (ev.value == INPUT_VAL_RELEASE) ? "release" : "repeat";
            if (name)
                outf("KEY  %-8s  %s\r\n", name, action);
            else if (ev.code >= 0x20 && ev.code < 0x7f)
                outf("KEY  '%c'      %s\r\n", (char)ev.code, action);
            else
                outf("KEY  0x%02x     %s\r\n", ev.code, action);
            if (ev.code == KEY_ESC && ev.value == INPUT_VAL_PRESS)
                break;
        } else if (ev.type == INPUT_EV_REL) {
            outf("REL  wheel    %+d\r\n", (int)ev.value);
        }
    }
    close(fd);
}
