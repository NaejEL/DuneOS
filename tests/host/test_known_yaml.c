/*
 * Copied-parser canary for the known.yaml grammar.
 *
 * WHY a verbatim copy: the parser lives inside app .c files that pull duneos
 * headers and cannot compile on host. The block below must stay byte-identical
 * to the daemon's (only KNOWN_YAML is redirected to a temp file). If the
 * daemon parser changes, re-sync this block — or better, extract one shared
 * parser into sdk/ and delete this copy.
 */

#include <ctype.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "known_yaml_cases.h"
#include "tassert.h"

static char s_yaml_path[256];
#define KNOWN_YAML s_yaml_path

#define MAX_KNOWN 16

typedef struct {
    char ssid[33];
    char psk[65];
} known_net_t;

/* SYNC: apps/system/wifi_daemon/wifi_daemon.c load_known — begin */

/* Trim whitespace and one pair of surrounding double quotes (psk: ""). */
static void copy_value(const char *src, char *dst, size_t dstsz) {
  while (*src && isspace((unsigned char)*src)) src++;
  size_t len = strlen(src);
  while (len > 0 && isspace((unsigned char)src[len - 1])) len--;
  if (len >= 2 && src[0] == '"' && src[len - 1] == '"') {
    src++;
    len -= 2;
  }
  if (len >= dstsz) len = dstsz - 1;
  memcpy(dst, src, len);
  dst[len] = '\0';
}

/* "- ssid:" opens an entry; the next "psk:" line (any indent) completes it. */
static int load_known_yaml(known_net_t *nets, int max) {
  int fd = open(KNOWN_YAML, O_RDONLY);
  if (fd < 0) return 0;

  char buf[2048]; /* covers 16 full entries, same as the UI's s_fbuf */
  ssize_t n = read(fd, buf, sizeof(buf) - 1);
  close(fd);
  if (n <= 0) return 0;
  buf[n] = '\0';

  int count = 0;
  int open_entry = -1;
  char *line = buf;
  while (line && *line) {
    char *nl = strchr(line, '\n');
    if (nl) *nl = '\0';

    char *p = line;
    while (*p && isspace((unsigned char)*p)) p++;
    if (*p && *p != '#') {
      if (strncmp(p, "- ssid:", 7) == 0) {
        open_entry = -1;
        if (count < max) {
          copy_value(p + 7, nets[count].ssid, sizeof(nets[count].ssid));
          nets[count].psk[0] = '\0';
          if (nets[count].ssid[0]) open_entry = count++;
        }
      } else if (open_entry >= 0 && strncmp(p, "psk:", 4) == 0) {
        copy_value(p + 4, nets[open_entry].psk, sizeof(nets[open_entry].psk));
        open_entry = -1;
      }
    }

    line = nl ? nl + 1 : NULL;
  }
  return count;
}

/* SYNC: apps/system/wifi_daemon/wifi_daemon.c load_known — end */

static void run_case(const ky_case_t *kc)
{
    FILE *f = fopen(s_yaml_path, "wb");
    if (!f) {
        fprintf(stderr, "  FAIL cannot write %s\n", s_yaml_path);
        t_run++;
        t_fail++;
        return;
    }
    fwrite(kc->input, 1, strlen(kc->input), f);
    fclose(f);

    known_net_t nets[MAX_KNOWN];
    memset(nets, 0, sizeof(nets));
    int n = load_known_yaml(nets, MAX_KNOWN);

    int before = t_fail;
    CHECK_INT(n, kc->count);
    int limit = n < kc->count ? n : kc->count;
    for (int i = 0; i < limit; i++) {
        CHECK_STR(nets[i].ssid, kc->nets[i].ssid);
        CHECK_STR(nets[i].psk,  kc->nets[i].psk);
    }
    if (t_fail != before)
        fprintf(stderr, "  ^ case: %s\n", kc->name);
}

int main(void)
{
    snprintf(s_yaml_path, sizeof(s_yaml_path),
             "known_yaml_test_%ld.tmp", (long)getpid());

    for (int i = 0; i < KY_NCASES; i++)
        run_case(&ky_cases[i]);

    unlink(s_yaml_path);
    return t_report("test_known_yaml");
}
