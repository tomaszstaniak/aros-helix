/*
 * conexit: does a console program shaped like hx at exit raise "unexpected DOS
 * packet received" (0x07000004) when it is run again and again in one Shell?
 *
 *   conexit ORDER MS
 *
 * The console calls are hx's (crossterm's AROS event source over libc-aros):
 * the program's task sets raw mode with posixc tcsetattr() and turns on resize
 * reports; a detached pthread with a 16 MiB stack waits for input on the
 * Shell's input handle with WaitForChar(250 ms), reads what arrives, and asks
 * the console for its size with the CONG packet (ACTION_CONSOLE_GETGEOMETRY)
 * after every empty slice, as libc-aros's TIOCGWINSZ does. Meanwhile the task
 * writes a screen every 50 ms for MS milliseconds. Then, by ORDER:
 *
 *   A  ask the thread to stop (as dropping crossterm's EventStream does), set
 *      cooked mode, write the leaving sequences, return from main without
 *      joining: pthread's exit code cancels the thread and waits for it
 *   B  cooked mode and leaving sequences first, then ask the thread to stop,
 *      return without joining
 *   J  as A, but join the thread before returning (control)
 *
 * Status 0 when every call returned; 20 for a usage or setup failure. An alert
 * names the task that met the unexpected packet.
 */
#include <exec/types.h>
#include <exec/tasks.h>
#include <dos/dos.h>
#include <dos/dosextens.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>

#define ACTION_CONSOLE_GETGEOMETRY 0x434f4e47L

struct geometry {
    ULONG size, version, columns, rows, width, height;
};

static BPTR in_fh;
static volatile int stop;
static volatile long slices, reads, geometries, geometry_failures;

static void put(const char *s)
{
    size_t n = strlen(s);
    while (n) {
        ssize_t w = write(STDOUT_FILENO, s, n);
        if (w <= 0)
            return;
        s += w;
        n -= (size_t)w;
    }
}

static void ask_geometry(void)
{
    struct FileHandle *fh = BADDR(in_fh);
    struct geometry g;
    memset(&g, 0, sizeof g);
    if (DoPkt(fh->fh_Type, ACTION_CONSOLE_GETGEOMETRY, fh->fh_Arg1, (SIPTR)&g, sizeof g, 1, 0) == DOSTRUE)
        geometries++;
    else
        geometry_failures++;
}

static void *input_thread(void *arg)
{
    char buffer[256];
    (void)arg;
    while (!stop) {
        slices++;
        if (WaitForChar(in_fh, 250000)) {
            if (Read(in_fh, buffer, sizeof buffer) > 0)
                reads++;
        } else {
            ask_geometry();
        }
    }
    return NULL;
}

int main(int argc, char **argv)
{
    struct termios cooked, raw;
    pthread_attr_t attr;
    pthread_t thread;
    char order;
    long ms, t;
    char line[96];

    if (argc != 3 || strlen(argv[1]) != 1 || !strchr("ABJ", argv[1][0]) || (ms = atol(argv[2])) <= 0) {
        fprintf(stderr, "usage: conexit A|B|J MS\n");
        return 20;
    }
    order = argv[1][0];
    in_fh = Input();
    if (!in_fh || !IsInteractive(in_fh) || tcgetattr(STDIN_FILENO, &cooked) != 0) {
        fprintf(stderr, "conexit: input is not a console\n");
        return 20;
    }
    raw = cooked;
    /* What cfmakeraw() does; the SDK has no cfmakeraw. */
    raw.c_iflag &= ~(IGNBRK | BRKINT | PARMRK | ISTRIP | INLCR | IGNCR | ICRNL | IXON);
    raw.c_oflag &= ~OPOST;
    raw.c_lflag &= ~(ECHO | ECHONL | ICANON | ISIG | IEXTEN);
    raw.c_cflag &= ~(CSIZE | PARENB);
    raw.c_cflag |= CS8;
    if (tcsetattr(STDIN_FILENO, TCSANOW, &raw) != 0) {
        fprintf(stderr, "conexit: tcsetattr raw failed\n");
        return 20;
    }
    put("\x1b[?2048h\x1b[?1049h\x1b[?25l");

    pthread_attr_init(&attr);
    pthread_attr_setstacksize(&attr, 16 * 1024 * 1024);
    if (pthread_create(&thread, &attr, input_thread, NULL) != 0) {
        tcsetattr(STDIN_FILENO, TCSANOW, &cooked);
        fprintf(stderr, "conexit: pthread_create failed\n");
        return 20;
    }
    pthread_attr_destroy(&attr);
    if (order != 'J')
        pthread_detach(thread);

    for (t = 0; t < ms; t += 50) {
        snprintf(line, sizeof line, "\x1b[H\x1b[2Jconexit %c frame %ld\r\n", order, t / 50);
        put(line);
        usleep(50000);
    }

    if (order == 'B') {
        tcsetattr(STDIN_FILENO, TCSANOW, &cooked);
        put("\x1b[?25h\x1b[?1049l");
        stop = 1;
    } else {
        stop = 1;
        tcsetattr(STDIN_FILENO, TCSANOW, &cooked);
        put("\x1b[?25h\x1b[?1049l");
    }
    if (order == 'J')
        pthread_join(thread, NULL);
    snprintf(line, sizeof line, "conexit %c: slices %ld reads %ld geometry %ld/%ld, main end\n",
             order, slices, reads, geometries, geometries + geometry_failures);
    put(line);
    return 0;
}
