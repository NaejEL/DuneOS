/*
 * The shipped known.yaml parser (sdk/net/known_yaml.c), compiled here — not a
 * copy of it. The copy this file used to carry had already diverged on the
 * missing-file return, which is the value the daemon's /data → /etc seed
 * fallback branches on.
 */

#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "duneos/known_yaml.h"
#include "known_yaml_cases.h"
#include "tassert.h"

static char s_yaml_path[256];

static void write_file(const char *path, const char *text)
{
    FILE *f = fopen(path, "wb");
    if (!f) {
        fprintf(stderr, "  FAIL cannot write %s\n", path);
        t_run++;
        t_fail++;
        return;
    }
    fwrite(text, 1, strlen(text), f);
    fclose(f);
}

static void run_case(const ky_case_t *kc)
{
    write_file(s_yaml_path, kc->input);

    known_net_t nets[KNOWN_YAML_MAX_NETS];
    memset(nets, 0, sizeof(nets));
    int n = known_yaml_load(s_yaml_path, nets, KNOWN_YAML_MAX_NETS);

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

/* The daemon's seed fallback (wifi_daemon.c load_known) and iw's, spelled out:
 * -1 means "no file here, try the seed", 0 means "this file has no networks"
 * and stops the fallback. Collapsing the two silently disables first-boot
 * provisioning, which no on-device symptom names. */
static void test_missing_file_and_seed_fallback(void)
{
    char absent[300];
    snprintf(absent, sizeof(absent), "%s.absent", s_yaml_path);
    unlink(absent);

    known_net_t nets[KNOWN_YAML_MAX_NETS];
    memset(nets, 0, sizeof(nets));
    CHECK_INT(known_yaml_load(absent, nets, KNOWN_YAML_MAX_NETS), -1);

    write_file(s_yaml_path, "networks:\n");
    CHECK_INT(known_yaml_load(s_yaml_path, nets, KNOWN_YAML_MAX_NETS), 0);

    write_file(s_yaml_path, "- ssid: Seeded\n  psk: fromseed\n");

    int count = known_yaml_load(absent, nets, KNOWN_YAML_MAX_NETS);
    if (count < 0)
        count = known_yaml_load(s_yaml_path, nets, KNOWN_YAML_MAX_NETS);
    CHECK_INT(count, 1);
    CHECK_STR(nets[0].ssid, "Seeded");
    CHECK_STR(nets[0].psk, "fromseed");

    /* The mirror case: an existing but network-less /data file must NOT fall
     * back, or a user who removed their last network gets the seed back. */
    write_file(s_yaml_path, "networks:\n");
    count = known_yaml_load(s_yaml_path, nets, KNOWN_YAML_MAX_NETS);
    CHECK_INT(count, 0);
    CHECK(count >= 0);

    /* iw reads through known_yaml_read and parses in place; it needs the same
     * -1 to pick the seed, and a byte count that survives the parse. */
    char raw[KNOWN_YAML_BUF_SIZE];
    CHECK_INT(known_yaml_read(absent, raw, sizeof(raw)), -1);

    const char *text = "- ssid: Direct\n  psk: pd\n";
    write_file(s_yaml_path, text);
    CHECK_INT(known_yaml_read(s_yaml_path, raw, sizeof(raw)), (int)strlen(text));
    CHECK_INT(known_yaml_parse(raw, nets, KNOWN_YAML_MAX_NETS), 1);
    CHECK_STR(nets[0].ssid, "Direct");

    write_file(s_yaml_path, "");
    CHECK_INT(known_yaml_read(s_yaml_path, raw, sizeof(raw)), 0);
    CHECK_STR(raw, "");

    unlink(absent);
}

/* The legacy ssid:/password: grammar. Same helper, same trimming and quote
 * rules; only the keys differ. */
static void test_legacy_value_trimming(void)
{
    for (int i = 0; i < KY_NLEGACY; i++) {
        const ky_value_case_t *vc = &ky_legacy_values[i];
        char out[33];
        memset(out, 'x', sizeof(out));
        known_yaml_copy_value(vc->input, out, sizeof(out));
        int before = t_fail;
        CHECK_STR(out, vc->want);
        if (t_fail != before)
            fprintf(stderr, "  ^ case: %s\n", vc->name);
    }

    char small[4];
    known_yaml_copy_value("  abcdefgh  ", small, sizeof(small));
    CHECK_STR(small, "abc");
}

static void test_legacy_merge(void)
{
    for (int i = 0; i < KY_NLEGACY_CASES; i++) {
        const ky_legacy_case_t *lc = &ky_legacy_cases[i];
        write_file(s_yaml_path, lc->input);

        known_net_t nets[KNOWN_YAML_MAX_NETS];
        memset(nets, 0, sizeof(nets));
        int count = 0;

        int before = t_fail;
        CHECK_INT(known_yaml_merge_legacy(s_yaml_path, nets, &count,
                                          KNOWN_YAML_MAX_NETS), lc->appended);
        CHECK_INT(count, lc->appended);
        if (lc->appended) {
            CHECK_STR(nets[0].ssid, lc->net.ssid);
            CHECK_STR(nets[0].psk, lc->net.psk);
        }
        if (t_fail != before)
            fprintf(stderr, "  ^ legacy case: %s\n", lc->name);
    }

    known_net_t nets[KNOWN_YAML_MAX_NETS];
    memset(nets, 0, sizeof(nets));
    int count = 0;

    /* An absent migration file is simply nothing to merge. */
    char absent[300];
    snprintf(absent, sizeof(absent), "%s.absent", s_yaml_path);
    unlink(absent);
    CHECK_INT(known_yaml_merge_legacy(absent, nets, &count,
                                      KNOWN_YAML_MAX_NETS), 0);
    CHECK_INT(count, 0);

    /* Dedupe: the entry the user has since re-saved wins over the file they
     * migrated from, psk included. */
    write_file(s_yaml_path, "ssid: Dup\npassword: fromlegacy\n");
    snprintf(nets[0].ssid, sizeof(nets[0].ssid), "%s", "Dup");
    snprintf(nets[0].psk, sizeof(nets[0].psk), "%s", "current");
    count = 1;
    CHECK_INT(known_yaml_merge_legacy(s_yaml_path, nets, &count,
                                      KNOWN_YAML_MAX_NETS), 0);
    CHECK_INT(count, 1);
    CHECK_STR(nets[0].psk, "current");

    /* A full table has no room, and the merge must not write past it. */
    write_file(s_yaml_path, "ssid: Overflow\npassword: p\n");
    count = KNOWN_YAML_MAX_NETS;
    CHECK_INT(known_yaml_merge_legacy(s_yaml_path, nets, &count,
                                      KNOWN_YAML_MAX_NETS), 0);
    CHECK_INT(count, KNOWN_YAML_MAX_NETS);

    unlink(absent);
}

/* The read bound, which no fixture in the table is long enough to reach.
 * `read(fd, buf, bufsz)` instead of `bufsz - 1` writes the NUL one byte past
 * the caller's buffer; the guard byte below is what notices. */
static void test_buffer_bound_and_truncation(void)
{
    char oversize[KNOWN_YAML_BUF_SIZE * 2];
    memset(oversize, '#', sizeof(oversize));
    for (size_t i = 79; i < sizeof(oversize); i += 80)
        oversize[i] = '\n';
    oversize[sizeof(oversize) - 1] = '\0';
    write_file(s_yaml_path, oversize);

    struct { char buf[KNOWN_YAML_BUF_SIZE]; char guard; } framed;
    memset(&framed, 0, sizeof(framed));
    framed.guard = '\x7F';

    int n = known_yaml_read(s_yaml_path, framed.buf, sizeof(framed.buf));
    CHECK_INT(n, KNOWN_YAML_BUF_SIZE - 1);
    CHECK_INT(framed.buf[KNOWN_YAML_BUF_SIZE - 1], '\0');
    CHECK_INT(framed.guard, '\x7F');

    /* A file filling the buffer exactly: every entry still parses. */
    char full[KNOWN_YAML_BUF_SIZE];
    int o = 0;
    for (int i = 0; i < KNOWN_YAML_MAX_NETS; i++)
        o += snprintf(full + o, sizeof(full) - (size_t)o,
                      "- ssid: n%02d\n  psk: p%02d\n", i, i);
    int pad = KNOWN_YAML_BUF_SIZE - 1 - o;
    CHECK(pad > 2);
    full[o++] = '#';
    for (int i = 0; i < pad - 2; i++) full[o++] = 'x';
    full[o++] = '\n';
    full[o] = '\0';
    CHECK_INT(o, KNOWN_YAML_BUF_SIZE - 1);
    write_file(s_yaml_path, full);

    known_net_t nets[KNOWN_YAML_MAX_NETS];
    memset(nets, 0, sizeof(nets));
    CHECK_INT(known_yaml_load(s_yaml_path, nets, KNOWN_YAML_MAX_NETS),
              KNOWN_YAML_MAX_NETS);
    CHECK_STR(nets[0].ssid, "n00");
    CHECK_STR(nets[KNOWN_YAML_MAX_NETS - 1].psk, "p15");

    /* One byte over: the tail is cut mid-value and the truncated value is
     * what the daemon then tries to join with. Pinned, not endorsed. */
    const char *tail = "- ssid: Tail\n  psk: abcdefgh\n";
    int head = KNOWN_YAML_BUF_SIZE - 1 - (int)strlen("- ssid: Tail\n  psk: abc");
    char cut[KNOWN_YAML_BUF_SIZE * 2];
    cut[0] = '#';
    for (int i = 1; i < head - 1; i++) cut[i] = 'x';
    cut[head - 1] = '\n';
    snprintf(cut + head, sizeof(cut) - (size_t)head, "%s", tail);
    write_file(s_yaml_path, cut);

    memset(nets, 0, sizeof(nets));
    CHECK_INT(known_yaml_load(s_yaml_path, nets, KNOWN_YAML_MAX_NETS), 1);
    CHECK_STR(nets[0].ssid, "Tail");
    CHECK_STR(nets[0].psk, "abc");
}

int main(void)
{
    snprintf(s_yaml_path, sizeof(s_yaml_path),
             "known_yaml_test_%ld.tmp", (long)getpid());

    for (int i = 0; i < KY_NCASES; i++)
        run_case(&ky_cases[i]);

    test_missing_file_and_seed_fallback();
    test_legacy_value_trimming();
    test_legacy_merge();
    test_buffer_bound_and_truncation();

    unlink(s_yaml_path);
    return t_report("test_known_yaml");
}
