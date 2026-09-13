/* known.yaml store — see <duneos/known_yaml.h>. */

#include "duneos/known_yaml.h"

#include <ctype.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>

void known_yaml_copy_value(const char *src, char *dst, size_t dstsz)
{
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

int known_yaml_parse(char *buf, known_net_t *nets, int max)
{
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
                    known_yaml_copy_value(p + 7, nets[count].ssid,
                                          sizeof(nets[count].ssid));
                    nets[count].psk[0] = '\0';
                    if (nets[count].ssid[0]) open_entry = count++;
                }
            } else if (open_entry >= 0 && strncmp(p, "psk:", 4) == 0) {
                known_yaml_copy_value(p + 4, nets[open_entry].psk,
                                      sizeof(nets[open_entry].psk));
                open_entry = -1;
            }
        }

        line = nl ? nl + 1 : NULL;
    }
    return count;
}

int known_yaml_read(const char *path, char *buf, size_t bufsz)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;

    ssize_t n = read(fd, buf, bufsz - 1);
    close(fd);
    if (n <= 0) n = 0;
    buf[n] = '\0';
    return (int)n;
}

int known_yaml_load(const char *path, known_net_t *nets, int max)
{
    char buf[KNOWN_YAML_BUF_SIZE];
    int n = known_yaml_read(path, buf, sizeof(buf));
    if (n < 0) return -1;
    return n ? known_yaml_parse(buf, nets, max) : 0;
}

int known_yaml_merge_legacy(const char *path, known_net_t *nets, int *count,
                            int max)
{
    char buf[256];
    if (known_yaml_read(path, buf, sizeof(buf)) <= 0) return 0;

    char ssid[KNOWN_YAML_SSID_CAP] = "", psk[KNOWN_YAML_PSK_CAP] = "";
    char *line = buf;
    while (line && *line) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = '\0';

        char *p = line;
        while (*p && isspace((unsigned char)*p)) p++;
        if (*p && *p != '#') {
            if (strncmp(p, "ssid:", 5) == 0)
                known_yaml_copy_value(p + 5, ssid, sizeof(ssid));
            else if (strncmp(p, "password:", 9) == 0)
                known_yaml_copy_value(p + 9, psk, sizeof(psk));
        }

        line = nl ? nl + 1 : NULL;
    }

    if (!ssid[0] || *count >= max) return 0;
    for (int i = 0; i < *count; i++)
        if (strcmp(nets[i].ssid, ssid) == 0) return 0;

    memcpy(nets[*count].ssid, ssid, sizeof(ssid));
    memcpy(nets[*count].psk, psk, sizeof(psk));
    (*count)++;
    return 1;
}
