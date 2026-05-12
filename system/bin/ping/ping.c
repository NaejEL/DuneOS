#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <errno.h>
#include <time.h>

#include "duneos/socket.h"
#include "duneos/bin_args.h"

#define PING_DATA_LEN     56
#define PING_TIMEOUT_S    1
#define ICMP_ECHO_REQUEST 8
#define ICMP_ECHO_REPLY   0
#define PING_ID           0xD0E5   /* "DuneOS" */

struct icmp_pkt {
    uint8_t  type;
    uint8_t  code;
    uint16_t checksum;
    uint16_t id;
    uint16_t seq;
    uint8_t  data[PING_DATA_LEN];
};

static void out(const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    write(STDOUT_FILENO, buf, strlen(buf));
}

static uint16_t icmp_cksum(const void *data, size_t len)
{
    const uint16_t *p = data;
    uint32_t sum = 0;
    while (len > 1) { sum += *p++; len -= 2; }
    if (len) sum += *(const uint8_t *)p;
    while (sum >> 16) sum = (sum & 0xffff) + (sum >> 16);
    return (uint16_t)~sum;
}

void app_main(void)
{
    static char argbuf[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[4], *cwd;
    int argc = duneos_bin_args(argbuf, sizeof(argbuf), &cwd, argv, 4);
    (void)cwd;

    if (argc < 2) {
        out("usage: ping <host> [count]\r\n");
        return;
    }

    const char *host  = argv[1];
    int         count = (argc >= 3) ? atoi(argv[2]) : 4;
    if (count < 1) count = 4;

    struct addrinfo hints, *res = NULL;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family   = AF_INET;
    hints.ai_socktype = SOCK_RAW;

    if (getaddrinfo(host, NULL, &hints, &res) != 0 || !res) {
        out("ping: cannot resolve %s\r\n", host);
        return;
    }

    char ipstr[INET_ADDRSTRLEN] = {0};
    struct sockaddr_in *dst = (struct sockaddr_in *)res->ai_addr;
    inet_ntop(AF_INET, &dst->sin_addr, ipstr, sizeof(ipstr));
    out("PING %s (%s): %d data bytes\r\n", host, ipstr, PING_DATA_LEN);

    int fd = socket(AF_INET, SOCK_RAW, IPPROTO_ICMP);
    if (fd < 0) {
        out("ping: socket() failed (errno=%d)\r\n", errno);
        freeaddrinfo(res);
        return;
    }

    int sent = 0, received = 0;
    uint8_t rxbuf[128];

    for (int seq = 0; seq < count; seq++) {
        struct icmp_pkt pkt;
        memset(&pkt, 0, sizeof(pkt));
        pkt.type     = ICMP_ECHO_REQUEST;
        pkt.id       = htons(PING_ID);
        pkt.seq      = htons((uint16_t)seq);
        memset(pkt.data, 0x42, PING_DATA_LEN);
        pkt.checksum = icmp_cksum(&pkt, sizeof(pkt));

        struct timespec t0, t1;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        if (sendto(fd, &pkt, sizeof(pkt), 0, res->ai_addr, res->ai_addrlen) < 0) {
            out("ping: sendto failed\r\n");
            break;
        }
        sent++;

        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(fd, &rfds);
        struct timeval tv = { .tv_sec = PING_TIMEOUT_S, .tv_usec = 0 };

        if (select(fd + 1, &rfds, NULL, NULL, &tv) <= 0) {
            out("Request timeout for icmp_seq %d\r\n", seq);
            goto next;
        }

        struct sockaddr_in from;
        socklen_t fromlen = sizeof(from);
        ssize_t n = recvfrom(fd, rxbuf, sizeof(rxbuf), 0,
                             (struct sockaddr *)&from, &fromlen);
        clock_gettime(CLOCK_MONOTONIC, &t1);

        if (n < 0) { out("ping: recvfrom failed\r\n"); break; }

        /* Skip IP header — IHL field (low nibble of byte 0) in 32-bit words */
        int iphlen = (rxbuf[0] & 0x0f) * 4;
        if (n < iphlen + (int)sizeof(struct icmp_pkt)) goto next;

        struct icmp_pkt *rp = (struct icmp_pkt *)(rxbuf + iphlen);
        if (rp->type != ICMP_ECHO_REPLY
            || ntohs(rp->id)  != PING_ID
            || ntohs(rp->seq) != (uint16_t)seq) {
            out("Request timeout for icmp_seq %d\r\n", seq);
            goto next;
        }

        received++;
        int64_t rtt_us = (int64_t)(t1.tv_sec - t0.tv_sec) * 1000000LL
                         + (t1.tv_nsec - t0.tv_nsec) / 1000;
        uint8_t ttl = rxbuf[8];   /* IP TTL is at byte 8 in the IP header */

        char from_ip[INET_ADDRSTRLEN];
        inet_ntop(AF_INET, &from.sin_addr, from_ip, sizeof(from_ip));
        out("%d bytes from %s: icmp_seq=%d ttl=%d time=%d.%d ms\r\n",
            (int)(n - iphlen), from_ip, seq, ttl,
            (int)(rtt_us / 1000), (int)(rtt_us % 1000) / 100);

next:
        if (seq < count - 1) sleep(1);
    }

    out("\n--- %s ping statistics ---\r\n", host);
    out("%d packets transmitted, %d received, %d%% packet loss\r\n",
        sent, received,
        sent > 0 ? (sent - received) * 100 / sent : 0);

    close(fd);
    freeaddrinfo(res);
}
