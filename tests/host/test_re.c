#include "duneos/re.h"
#include "tassert.h"

#include <stdlib.h>
#include <string.h>

/* Returns match position; *len only written on success, so seed it. */
static int m(const char *pat, const char *txt, int *len)
{
    if (len) *len = -99;
    return re_match(pat, txt, len);
}

static void test_literals(void)
{
    int len;
    CHECK_INT(m("abc", "abc", &len), 0);      CHECK_INT(len, 3);
    CHECK_INT(m("abc", "xxabcxx", &len), 2);  CHECK_INT(len, 3);
    CHECK_INT(m("abc", "ab", &len), -1);
    CHECK_INT(m("abc", "", &len), -1);
    CHECK_INT(m("", "anything", &len), 0);    CHECK_INT(len, 0);
    CHECK_INT(m("", "", &len), 0);            CHECK_INT(len, 0);
    CHECK_INT(m("b", "", &len), -1);
    CHECK_INT(re_match("a", "za", NULL), 1);
}

static void test_dot(void)
{
    int len;
    CHECK_INT(m("a.c", "abc", &len), 0);      CHECK_INT(len, 3);
    CHECK_INT(m("a.c", "a c", &len), 0);
    CHECK_INT(m("a.c", "ac", &len), -1);
    CHECK_INT(m(".", "x", &len), 0);          CHECK_INT(len, 1);
    CHECK_INT(m(".", "", &len), -1);
    CHECK_INT(m("...", "ab", &len), -1);
}

static void test_anchors(void)
{
    int len;
    CHECK_INT(m("^ab", "abx", &len), 0);      CHECK_INT(len, 2);
    CHECK_INT(m("^ab", "xab", &len), -1);
    CHECK_INT(m("ab$", "cab", &len), 1);      CHECK_INT(len, 2);
    CHECK_INT(m("ab$", "abc", &len), -1);
    CHECK_INT(m("^ab$", "ab", &len), 0);      CHECK_INT(len, 2);
    CHECK_INT(m("^ab$", "aab", &len), -1);
    CHECK_INT(m("^$", "", &len), 0);          CHECK_INT(len, 0);
    CHECK_INT(m("^$", "x", &len), -1);
    CHECK_INT(m("^", "abc", &len), 0);        CHECK_INT(len, 0);
    CHECK_INT(m("$", "ab", &len), 2);         CHECK_INT(len, 0);
}

static void test_quantifiers(void)
{
    int len;
    CHECK_INT(m("ab*c", "ac", &len), 0);      CHECK_INT(len, 2);
    CHECK_INT(m("ab*c", "abc", &len), 0);     CHECK_INT(len, 3);
    CHECK_INT(m("ab*c", "abbbbc", &len), 0);  CHECK_INT(len, 6);
    CHECK_INT(m("x*", "yyy", &len), 0);       CHECK_INT(len, 0);
    CHECK_INT(m("ab*", "abbb", &len), 0);     CHECK_INT(len, 4);

    CHECK_INT(m("ab+c", "ac", &len), -1);
    CHECK_INT(m("ab+c", "abc", &len), 0);     CHECK_INT(len, 3);
    CHECK_INT(m("ab+c", "abbc", &len), 0);    CHECK_INT(len, 4);
    CHECK_INT(m("a+", "baaa", &len), 1);      CHECK_INT(len, 3);

    CHECK_INT(m("ab?c", "ac", &len), 0);      CHECK_INT(len, 2);
    CHECK_INT(m("ab?c", "abc", &len), 0);     CHECK_INT(len, 3);
    CHECK_INT(m("ab?c", "abbc", &len), -1);
    CHECK_INT(m("colou?r", "color", &len), 0);
    CHECK_INT(m("colou?r", "colour", &len), 0);
    CHECK_INT(m("a?", "", &len), 0);          CHECK_INT(len, 0);

    /* greedy: .* takes the longest match, backtracks to satisfy the rest */
    CHECK_INT(m("a.*c", "abcabc", &len), 0);  CHECK_INT(len, 6);
    CHECK_INT(m("a.*", "abc", &len), 0);      CHECK_INT(len, 3);
}

static void test_classes(void)
{
    int len;
    CHECK_INT(m("[abc]", "zb", &len), 1);         CHECK_INT(len, 1);
    CHECK_INT(m("[abc]+", "zzabca", &len), 2);    CHECK_INT(len, 4);
    CHECK_INT(m("[abc]", "xyz", &len), -1);
    CHECK_INT(m("[a-z]+", "HELLOworld", &len), 5); CHECK_INT(len, 5);
    CHECK_INT(m("[0-9a-fA-F]+", "zzDEadBE", &len), 2); CHECK_INT(len, 6);
    CHECK_INT(m("[^0-9]+", "12ab34", &len), 2);   CHECK_INT(len, 2);
    CHECK_INT(m("[^abc]", "abc", &len), -1);
    CHECK_INT(m("[\\d]+", "ab42", &len), 2);      CHECK_INT(len, 2);
    CHECK_INT(m("[a\\]]+", "]a]", &len), 0);      CHECK_INT(len, 3);
    CHECK_INT(m("[]", "x", &len), -1);            /* empty class matches nothing */
}

static void test_metachars(void)
{
    int len;
    CHECK_INT(m("\\d+", "abc123def", &len), 3);   CHECK_INT(len, 3);
    CHECK_INT(m("\\D+", "12ab34", &len), 2);      CHECK_INT(len, 2);
    CHECK_INT(m("\\w+", "!!a_9!!", &len), 2);     CHECK_INT(len, 3);
    CHECK_INT(m("\\W", "ab!", &len), 2);
    CHECK_INT(m("\\s", "ab cd", &len), 2);        CHECK_INT(len, 1);
    CHECK_INT(m("\\s+", "a \t\r\nb", &len), 1);   CHECK_INT(len, 4);
    CHECK_INT(m("\\S+", "  xy z", &len), 2);      CHECK_INT(len, 2);
    CHECK_INT(m("\\d", "abc", &len), -1);
}

static void test_escapes(void)
{
    int len;
    CHECK_INT(m("\\.", "a.b", &len), 1);          CHECK_INT(len, 1);
    CHECK_INT(m("\\.", "ab", &len), -1);
    CHECK_INT(m("\\*", "a*b", &len), 1);
    CHECK_INT(m("\\[", "x[", &len), 1);
    CHECK_INT(m("\\\\", "a\\b", &len), 1);
    CHECK_INT(m("a\\+b", "a+b", &len), 0);        CHECK_INT(len, 3);
    CHECK_INT(m("a\\+b", "aab", &len), -1);
}

static void test_compile_errors(void)
{
    CHECK(re_compile("abc\\") == NULL);           /* trailing backslash */
    CHECK(re_compile("[abc") == NULL);            /* unterminated class */
    CHECK(re_compile("[^ab") == NULL);

    char big_class[512];
    big_class[0] = '[';
    memset(big_class + 1, 'a', 300);
    big_class[301] = ']';
    big_class[302] = '\0';
    CHECK(re_compile(big_class) == NULL);         /* class buffer overflow */

    CHECK_INT(re_matchp(NULL, "text", NULL), -1);
}

static void test_node_limit(void)
{
    /* Node overflow must refuse to compile (re.h "too long -> NULL",
     * ADR 036) — a truncated program would silently match a prefix. */
    char pat[201];
    memset(pat, 'a', 200);
    pat[200] = '\0';
    CHECK(re_compile(pat) == NULL);

    /* Just under the limit still compiles. */
    char ok[95];
    memset(ok, 'a', 94);
    ok[94] = '\0';
    CHECK(re_compile(ok) != NULL);
}

static void test_pathological(void)
{
    int len;
    CHECK_INT(m("a*a*a*a*b", "aaaaaaaaaaaaaaaaaaaa", &len), -1);
    CHECK_INT(m("\\d*\\d*\\d*x", "123456789012345678", &len), -1);
    CHECK_INT(m("^[^ ]+ [^ ]+$", "one two three", &len), -1);
    CHECK_INT(m("a$b", "ab", &len), -1);          /* $ mid-pattern never matches */
}

static void test_realistic(void)
{
    int len;
    CHECK_INT(m("^\\s*#", "   # comment", &len), 0);
    CHECK_INT(m("^\\s*#", "code # trailing", &len), -1);
    CHECK_INT(m("\\w+\\.dap$", "run /sd/apps/snake.dap", &len), 13);
    CHECK_INT(len, 9);
    CHECK_INT(m("err[a-z]*: \\d+", "klog erro: 42x", &len), 5);
    CHECK_INT(len, 8);
}

int main(void)
{
    test_literals();
    test_dot();
    test_anchors();
    test_quantifiers();
    test_classes();
    test_metachars();
    test_escapes();
    test_compile_errors();
    test_node_limit();
    test_pathological();
    test_realistic();
    return t_report("test_re");
}
