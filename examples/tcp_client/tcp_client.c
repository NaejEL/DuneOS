#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

#include "duneos/wifi.h"
#include "duneos/socket.h"

extern void duneos_exit(int code);

#define HOST    "example.com"
#define PORT    "80"
#define REQUEST "GET / HTTP/1.0\r\nHost: " HOST "\r\nConnection: close\r\n\r\n"

static void out(const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    write(STDOUT_FILENO, buf, strlen(buf));
}

void app_main(void)
{
    out("tcp_client: waiting for network...\r\n");

    if (duneos_netif_wait_ip(15000) != 0) {
        out("tcp_client: no network after 15 s — is wifi_daemon running?\r\n");
        duneos_exit(1);
        return;
    }

    duneos_net_info_t info;
    if (duneos_wifi_get_info(&info) == 0)
        out("tcp_client: ip=%s  ssid=%s\r\n", info.ip, info.ssid);

    /* Resolve hostname */
    struct addrinfo hints;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family   = AF_INET;
    hints.ai_socktype = SOCK_STREAM;

    struct addrinfo *res = NULL;
    out("tcp_client: resolving %s:%s...\r\n", HOST, PORT);
    int rc = getaddrinfo(HOST, PORT, &hints, &res);
    if (rc != 0 || !res) {
        out("tcp_client: getaddrinfo failed (%d)\r\n", rc);
        duneos_exit(1);
        return;
    }

    /* Extract the resolved IP for display */
    char ipstr[INET_ADDRSTRLEN] = {0};
    struct sockaddr_in *sin = (struct sockaddr_in *)res->ai_addr;
    inet_ntop(AF_INET, &sin->sin_addr, ipstr, sizeof(ipstr));
    out("tcp_client: %s -> %s\r\n", HOST, ipstr);

    /* Connect */
    int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (fd < 0) {
        out("tcp_client: socket() failed\r\n");
        freeaddrinfo(res);
        duneos_exit(1);
        return;
    }

    out("tcp_client: connecting...\r\n");
    if (connect(fd, res->ai_addr, res->ai_addrlen) != 0) {
        out("tcp_client: connect() failed\r\n");
        freeaddrinfo(res);
        close(fd);
        duneos_exit(1);
        return;
    }
    freeaddrinfo(res);
    out("tcp_client: connected\r\n");

    /* Send HTTP GET */
    const char *req = REQUEST;
    ssize_t sent = send(fd, req, strlen(req), 0);
    out("tcp_client: sent %d bytes\r\n", (int)sent);

    /* Receive and print response (first 2 KB) */
    char buf[512];
    int total = 0;
    out("--- response ---\r\n");
    for (;;) {
        ssize_t n = recv(fd, buf, sizeof(buf) - 1, 0);
        if (n <= 0)
            break;
        buf[n] = '\0';
        write(STDOUT_FILENO, buf, n);
        total += (int)n;
        if (total >= 2048)
            break;
    }
    out("\r\n--- end (%d bytes) ---\r\n", total);

    close(fd);
    duneos_exit(0);
}
