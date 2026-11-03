/*
 * conprobe: which console access pattern raises "unexpected DOS packet received"?
 *
 *   conprobe ONE N LOG       the Shell's own task: raw mode, N WaitForChar(50 ms)
 *                            calls with a line written every 10th, a pause, cooked
 *                            mode, a last line
 *   conprobe WORKER N LOG    a second process does exactly ONE's sequence on the
 *                            Shell's input and output handles while this task
 *                            writes lines to the same output
 *   conprobe PENDING N LOG   N rounds: a second process waits in WaitForChar(1 s)
 *                            again and again; meanwhile this task sets cooked mode
 *                            and writes, so its packets go out while the other's
 *                            wait is outstanding; then the worker is stopped,
 *                            writes one line itself and returns
 *
 * Each console call is recorded in memory (sequence number, tick, task, its reply
 * port, handle, handler port, call, result) and written to LOG at the end, so the
 * record adds no packets to the console during the test. A pause after the waits
 * gives a late or second reply time to arrive before the task's next packet.
 * Status 0: all calls returned; 20: usage or setup failure. A DOS alert stops the
 * program before the log is written; the screen then shows how far it got.
 */
#include <exec/types.h>
#include <exec/tasks.h>
#include <exec/ports.h>
#include <dos/dos.h>
#include <dos/dosextens.h>
#include <dos/dostags.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_EVENTS 4096

struct event {
    ULONG seq;
    ULONG tick;
    struct Task *task;
    struct MsgPort *reply;
    BPTR fh;
    struct MsgPort *handler;
    const char *call;
    LONG result;
};

static struct event events[MAX_EVENTS];
static volatile ULONG next_event, lost_events;
static BPTR in_fh, out_fh;
static volatile int stop;
static volatile LONG worker_running;
static long rounds;

static ULONG ticks(void)
{
    struct DateStamp ds;
    DateStamp(&ds);
    return (ds.ds_Minute * 60 * TICKS_PER_SECOND) + ds.ds_Tick;
}

static void record(BPTR fh, const char *call, LONG result)
{
    struct Task *me = FindTask(NULL);
    struct event *e = NULL;
    Forbid();
    if (next_event < MAX_EVENTS)
        e = &events[next_event];
    else
        lost_events++;
    if (e) {
        e->seq = next_event++;
        e->tick = ticks();
        e->task = me;
        e->reply = me->tc_Node.ln_Type == NT_PROCESS ? &((struct Process *)me)->pr_MsgPort : NULL;
        e->fh = fh;
        e->handler = fh ? ((struct FileHandle *)BADDR(fh))->fh_Type : NULL;
        e->call = call;
        e->result = result;
    }
    Permit();
}

static void say(const char *who, long n)
{
    char line[80];
    LONG r;
    int len = snprintf(line, sizeof line, "conprobe: %s %ld\n", who, n);
    record(out_fh, "Write: before", 0);
    r = Write(out_fh, line, len);
    record(out_fh, "Write", r);
}

static void wait_char(LONG us)
{
    LONG r;
    record(in_fh, "WaitForChar: before", us);
    r = WaitForChar(in_fh, us);
    record(in_fh, "WaitForChar", r);
}

static void set_mode(LONG mode)
{
    LONG r;
    record(in_fh, mode ? "SetMode raw: before" : "SetMode cooked: before", 0);
    r = SetMode(in_fh, mode);
    record(in_fh, mode ? "SetMode raw" : "SetMode cooked", r);
}

/* ONE's sequence; WORKER runs it in a second process. */
static void sequence(const char *who)
{
    long i;
    set_mode(1);
    for (i = 0; i < rounds; i++) {
        wait_char(50000);
        if (i % 10 == 9)
            say(who, i + 1);
    }
    Delay(25);
    set_mode(0);
    say(who, -1);
}

/* Ending protocol as in modrace: count out inside Forbid() and return without
   Permit(), so the main task runs again only after this process left our code. */
static void worker_sequence(void)
{
    sequence("worker");
    Forbid();
    worker_running--;
}

static void worker_pending(void)
{
    while (!stop)
        wait_char(1000000);
    Delay(25);
    say("worker after waits", 0);
    Forbid();
    worker_running--;
}

static int start_worker(void (*entry)(void))
{
    struct Process *p;
    Forbid();
    worker_running++;
    Permit();
    p = CreateNewProcTags(NP_Entry, (IPTR)entry, NP_Name, (IPTR)"conprobe worker",
                          NP_StackSize, 32768, NP_Input, 0, NP_Output, 0,
                          NP_CloseInput, FALSE, NP_CloseOutput, FALSE, TAG_END);
    if (p)
        return 1;
    Forbid();
    worker_running--;
    Permit();
    return 0;
}

static void wait_worker(void)
{
    while (worker_running)
        Delay(2);
    Forbid();
    while (FindTask((CONST_STRPTR)"conprobe worker")) {
        Permit();
        Delay(5);
        Forbid();
    }
    Permit();
}

static int write_log(const char *path, const char *mode)
{
    FILE *f = fopen(path, "w");
    ULONG i;
    if (!f)
        return 0;
    fprintf(f, "conprobe %s %ld: main task %p, input %p (handler %p), output %p (handler %p)\n", mode, rounds,
            (void *)FindTask(NULL), (void *)in_fh, (void *)((struct FileHandle *)BADDR(in_fh))->fh_Type,
            (void *)out_fh, (void *)((struct FileHandle *)BADDR(out_fh))->fh_Type);
    for (i = 0; i < next_event; i++) {
        struct event *e = &events[i];
        fprintf(f, "%5lu t%-6lu task %p port %p fh %p handler %p %-22s %ld\n", (unsigned long)e->seq,
                (unsigned long)e->tick, (void *)e->task, (void *)e->reply, (void *)e->fh, (void *)e->handler,
                e->call, (long)e->result);
    }
    fprintf(f, "events %lu, not recorded %lu\nresult: all calls returned\n", (unsigned long)next_event,
            (unsigned long)lost_events);
    fclose(f);
    return 1;
}

static int usage(const char *why)
{
    printf("conprobe: %s\nusage: conprobe ONE|WORKER|PENDING N LOG\n", why);
    return 20;
}

int main(int argc, char **argv)
{
    long i;
    if (argc != 4)
        return usage("wrong number of arguments");
    rounds = atol(argv[2]);
    if (rounds < 1)
        return usage("N must be at least 1");
    in_fh = Input();
    out_fh = Output();
    if (!in_fh || !out_fh)
        return usage("no input or output handle");
    if (!strcmp(argv[1], "ONE"))
        sequence("main");
    else if (!strcmp(argv[1], "WORKER")) {
        if (!start_worker(worker_sequence))
            return usage("worker not created");
        for (i = 0; i < rounds / 5 + 1; i++) {
            say("main", i);
            Delay(5);
        }
        wait_worker();
        say("main end", 0);
    } else if (!strcmp(argv[1], "PENDING")) {
        for (i = 0; i < rounds; i++) {
            stop = 0;
            if (!start_worker(worker_pending))
                return usage("worker not created");
            Delay(25);
            set_mode(1);
            set_mode(0);
            say("main while worker waits", i);
            stop = 1;
            wait_worker();
        }
        say("main end", 0);
    } else
        return usage("unknown mode");
    if (!write_log(argv[3], argv[1]))
        return usage("log not written");
    return 0;
}
