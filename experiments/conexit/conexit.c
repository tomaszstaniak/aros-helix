/*
 * conexit: does a console program shaped like hx at exit raise "unexpected DOS
 * packet received" (0x07000004) when it is run again and again in one Shell?
 *
 *   conexit ORDER MS [LAYOUT]
 *
 * The console calls are hx's (crossterm's AROS event source over libc-aros):
 * the console owner sets raw mode with posixc tcsetattr() and turns on resize
 * reports; a detached pthread with a 16 MiB stack waits for input on the
 * Shell's input handle with WaitForChar(250 ms), reads what arrives, and asks
 * the console for its size with the CONG packet (ACTION_CONSOLE_GETGEOMETRY)
 * after every empty slice, as libc-aros's TIOCGWINSZ does. Meanwhile the owner
 * writes a screen every 50 ms for MS milliseconds. Then, by ORDER:
 *
 *   A  ask the input thread to stop (as dropping crossterm's EventStream
 *      does), set cooked mode, write the leaving sequences, return without
 *      joining it: pthread's exit code cancels the thread and waits for it
 *   B  cooked mode and leaving sequences first, then ask the thread to stop,
 *      return without joining
 *   J  as A, but join the input thread before returning (control)
 *
 * LAYOUT (default M):
 *
 *   M  the program's own task is the console owner
 *   E  as hx: main creates a 16 MiB "editor" thread that is the console
 *      owner and creates the input thread; main joins the editor thread and
 *      ends with exit()
 *
 * Each thread prints its role and Exec task address, and the input thread
 * the handle it waits on, so a packet trace (pkttrap) can name them.
 *
 * Status 0 when every call succeeded; 5 when a call failed after setup
 * (written to stderr, terminal restored); 20 for a usage or setup failure.
 * Shared flags are C11 atomics; the threads run on one CPU here, but nothing
 * relies on that. An alert names the task that met the unexpected packet.
 */
#include <exec/types.h>
#include <exec/tasks.h>
#include <dos/dos.h>
#include <dos/dosextens.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>

#define ACTION_CONSOLE_GETGEOMETRY 0x434f4e47L

struct geometry {
    ULONG size, version, columns, rows, width, height;
};

static char order, layout;
static long ms;
static BPTR in_fh;
static atomic_int stop;
static atomic_long slices, reads, wait_errors, read_errors, geometries, geometry_failures;
static atomic_int failures;

static void fail(const char *what, long code)
{
    fprintf(stderr, "conexit %c%c: %s failed (%ld)\n", order, layout, what, code);
    atomic_fetch_add(&failures, 1);
}

static void put(const char *s)
{
    size_t n = strlen(s);
    while (n) {
        ssize_t w = write(STDOUT_FILENO, s, n);
        if (w <= 0) {
            fail("write", w < 0 ? errno : 0);
            return;
        }
        s += w;
        n -= (size_t)w;
    }
}

static void say_role(const char *role)
{
    char line[96];
    snprintf(line, sizeof line, "conexit role %s task %p\n", role, (void *)FindTask(NULL));
    put(line);
}

static void ask_geometry(void)
{
    struct FileHandle *fh = BADDR(in_fh);
    struct geometry g;
    memset(&g, 0, sizeof g);
    if (DoPkt(fh->fh_Type, ACTION_CONSOLE_GETGEOMETRY, fh->fh_Arg1, (SIPTR)&g, sizeof g, 1, 0) == DOSTRUE)
        atomic_fetch_add(&geometries, 1);
    else
        atomic_fetch_add(&geometry_failures, 1);
}

static void *input_thread(void *arg)
{
    char buffer[256];
    char line[96];
    (void)arg;
    snprintf(line, sizeof line, "conexit role input task %p handle %p port %p\n", (void *)FindTask(NULL),
             (void *)BADDR(in_fh), (void *)((struct FileHandle *)BADDR(in_fh))->fh_Type);
    put(line);
    while (!atomic_load(&stop)) {
        atomic_fetch_add(&slices, 1);
        if (WaitForChar(in_fh, 250000)) {
            LONG got = Read(in_fh, buffer, sizeof buffer);
            if (got > 0)
                atomic_fetch_add(&reads, 1);
            else
                atomic_fetch_add(&read_errors, 1);
        } else if (IoErr() != 0) {
            atomic_fetch_add(&wait_errors, 1);
            SetIoErr(0);
        } else {
            ask_geometry();
        }
    }
    return NULL;
}

/* The console owner: hx's editor thread, or the program's task. */
static int run_console(void)
{
    struct termios cooked, raw;
    pthread_attr_t attr;
    pthread_t thread;
    long t;
    int e;
    char line[96];

    if (tcgetattr(STDIN_FILENO, &cooked) != 0) {
        fprintf(stderr, "conexit: tcgetattr failed (%d)\n", errno);
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
        fprintf(stderr, "conexit: tcsetattr raw failed (%d)\n", errno);
        tcsetattr(STDIN_FILENO, TCSANOW, &cooked);
        return 20;
    }
    put("\x1b[?2048h\x1b[?1049h\x1b[?25l");

    if ((e = pthread_attr_init(&attr)) != 0 || (e = pthread_attr_setstacksize(&attr, 16 * 1024 * 1024)) != 0
        || (e = pthread_create(&thread, &attr, input_thread, NULL)) != 0) {
        put("\x1b[?25h\x1b[?1049l");
        tcsetattr(STDIN_FILENO, TCSANOW, &cooked);
        fprintf(stderr, "conexit: input thread setup failed (%d)\n", e);
        return 20;
    }
    if ((e = pthread_attr_destroy(&attr)) != 0)
        fail("pthread_attr_destroy", e);
    if (order != 'J' && (e = pthread_detach(thread)) != 0)
        fail("pthread_detach", e);

    for (t = 0; t < ms; t += 50) {
        snprintf(line, sizeof line, "\x1b[H\x1b[2Jconexit %c%c frame %ld\r\n", order, layout, t / 50);
        put(line);
        usleep(50000);
    }

    if (order == 'B') {
        if (tcsetattr(STDIN_FILENO, TCSANOW, &cooked) != 0)
            fail("tcsetattr cooked", errno);
        put("\x1b[?25h\x1b[?1049l");
        atomic_store(&stop, 1);
    } else {
        atomic_store(&stop, 1);
        if (tcsetattr(STDIN_FILENO, TCSANOW, &cooked) != 0)
            fail("tcsetattr cooked", errno);
        put("\x1b[?25h\x1b[?1049l");
    }
    if (order == 'J' && (e = pthread_join(thread, NULL)) != 0)
        fail("pthread_join input", e);
    return 0;
}

static void *editor_thread(void *arg)
{
    say_role("editor");
    *(int *)arg = run_console();
    return NULL;
}

int main(int argc, char **argv)
{
    char line[160];
    int status = 0, e;

    if (argc < 3 || argc > 4 || strlen(argv[1]) != 1 || !strchr("ABJ", argv[1][0]) || (ms = atol(argv[2])) <= 0
        || (argc == 4 && (strlen(argv[3]) != 1 || !strchr("ME", argv[3][0])))) {
        fprintf(stderr, "usage: conexit A|B|J MS [M|E]\n");
        return 20;
    }
    order = argv[1][0];
    layout = argc == 4 ? argv[3][0] : 'M';
    in_fh = Input();
    if (!in_fh || !IsInteractive(in_fh)) {
        fprintf(stderr, "conexit: input is not a console\n");
        return 20;
    }
    say_role("main");

    if (layout == 'E') {
        pthread_attr_t attr;
        pthread_t editor;
        if ((e = pthread_attr_init(&attr)) != 0 || (e = pthread_attr_setstacksize(&attr, 16 * 1024 * 1024)) != 0
            || (e = pthread_create(&editor, &attr, editor_thread, &status)) != 0) {
            fprintf(stderr, "conexit: editor thread setup failed (%d)\n", e);
            return 20;
        }
        if ((e = pthread_attr_destroy(&attr)) != 0)
            fail("pthread_attr_destroy editor", e);
        if ((e = pthread_join(editor, NULL)) != 0)
            fail("pthread_join editor", e);
    } else {
        status = run_console();
    }
    if (status == 20)
        return 20;

    snprintf(line, sizeof line,
             "conexit %c%c: slices %ld reads %ld wait errors %ld read errors %ld geometry %ld/%ld failures %d, main end\n",
             order, layout, atomic_load(&slices), atomic_load(&reads), atomic_load(&wait_errors),
             atomic_load(&read_errors), atomic_load(&geometries),
             atomic_load(&geometries) + atomic_load(&geometry_failures), atomic_load(&failures));
    put(line);
    fflush(stdout);
    exit(atomic_load(&failures) || atomic_load(&wait_errors) || atomic_load(&read_errors)
         || atomic_load(&geometry_failures) ? 5 : 0);
}
