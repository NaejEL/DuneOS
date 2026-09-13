#pragma once

/*
 * Tricky known.yaml inputs and what sdk/net/known_yaml.c makes of them.
 *
 * The grammar used to have three hand-rolled parsers (wifi_daemon, iw,
 * apps/user/wifi) and they disagreed on two points; iw writes the file the
 * daemon reads, so a disagreement is a network saved but never joined. The
 * cases below named `former_iw_*` are those two, pinned at the daemon's
 * behaviour — they are the record of what changed for iw and the UI app.
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

    /* iw kept the entry, ssid "", and attached the next psk: to it — so a
     * hand-edited stray `- ssid:` shifted every subsequent psk by one. */
    { "former_iw_empty_ssid_drops_the_entry_and_its_psk",
      "- ssid: A\n"
      "  psk: pa\n"
      "- ssid:\n"
      "  psk: orphan\n"
      "- ssid: B\n"
      "  psk: pb\n",
      2, { { "A", "pa" }, { "B", "pb" } } },

    /* iw and the UI app trimmed space/tab only (plus CR at the end), so a
     * vertical tab or form feed stayed inside the ssid and the entry never
     * matched the scan result. isspace() is the daemon's class and now the
     * only one. */
    { "former_iw_whitespace_class_is_isspace",
      "\v- ssid:\v\fVT\f\v\n"
      "\f  psk:\f\vpw\v\f\n",
      1, { { "VT", "pw" } } },

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

/*
 * known_yaml_copy_value() on its own — the value rules both grammars share.
 * The legacy ssid:/password: line loop has its own cases below the table.
 */

typedef struct {
    const char *name;
    const char *input;
    const char *want;
} ky_value_case_t;

static const ky_value_case_t ky_legacy_values[] = {
    { "plain",              " MyNet",          "MyNet" },
    { "tabs_both_sides",    "\tMyNet\t",       "MyNet" },
    { "crlf_tail",          " MyNet\r",        "MyNet" },
    { "quoted_spaces",      " \"My Net\" ",    "My Net" },
    { "quoted_empty",       " \"\" ",          "" },
    { "single_quote_kept",  " \"half",          "\"half" },
    { "inner_quotes_kept",  " a\"b\"c ",        "a\"b\"c" },
    { "empty_value",        "",                "" },
    { "whitespace_only",    " \t\v\f ",        "" },
};

#define KY_NLEGACY ((int)(sizeof(ky_legacy_values) / sizeof(ky_legacy_values[0])))

/*
 * known_yaml_merge_legacy() — the one-network migration file wifi_daemon
 * folds in on top of known.yaml. `count` is what the merge appends to, so the
 * dedupe and the cap are part of the grammar's behaviour, not around it.
 */

typedef struct {
    const char *name;
    const char *input;
    int         appended;   /* 1 when the file contributed an entry */
    ky_net_t    net;        /* the appended entry, when appended */
} ky_legacy_case_t;

static const ky_legacy_case_t ky_legacy_cases[] = {
    { "plain", "ssid: Legacy\npassword: oldpw\n", 1, { "Legacy", "oldpw" } },

    { "indented_and_commented",
      "# migrated 2025\n"
      "  ssid: Indented\n"
      "\n"
      "  password: p\n",
      1, { "Indented", "p" } },

    { "quoted_values",
      "ssid: \"My Net\"\npassword: \"\"\n",
      1, { "My Net", "" } },

    { "password_absent_is_an_open_network",
      "ssid: Open\n", 1, { "Open", "" } },

    { "commented_out_ssid_contributes_nothing",
      "# ssid: NotThis\npassword: p\n", 0, { "", "" } },

    { "empty_ssid_contributes_nothing",
      "ssid:\npassword: p\n", 0, { "", "" } },

    { "last_key_wins",
      "ssid: First\nssid: Second\npassword: a\npassword: b\n",
      1, { "Second", "b" } },

    { "crlf_line_endings",
      "ssid: CR\r\npassword: LF\r\n", 1, { "CR", "LF" } },

    { "empty_file", "", 0, { "", "" } },
};

#define KY_NLEGACY_CASES \
    ((int)(sizeof(ky_legacy_cases) / sizeof(ky_legacy_cases[0])))
