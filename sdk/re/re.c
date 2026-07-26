/*
 * re.c — tiny backtracking regex engine. See <duneos/re.h>.
 *
 * A pattern compiles to a fixed array of nodes; matching is recursive with
 * greedy quantifiers. No heap, one static buffer (not reentrant).
 */

#include "duneos/re.h"

#include <stddef.h>

#define MAX_NODES      96
#define MAX_CLASS_BUF  192

enum {
    RE_UNUSED, RE_DOT, RE_BEGIN, RE_END,
    RE_QUESTION, RE_STAR, RE_PLUS,
    RE_CHAR, RE_CLASS, RE_NCLASS,
    RE_DIGIT, RE_NDIGIT, RE_ALNUM, RE_NALNUM, RE_SPACE, RE_NSPACE,
};

struct re_node {
    unsigned char        type;
    union {
        unsigned char    ch;     /* RE_CHAR */
        unsigned char   *cls;    /* RE_CLASS / RE_NCLASS → into s_classbuf */
    } u;
};

static struct re_node s_prog[MAX_NODES];
static unsigned char  s_classbuf[MAX_CLASS_BUF];

static int is_digit(char c) { return c >= '0' && c <= '9'; }
static int is_space(char c) { return c == ' ' || c == '\t' || c == '\n' ||
                                     c == '\r' || c == '\f' || c == '\v'; }
static int is_alpha(char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); }
static int is_alnum(char c) { return c == '_' || is_alpha(c) || is_digit(c); }

re_t re_compile(const char *pattern)
{
    int ni = 0;             /* node index   */
    int ci = 0;             /* class buffer  */
    char c;
    int i = 0;              /* pattern index */

    while (pattern[i] != '\0' && ni + 1 < MAX_NODES) {
        c = pattern[i];
        switch (c) {
        case '^': s_prog[ni].type = RE_BEGIN;    break;
        case '$': s_prog[ni].type = RE_END;      break;
        case '.': s_prog[ni].type = RE_DOT;      break;
        case '*': s_prog[ni].type = RE_STAR;     break;
        case '+': s_prog[ni].type = RE_PLUS;     break;
        case '?': s_prog[ni].type = RE_QUESTION; break;

        case '\\':
            i++;
            switch (pattern[i]) {
            case 'd': s_prog[ni].type = RE_DIGIT;  break;
            case 'D': s_prog[ni].type = RE_NDIGIT; break;
            case 'w': s_prog[ni].type = RE_ALNUM;  break;
            case 'W': s_prog[ni].type = RE_NALNUM; break;
            case 's': s_prog[ni].type = RE_SPACE;  break;
            case 'S': s_prog[ni].type = RE_NSPACE; break;
            case '\0': return NULL;              /* trailing backslash */
            default:
                s_prog[ni].type = RE_CHAR;
                s_prog[ni].u.ch = (unsigned char)pattern[i];
                break;
            }
            break;

        case '[': {
            int neg = 0;
            i++;
            if (pattern[i] == '^') { neg = 1; i++; }
            s_prog[ni].type   = neg ? RE_NCLASS : RE_CLASS;
            s_prog[ni].u.cls  = &s_classbuf[ci];
            while (pattern[i] != ']' && pattern[i] != '\0') {
                if (pattern[i] == '\\') {           /* keep the escape pair */
                    if (ci + 1 >= MAX_CLASS_BUF - 1) return NULL;
                    s_classbuf[ci++] = (unsigned char)pattern[i++];
                }
                if (ci >= MAX_CLASS_BUF - 1 || pattern[i] == '\0') return NULL;
                s_classbuf[ci++] = (unsigned char)pattern[i++];
            }
            if (pattern[i] != ']') return NULL;     /* unterminated class */
            s_classbuf[ci++] = '\0';
            break;
        }

        default:
            s_prog[ni].type = RE_CHAR;
            s_prog[ni].u.ch = (unsigned char)c;
            break;
        }
        i++;
        ni++;
    }
    /* Loop left before consuming the whole pattern → program full. A
     * truncated program silently matches a prefix of what the user wrote;
     * refuse instead (ADR 036: fail to compile rather than mis-match). */
    if (pattern[i] != '\0') return NULL;

    s_prog[ni].type = RE_UNUSED;
    return s_prog;
}

static int meta_match(char c, unsigned char meta)
{
    switch (meta) {
    case 'd': return is_digit(c);
    case 'D': return !is_digit(c);
    case 'w': return is_alnum(c);
    case 'W': return !is_alnum(c);
    case 's': return is_space(c);
    case 'S': return !is_space(c);
    default:  return c == (char)meta;     /* escaped literal */
    }
}

static int class_match(char c, const unsigned char *cls)
{
    while (*cls != '\0') {
        if (cls[0] == '\\') {
            if (meta_match(c, cls[1])) return 1;
            cls += 2;
        } else if (cls[1] == '-' && cls[2] != '\0') {     /* a-z range */
            if ((unsigned char)c >= cls[0] && (unsigned char)c <= cls[2]) return 1;
            cls += 3;
        } else {
            if ((unsigned char)c == cls[0]) return 1;
            cls += 1;
        }
    }
    return 0;
}

static int one_match(const struct re_node *n, char c)
{
    switch (n->type) {
    case RE_DOT:    return c != '\0';
    case RE_CHAR:   return (unsigned char)c == n->u.ch;
    case RE_CLASS:  return class_match(c, n->u.cls);
    case RE_NCLASS: return c != '\0' && !class_match(c, n->u.cls);
    case RE_DIGIT:  return is_digit(c);
    case RE_NDIGIT: return !is_digit(c);
    case RE_ALNUM:  return is_alnum(c);
    case RE_NALNUM: return !is_alnum(c);
    case RE_SPACE:  return is_space(c);
    case RE_NSPACE: return !is_space(c);
    default:        return 0;
    }
}

static int match_here(const struct re_node *p, const char *text, int *len);

/* Greedy: consume as many `atom` as possible, then backtrack to satisfy rest. */
static int match_star(const struct re_node *atom, const struct re_node *rest,
                      const char *text, int *len)
{
    const char *t = text;
    while (*t != '\0' && one_match(atom, *t)) t++;
    for (;;) {
        int sub = 0;
        if (match_here(rest, t, &sub)) { *len += (int)(t - text) + sub; return 1; }
        if (t == text) return 0;
        t--;
    }
}

static int match_plus(const struct re_node *atom, const struct re_node *rest,
                      const char *text, int *len)
{
    if (*text == '\0' || !one_match(atom, *text)) return 0;
    int sub = 0;
    if (match_star(atom, rest, text + 1, &sub)) { *len += 1 + sub; return 1; }
    return 0;
}

static int match_here(const struct re_node *p, const char *text, int *len)
{
    if (p[0].type == RE_UNUSED) return 1;
    if (p[0].type == RE_END && p[1].type == RE_UNUSED) return *text == '\0';

    if (p[1].type == RE_STAR)     return match_star(&p[0], &p[2], text, len);
    if (p[1].type == RE_PLUS)     return match_plus(&p[0], &p[2], text, len);
    if (p[1].type == RE_QUESTION) {
        int sub = 0;
        if (*text != '\0' && one_match(&p[0], *text) && match_here(&p[2], text + 1, &sub)) {
            *len += 1 + sub; return 1;
        }
        return match_here(&p[2], text, len);     /* zero occurrence */
    }

    if (*text != '\0' && one_match(&p[0], *text)) {
        int sub = 0;
        if (match_here(&p[1], text + 1, &sub)) { *len += 1 + sub; return 1; }
    }
    return 0;
}

int re_matchp(re_t prog, const char *text, int *matchlen)
{
    if (!prog) return -1;
    const struct re_node *p = prog;
    int anchored = (p[0].type == RE_BEGIN);
    if (anchored) p++;

    int pos = 0;
    do {
        int len = 0;
        if (match_here(p, text + pos, &len)) {
            if (matchlen) *matchlen = len;
            return pos;
        }
        pos++;
    } while (!anchored && text[pos - 1] != '\0');
    return -1;
}

int re_match(const char *pattern, const char *text, int *matchlen)
{
    return re_matchp(re_compile(pattern), text, matchlen);
}
