/* Which descriptors give the console size through the C library's
 * ioctl(TIOCGWINSZ), and what it returns when it fails. crossterm asks on
 * stdout; Limpet's own regression test asks on stdin.
 *     winsize-fds [LOG] */
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <proto/dos.h>
#include <dos/dosextens.h>

int main(int argc, char **argv)
{
    FILE *log = argc == 2 ? fopen(argv[1], "w") : NULL;
    struct FileHandle *in = BADDR(Input()), *out = BADDR(Output());
    char line[160];
    snprintf(line, sizeof line, "Input interactive=%ld Output interactive=%ld same port=%d\n",
             (long)IsInteractive(Input()), (long)IsInteractive(Output()),
             in && out && in->fh_Type == out->fh_Type);
    fputs(line, stdout);
    if (log) fputs(line, log);
    for (int fd = 0; fd <= 2; fd++) {
        struct winsize ws;
        memset(&ws, 0, sizeof ws);
        errno = 0;
        int rc = ioctl(fd, TIOCGWINSZ, &ws);
        snprintf(line, sizeof line, "fd %d: rc=%d errno=%d rows=%u cols=%u xpixel=%u ypixel=%u\n", fd, rc, errno,
                 (unsigned)ws.ws_row, (unsigned)ws.ws_col, (unsigned)ws.ws_xpixel, (unsigned)ws.ws_ypixel);
        fputs(line, stdout);
        if (log) fputs(line, log);
    }
    if (log) fclose(log);
    return 0;
}
