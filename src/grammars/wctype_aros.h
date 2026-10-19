/* Wide-character classification for tree-sitter grammar scanners on AROS.
 * The ABIv11 C library declares none of it (its wctype.h marks every
 * function not implemented), and scanners call iswspace, iswalpha and the
 * like. Force-included into each grammar source by scripts/hx.py. */
#ifndef HELIX_AROS_WCTYPE_H
#define HELIX_AROS_WCTYPE_H
#include <wctype.h>
int iswalnum(wint_t c);
int iswalpha(wint_t c);
int iswblank(wint_t c);
int iswdigit(wint_t c);
int iswlower(wint_t c);
int iswpunct(wint_t c);
int iswspace(wint_t c);
int iswupper(wint_t c);
int iswxdigit(wint_t c);
wint_t towlower(wint_t c);
wint_t towupper(wint_t c);
#endif
