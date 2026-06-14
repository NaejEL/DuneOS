/*
 * telnet_shell — the DuneOS shell over TCP (telnet), reusing shell_core.
 *
 * A network backend like usb_shell / uart_shell: it accepts a TCP connection
 * and hands the socket fd to shell_run(), so the whole shell (editor, pipes,
 * scripting, every tool) works unchanged over the network.
 *
 * Listens on INADDR_ANY → answers on whatever interface has an IP (WiFi or
 * Ethernet RMII). Cleartext, LAN-only, opt-in (ADR 011). SSH is the PSRAM-board
 * upgrade (see backlog).
 */

#define SHELL_BANNER     "DuneOS telnet"
#define SHELL_EOF_ON_ZERO            /* socket read()==0 ⇒ peer closed ⇒ end session */
#include "../shell_core/shell_core.c"

#include "duneos/socket.h"
#include "duneos/net.h"
#include "duneos/dlog.h"
#include <stdio.h>
#include <string.h>
#include <errno.h>

extern int  usleep(unsigned int useconds);
extern int  duneos_netif_wait_ip(uint32_t timeout_ms);

#define TELNET_PORT  23

void app_main(void)
{
    dlog_open("telnet");

    while (duneos_netif_wait_ip(3000) != 0) usleep(500000);

    int srv = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (srv < 0) { DLOGE("socket() failed errno=%d", errno); duneos_exit(2); }

    int one = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family      = AF_INET;
    a.sin_port        = htons(TELNET_PORT);
    a.sin_addr.s_addr = INADDR_ANY;

    if (bind(srv, (struct sockaddr *)&a, sizeof(a)) != 0) {
        DLOGE("bind :%d failed errno=%d", TELNET_PORT, errno);
        close(srv); duneos_exit(3);
    }
    if (listen(srv, 1) != 0) {
        DLOGE("listen failed errno=%d", errno);
        close(srv); duneos_exit(4);
    }
    DLOGI("listening on :%d", TELNET_PORT);

    for (;;) {
        struct sockaddr_in cli;
        socklen_t cl = sizeof(cli);
        int c = accept(srv, (struct sockaddr *)&cli, &cl);
        if (c < 0) { usleep(200000); continue; }

        unsigned char neg[] = { 255, 251, 1,   /* IAC WILL ECHO              */
                                255, 251, 3,   /* IAC WILL SUPPRESS-GO-AHEAD */
                                255, 253, 3 }; /* IAC DO   SUPPRESS-GO-AHEAD */
        write(c, neg, sizeof(neg));

        struct timeval tv = { .tv_sec = 0, .tv_usec = 100000 };
        setsockopt(c, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

        /* The client answers our negotiation with its own IAC burst; drain it
         * (SO_RCVTIMEO bounds the wait) so those 0xFF/option bytes don't land in
         * the shell as garbage at the first prompt. */
        char drain[64];
        while (read(c, drain, sizeof(drain)) > 0) { /* discard negotiation */ }

        shell_run(c, c);
        close(c);
    }
}
