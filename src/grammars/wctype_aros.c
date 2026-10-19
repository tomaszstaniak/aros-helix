/* See wctype_aros.h. ASCII is classified exactly. Above it, every character
 * except the Unicode spaces counts as a letter, which is what scanners need
 * to accept identifiers in other scripts; case and digits stay ASCII-only.
 * Weak, so a C library that gains these functions wins. */
#include "wctype_aros.h"

#define WEAK __attribute__((weak))

WEAK int iswspace(wint_t c)
{
    return c == ' ' || (c >= '\t' && c <= '\r') || c == 0x85 || c == 0xa0 ||
           c == 0x1680 || (c >= 0x2000 && c <= 0x200a) || c == 0x2028 ||
           c == 0x2029 || c == 0x202f || c == 0x205f || c == 0x3000;
}
WEAK int iswblank(wint_t c) { return c == ' ' || c == '\t'; }
WEAK int iswdigit(wint_t c) { return c >= '0' && c <= '9'; }
WEAK int iswxdigit(wint_t c) { return iswdigit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'); }
WEAK int iswupper(wint_t c) { return c >= 'A' && c <= 'Z'; }
WEAK int iswlower(wint_t c) { return c >= 'a' && c <= 'z'; }
WEAK int iswalpha(wint_t c) { return iswupper(c) || iswlower(c) || (c >= 0x80 && !iswspace(c)); }
WEAK int iswalnum(wint_t c) { return iswalpha(c) || iswdigit(c); }
WEAK int iswpunct(wint_t c) { return c > ' ' && c < 0x7f && !iswalnum(c); }
WEAK wint_t towlower(wint_t c) { return iswupper(c) ? c + 32 : c; }
WEAK wint_t towupper(wint_t c) { return iswlower(c) ? c - 32 : c; }
