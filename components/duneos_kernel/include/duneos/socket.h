#pragma once
/*
 * DuneOS BSD socket API — type definitions, constants, and function declarations.
 *
 * Apps compiled outside ESP-IDF do not have sys/socket.h or lwIP headers.
 * Include this header in place of any system socket headers.
 *
 * Layout matches ESP-IDF v5.x lwIP (Xtensa little-endian, LWIP_SOCKET_HAVE_SA_LEN=0):
 *   sa_family_t is u16_t — there is NO sin_len / sa_len field.
 *
 * Requires CONFIG_DUNEOS_DRV_WIFI=y — the kernel exports socket symbols only
 * when the WiFi driver is compiled in.  App manifest must include
 * DUNEOS_PERM_NET (1u << 4).
 */

#include <stdint.h>
#include <stddef.h>
#include <string.h>    /* memset — needed by FD_ZERO */
#include <sys/time.h>  /* struct timeval — needed by select() */
#include <sys/types.h> /* ssize_t */

/* ------------------------------------------------------------------ */
/* Address families                                                    */
/* ------------------------------------------------------------------ */
#define AF_UNSPEC   0
#define AF_INET     2
#define AF_INET6    10

/* ------------------------------------------------------------------ */
/* Socket types                                                        */
/* ------------------------------------------------------------------ */
#define SOCK_STREAM   1
#define SOCK_DGRAM    2
#define SOCK_RAW      3

/* ------------------------------------------------------------------ */
/* Protocol numbers                                                    */
/* ------------------------------------------------------------------ */
#define IPPROTO_IP    0
#define IPPROTO_TCP   6
#define IPPROTO_UDP   17
#define IPPROTO_RAW   255

/* ------------------------------------------------------------------ */
/* Option levels                                                       */
/* ------------------------------------------------------------------ */
#define SOL_SOCKET    0xfff

/* ------------------------------------------------------------------ */
/* SOL_SOCKET options                                                  */
/* ------------------------------------------------------------------ */
#define SO_REUSEADDR  0x0004
#define SO_KEEPALIVE  0x0008
#define SO_BROADCAST  0x0020
#define SO_SNDBUF     0x1001
#define SO_RCVBUF     0x1002
#define SO_SNDTIMEO   0x1005
#define SO_RCVTIMEO   0x1006
#define SO_ERROR      0x1007
#define SO_TYPE       0x1008
#define SO_NO_CHECK   0x100a

/* ------------------------------------------------------------------ */
/* IPPROTO_TCP options                                                 */
/* ------------------------------------------------------------------ */
#define TCP_NODELAY    0x01
#define TCP_KEEPIDLE   0x03
#define TCP_KEEPINTVL  0x04
#define TCP_KEEPCNT    0x05

/* ------------------------------------------------------------------ */
/* IPPROTO_IP options                                                  */
/* ------------------------------------------------------------------ */
#define IP_TOS              1
#define IP_TTL              2
#define IP_ADD_MEMBERSHIP   3
#define IP_DROP_MEMBERSHIP  4
#define IP_MULTICAST_TTL    5
#define IP_MULTICAST_IF     6
#define IP_MULTICAST_LOOP   7

/* ------------------------------------------------------------------ */
/* MSG flags                                                           */
/* ------------------------------------------------------------------ */
#define MSG_OOB       0x01
#define MSG_PEEK      0x02
#define MSG_WAITALL   0x08
#define MSG_DONTWAIT  0x40

/* ------------------------------------------------------------------ */
/* shutdown() how                                                      */
/* ------------------------------------------------------------------ */
#define SHUT_RD    0
#define SHUT_WR    1
#define SHUT_RDWR  2

/* ------------------------------------------------------------------ */
/* Special IPv4 addresses                                              */
/* ------------------------------------------------------------------ */
#define INADDR_ANY        ((uint32_t)0x00000000UL)
#define INADDR_LOOPBACK   ((uint32_t)0x7f000001UL)
#define INADDR_BROADCAST  ((uint32_t)0xffffffffUL)
#define INADDR_NONE       ((uint32_t)0xffffffffUL)

#define INET_ADDRSTRLEN   16   /* "255.255.255.255\0" */
#define INET6_ADDRSTRLEN  46

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */
typedef uint32_t  in_addr_t;
typedef uint16_t  in_port_t;
typedef uint16_t  sa_family_t;  /* u16_t — LWIP_SOCKET_HAVE_SA_LEN=0 */
typedef uint32_t  socklen_t;

struct in_addr {
    in_addr_t s_addr;
};

struct sockaddr {
    sa_family_t sa_family;
    char        sa_data[14];
};

/* Binary layout: family(2) + port(2) + addr(4) + zero(8) = 16 bytes */
struct sockaddr_in {
    sa_family_t    sin_family;
    in_port_t      sin_port;
    struct in_addr sin_addr;
    char           sin_zero[8];
};

/* Large enough for any supported address family */
struct sockaddr_storage {
    sa_family_t ss_family;
    char        ss_pad[26];
};

struct ip_mreq {
    struct in_addr imr_multiaddr;
    struct in_addr imr_interface;
};

/* ------------------------------------------------------------------ */
/* getaddrinfo / freeaddrinfo                                          */
/* ------------------------------------------------------------------ */
struct addrinfo {
    int              ai_flags;
    int              ai_family;
    int              ai_socktype;
    int              ai_protocol;
    socklen_t        ai_addrlen;
    struct sockaddr *ai_addr;
    char            *ai_canonname;
    struct addrinfo *ai_next;
};

#define AI_PASSIVE      0x01
#define AI_CANONNAME    0x02
#define AI_NUMERICHOST  0x04
#define AI_NUMERICSERV  0x08

/* ------------------------------------------------------------------ */
/* select() — fd_set                                                   */
/* lwIP FD_SETSIZE = 64; fd_mask = unsigned long (4 bytes on Xtensa)  */
/* ------------------------------------------------------------------ */
#define FD_SETSIZE  64

typedef unsigned long fd_mask;
#define NFDBITS  (8 * (int)sizeof(fd_mask))

typedef struct {
    fd_mask fds_bits[(FD_SETSIZE + NFDBITS - 1) / NFDBITS];
} fd_set;

#define FD_SET(n, p)   ((p)->fds_bits[(unsigned)(n) / NFDBITS] |=  (1UL << ((unsigned)(n) % NFDBITS)))
#define FD_CLR(n, p)   ((p)->fds_bits[(unsigned)(n) / NFDBITS] &= ~(1UL << ((unsigned)(n) % NFDBITS)))
#define FD_ISSET(n, p) ((int)(((p)->fds_bits[(unsigned)(n) / NFDBITS] >> ((unsigned)(n) % NFDBITS)) & 1))
#define FD_ZERO(p)     (memset((p), 0, sizeof(*(p))))

/* ------------------------------------------------------------------ */
/* poll()                                                              */
/* ------------------------------------------------------------------ */
struct pollfd {
    int   fd;
    short events;
    short revents;
};

typedef unsigned int nfds_t;

#define POLLIN    0x01
#define POLLOUT   0x04
#define POLLERR   0x08
#define POLLHUP   0x10
#define POLLNVAL  0x20

/* ------------------------------------------------------------------ */
/* Byte order — Xtensa ESP32-S3 is little-endian                      */
/* ------------------------------------------------------------------ */
#define htons(x)  __builtin_bswap16((uint16_t)(x))
#define ntohs(x)  __builtin_bswap16((uint16_t)(x))
#define htonl(x)  __builtin_bswap32((uint32_t)(x))
#define ntohl(x)  __builtin_bswap32((uint32_t)(x))

/* ------------------------------------------------------------------ */
/* Function declarations                                               */
/* close() / read() / write() / fcntl() on socket fds work via VFS   */
/* and are already exported — no need to redeclare them here.         */
/* ------------------------------------------------------------------ */
int     socket(int domain, int type, int protocol);
int     bind(int sockfd, const struct sockaddr *addr, socklen_t addrlen);
int     listen(int sockfd, int backlog);
int     accept(int sockfd, struct sockaddr *addr, socklen_t *addrlen);
int     connect(int sockfd, const struct sockaddr *addr, socklen_t addrlen);
ssize_t send(int sockfd, const void *buf, size_t len, int flags);
ssize_t recv(int sockfd, void *buf, size_t len, int flags);
ssize_t sendto(int sockfd, const void *buf, size_t len, int flags,
               const struct sockaddr *dest_addr, socklen_t addrlen);
ssize_t recvfrom(int sockfd, void *buf, size_t len, int flags,
                 struct sockaddr *src_addr, socklen_t *addrlen);
int     setsockopt(int sockfd, int level, int optname,
                   const void *optval, socklen_t optlen);
int     getsockopt(int sockfd, int level, int optname,
                   void *optval, socklen_t *optlen);
int     shutdown(int sockfd, int how);
int     select(int maxfdp1, fd_set *readset, fd_set *writeset,
               fd_set *exceptset, struct timeval *timeout);
int     poll(struct pollfd *fds, nfds_t nfds, int timeout);
int     getaddrinfo(const char *nodename, const char *servname,
                    const struct addrinfo *hints, struct addrinfo **res);
void    freeaddrinfo(struct addrinfo *ai);

in_addr_t   inet_addr(const char *cp);
char       *inet_ntoa_r(struct in_addr in, char *buf, int buflen);
const char *inet_ntop(int af, const void *src, char *dst, socklen_t size);
int         inet_pton(int af, const char *src, void *dst);
