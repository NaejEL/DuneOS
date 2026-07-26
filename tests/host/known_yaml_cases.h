#pragma once

/*
 * Tricky known.yaml inputs and the behavior the daemon parser exhibits today.
 * WHY a fixture table: the grammar has three hand-rolled parsers
 * (wifi_daemon, apps/user/wifi, iw) that must agree; until they are unified
 * behind one sdk/ parser, this table pins the daemon's behavior as reference.
 */

typedef struct {
    const char *ssid;
    const char *psk;
} ky_net_t;

typedef struct {
    const char *name;
    const char *input;
    int         count;
    ky_net_t    nets[16];
} ky_case_t;

#define KY_SSID32 "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"

#define KY_E(n) "- ssid: net" n "\n  psk: pw" n "\n"
#define KY_X(n) { "net" n, "pw" n }

static const ky_case_t ky_cases[] = {
    { "basic_two_entries",
      "networks:\n"
      "  - ssid: HomeNet\n"
      "    psk: hunter22\n"
      "  - ssid: Office\n"
      "    psk: w0rk\n",
      2, { { "HomeNet", "hunter22" }, { "Office", "w0rk" } } },

    { "orphan_psk_ignored",
      "networks:\n"
      "  psk: lonely\n"
      "  - ssid: A\n"
      "    psk: pa\n",
      1, { { "A", "pa" } } },

    { "second_psk_ignored",
      "- ssid: A\n"
      "  psk: one\n"
      "  psk: two\n",
      1, { { "A", "one" } } },

    { "entry_without_psk",
      "- ssid: A\n"
      "- ssid: B\n"
      "  psk: pb\n",
      2, { { "A", "" }, { "B", "pb" } } },

    { "comments_and_blanks",
      "# header comment\n"
      "\n"
      "- ssid: C1\n"
      "  # inner comment does not close the entry\n"
      "  psk: p1\n"
      "\n"
      "# tail\n",
      1, { { "C1", "p1" } } },

    { "missing_trailing_newline",
      "- ssid: Last\n"
      "  psk: end",
      1, { { "Last", "end" } } },

    { "crlf_line_endings",
      "networks:\r\n"
      "- ssid: CR\r\n"
      "  psk: LF\r\n",
      1, { { "CR", "LF" } } },

    { "ssid_32_chars_fits",
      "- ssid: " KY_SSID32 "\n"
      "  psk: x\n",
      1, { { KY_SSID32, "x" } } },

    { "ssid_33_chars_truncated",
      "- ssid: " KY_SSID32 "6\n"
      "  psk: x\n",
      1, { { KY_SSID32, "x" } } },

    { "quoted_values",
      "- ssid: \"My Net\"\n"
      "  psk: \"p w\"\n",
      1, { { "My Net", "p w" } } },

    { "empty_psk_value",
      "- ssid: E\n"
      "  psk:\n",
      1, { { "E", "" } } },

    { "quoted_empty_psk",
      "- ssid: E\n"
      "  psk: \"\"\n",
      1, { { "E", "" } } },

    { "empty_ssid_skipped",
      "- ssid:\n"
      "  psk: x\n",
      0, { { "", "" } } },

    { "empty_file",
      "",
      0, { { "", "" } } },

    { "seventeen_entries_capped_at_16",
      KY_E("01") KY_E("02") KY_E("03") KY_E("04") KY_E("05") KY_E("06")
      KY_E("07") KY_E("08") KY_E("09") KY_E("10") KY_E("11") KY_E("12")
      KY_E("13") KY_E("14") KY_E("15") KY_E("16") KY_E("17"),
      16,
      { KY_X("01"), KY_X("02"), KY_X("03"), KY_X("04"), KY_X("05"),
        KY_X("06"), KY_X("07"), KY_X("08"), KY_X("09"), KY_X("10"),
        KY_X("11"), KY_X("12"), KY_X("13"), KY_X("14"), KY_X("15"),
        KY_X("16") } },
};

#define KY_NCASES ((int)(sizeof(ky_cases) / sizeof(ky_cases[0])))
