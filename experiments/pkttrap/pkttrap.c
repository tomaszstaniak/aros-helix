/*
 * pkttrap: which message does a task find on its process port when dos.library
 * raises "unexpected DOS packet received" (0x07000004), and who put it there?
 *
 *   Run >NIL: pkttrap LOG NAME...
 *   pkttrap TEST
 *
 * Diagnostic only, for a disposable test machine. It patches exec with
 * SetFunction and stays resident until the machine is reset: the patches are
 * never removed, so the code must stay loaded.
 *
 * A task is watched when its name starts with "pthread thread #" or when it is
 * a Shell process whose current command's base name is one of NAME (case
 * insensitive), e.g. "hx". Records go into a ring of RING entries:
 *
 *   - PutMsg/ReplyMsg (before and after) whose sender or target port owner is
 *     watched, GetMsg on a port a watched task owns, AddTask/RemTask of
 *     watched tasks; with the DOS packet a message carries, the port's list
 *     head/tail/tailpred, the message's links and the caller's address
 *   - exec list calls (Insert, AddHead, AddTail, Remove, RemHead, RemTail,
 *     Enqueue) that touch a watched process port's list
 *   - OpenLibrary/CloseLibrary of stdc, stdcio and posixc in watched tasks,
 *     with the cached handles of a stdcio base (offsets of this nightly's
 *     stdcio.library) and the task's pr_CIS/pr_COS
 *   - CORRUPT: the first time a watched process port's list fails a chain
 *     check; checks run on every patched call, including AllocMem/FreeMem,
 *     AllocVec/FreeVec, Obtain/ReleaseSemaphore and Signal, and the finding
 *     task's stack is copied
 *   - WATCH: an x86 write watchpoint (DR0/DR7, set through Supervisor(); one
 *     CPU) on ln_Succ of the message last added to a watched process port,
 *     disarmed around exec's own writes; a hit records the writer's RIP,
 *     registers and stack from a kernel.resource handler for vector 1
 *
 * At Alert(0x07000004) the ring is frozen and written to LOG while the alert
 * requester is up; CTRL-E writes it at any time (LOG is overwritten). Callers
 * are decoded with debug.library. TEST arms the watchpoint on a variable,
 * writes it and reports whether the handler saw the write.
 *
 * Status 20 for a usage or setup failure; otherwise LOG mode never returns.
 */
#include <exec/types.h>
#include <exec/tasks.h>
#include <exec/ports.h>
#include <exec/execbase.h>
#include <exec/alerts.h>
#include <dos/dos.h>
#include <dos/dosextens.h>
#include <dos/bptr.h>
#include <devices/timer.h>
#include <aros/asmcall.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <proto/timer.h>
#include <proto/debug.h>
#include <proto/kernel.h>
#include <aros/x86_64/cpucontext.h>
#include <libraries/debug.h>
#include <utility/tagitem.h>
#include <string.h>

#define RING 65536
#define NAMES_MAX 8
#define NAMELEN 24

enum { OP_PUT = 1, OP_PUTD, OP_REPLY, OP_REPLYD, OP_GET, OP_ADDTASK, OP_REMTASK, OP_ALERT, OP_PEND, OP_CORRUPT,
       OP_INSERT, OP_ADDHEAD, OP_ADDTAIL, OP_REMOVE, OP_REMHEAD, OP_REMTAIL, OP_ENQUEUE, OP_WATCH, OP_ARM, OP_OPENLIB, OP_CLOSELIB, OP_LAST = OP_CLOSELIB };
static const char *const opname[] = { "?", "PUT", "PUTD", "REPLY", "REPLYD", "GET", "ADDTASK", "REMTASK", "ALERT", "PEND", "CORRUPT",
                                      "INSERT", "ADDHEAD", "ADDTAIL", "REMOVE", "REMHEAD", "REMTAIL", "ENQUEUE", "WATCH", "ARM", "OPENLIB", "CLOSELIB" };

struct rec {
    ULONG seq;
    UBYTE op;
    UBYTE is_pkt;
    UBYTE pad[2];
    UQUAD time;
    struct Task *task;
    char task_name[NAMELEN];
    struct MsgPort *port;
    struct Task *owner;
    char owner_name[NAMELEN];
    struct Message *msg;
    struct MsgPort *reply_port;
    APTR dp;
    SIPTR dp_type, dp_arg1, dp_res1, dp_res2;
    struct MsgPort *dp_port;
    ULONG extra;
    APTR caller;
    struct Node *head, *tail, *tailpred;   /* the port's message list */
    struct Node *msucc, *mpred;            /* the message's links */
};

struct Device *TimerBase;
static struct rec *ring;
static volatile ULONG next_seq;
static volatile BOOL frozen;
static struct Task *trap_task;
static char watch[NAMES_MAX][32];
static int nwatch;
static ULONG eclock_freq;

static APTR old_PutMsg, old_GetMsg, old_ReplyMsg, old_AddTask, old_RemTask, old_Alert;
static APTR old_Insert, old_AddHead, old_AddTail, old_Remove, old_RemHead, old_RemTail, old_Enqueue;
static volatile BPTR hx_seglist;
static APTR old_OpenLibrary, old_CloseLibrary;
static APTR old_AllocMem, old_FreeMem, old_AllocVec, old_FreeVec, old_ObtainSemaphore, old_ReleaseSemaphore, old_Signal;
struct Library *DebugBase;

/* The stack of the task that found the first damaged list, from the check upward. */
#define STACK_WORDS 96
static IPTR stack_copy[STACK_WORDS];
static int stack_words;
static struct Task *stack_task;

static int lower(int c)
{
    return c >= 'A' && c <= 'Z' ? c + 32 : c;
}

static BOOL name_is(const char *a, int alen, const char *b)
{
    int i;
    for (i = 0; i < alen && b[i]; i++)
        if (lower((UBYTE)a[i]) != lower((UBYTE)b[i]))
            return FALSE;
    return i == alen && b[i] == 0;
}

/* The base name of a Shell process's current command, or NULL. */
static const char *command_of(struct Task *t, int *len)
{
    struct Process *p = (struct Process *)t;
    struct CommandLineInterface *cli;
    const char *s;
    int n, i, start;

    if (t->tc_Node.ln_Type != NT_PROCESS || !p->pr_CLI)
        return NULL;
    cli = BADDR(p->pr_CLI);
    if (!cli || !cli->cli_Module || !cli->cli_CommandName)
        return NULL;
    s = (const char *)AROS_BSTR_ADDR(cli->cli_CommandName);
    if (!s)
        return NULL;
    for (n = 0; n < 256 && s[n]; n++)
        ;
    if (n == 0 || n == 256)
        return NULL;
    start = 0;
    for (i = 0; i < n; i++)
        if (s[i] == ':' || s[i] == '/')
            start = i + 1;
    *len = n - start;
    return s + start;
}

static BOOL watched(struct Task *t)
{
    const char *name, *cmd;
    int len, i;

    if (!t || t == trap_task)
        return FALSE;
    name = t->tc_Node.ln_Name;
    if (name && strncmp(name, "pthread thread #", 16) == 0)
        return TRUE;
    cmd = command_of(t, &len);
    if (!cmd)
        return FALSE;
    for (i = 0; i < nwatch; i++)
        if (name_is(cmd, len, watch[i])) {
            if (!hx_seglist)
                hx_seglist = ((struct CommandLineInterface *)BADDR(((struct Process *)t)->pr_CLI))->cli_Module;
            return TRUE;
        }
    return FALSE;
}

static void copy_name(char *dst, struct Task *t)
{
    const char *s = t ? t->tc_Node.ln_Name : NULL;
    const char *cmd;
    int i = 0, len;

    if (s)
        for (; i < NAMELEN - 1 && s[i] && s[i] != ' '; i++)
            dst[i] = s[i];
    cmd = t ? command_of(t, &len) : NULL;
    if (cmd && i < NAMELEN - 2) {
        int k;
        dst[i++] = '/';
        for (k = 0; k < len && i < NAMELEN - 1; k++)
            dst[i++] = cmd[k];
    }
    dst[i] = 0;
}

static struct Task *port_owner(struct MsgPort *port)
{
    if (!port || (port->mp_Flags & PF_ACTION) != PA_SIGNAL)
        return NULL;
    return (struct Task *)port->mp_SigTask;
}

static void record(UBYTE op, struct MsgPort *port, struct Message *msg, ULONG extra, APTR caller)
{
    struct rec *r;
    struct Task *me = SysBase->ThisTask;
    struct Task *owner = port_owner(port);
    struct EClockVal ev;
    APTR name;

    Disable();
    if (frozen) {
        Enable();
        return;
    }
    r = &ring[next_seq % RING];
    memset(r, 0, sizeof *r);
    r->seq = next_seq++;
    Enable();

    r->op = op;
    ReadEClock(&ev);
    r->time = ((UQUAD)ev.ev_hi << 32) | ev.ev_lo;
    r->task = me;
    copy_name(r->task_name, me);
    r->port = port;
    r->owner = owner;
    copy_name(r->owner_name, owner);
    r->msg = msg;
    r->extra = extra;
    r->caller = caller;
    if (port) {
        r->head = port->mp_MsgList.lh_Head;
        r->tail = port->mp_MsgList.lh_Tail;
        r->tailpred = port->mp_MsgList.lh_TailPred;
    }
    if (msg && op != OP_ADDTASK && op != OP_REMTASK) {
        r->msucc = msg->mn_Node.ln_Succ;
        r->mpred = msg->mn_Node.ln_Pred;
    }
    if (msg && op != OP_ADDTASK && op != OP_REMTASK) {
        r->reply_port = msg->mn_ReplyPort;
        name = msg->mn_Node.ln_Name;
        if (name && TypeOfMem(name)) {
            struct DosPacket *dp = name;
            if (dp->dp_Link == msg) {
                r->is_pkt = 1;
                r->dp = dp;
                r->dp_type = dp->dp_Type;
                r->dp_arg1 = dp->dp_Arg1;
                r->dp_res1 = dp->dp_Res1;
                r->dp_res2 = dp->dp_Res2;
                r->dp_port = dp->dp_Port;
            }
        }
    }
}


/*
 * Hardware write watchpoint (x86 DR0/DR7, one CPU) on the ln_Succ field of the
 * message last added to a watched process port. exec's own list writes on that
 * port (a further PutMsg/ReplyMsg) run with it disarmed; any other write traps
 * into the debug exception handler, which records the writer and disarms it.
 */
APTR KernelBase;
static volatile IPTR wp_addr;          /* armed address, 0 when disarmed */
static volatile struct MsgPort *wp_port;
static volatile IPTR dr_value0, dr_value7;
#define WATCH_WORDS 512
static IPTR watch_stack[WATCH_WORDS];
static int watch_words;
static struct ExceptionContext watch_ctx;
static struct Task *watch_task;

/* Ring-0 stubs for Supervisor(): set DR0/DR7 from the globals, return with iretq. */
extern void dr_load(void);
__asm__(
    "    .text\n"
    "    .globl dr_load\n"
    "dr_load:\n"
    "    movq dr_value0(%rip), %rax\n"
    "    movq %rax, %dr0\n"
    "    xorq %rax, %rax\n"
    "    movq %rax, %dr6\n"
    "    movq dr_value7(%rip), %rax\n"
    "    movq %rax, %dr7\n"
    "    iretq\n");

static void dr_set(IPTR addr)
{
    dr_value0 = addr;
    /* L0, write-only, 8 bytes */
    dr_value7 = addr ? (1UL | (1UL << 16) | (3UL << 18)) : 0;
    if (KrnIsSuper()) {
        __asm__ __volatile__("movq %0, %%dr0; movq %1, %%dr6; movq %2, %%dr7"
                             :: "r"(dr_value0), "r"(0UL), "r"(dr_value7));
    } else {
        Supervisor((ULONG_FUNC)dr_load);
    }
}

static int debug_handler(struct ExceptionContext *ctx, void *d1, void *d2)
{
    IPTR dr6;
    struct rec *r;
    (void)d1; (void)d2;
    __asm__ __volatile__("movq %%dr6, %0" : "=r"(dr6));
    if (!(dr6 & 1))
        return 0;
    /* Disarm first: one capture is what we want. */
    wp_addr = 0;
    __asm__ __volatile__("movq %0, %%dr7; movq %0, %%dr6" :: "r"(0UL));
    if (!watch_task) {
        IPTR *sp = (IPTR *)ctx->rsp;
        int k;
        watch_ctx = *ctx;
        watch_task = SysBase->ThisTask;
        for (k = 0; k < WATCH_WORDS; k++)
            watch_stack[k] = sp[k];
        watch_words = k;
    }
    if (!frozen) {
        r = &ring[next_seq % RING];
        memset(r, 0, sizeof *r);
        r->seq = next_seq++;
        r->op = OP_WATCH;
        r->task = SysBase->ThisTask;
        copy_name(r->task_name, r->task);
        r->port = (struct MsgPort *)wp_port;
        r->msg = (struct Message *)dr_value0;
        r->caller = (APTR)ctx->rip;
        r->msucc = *(struct Node **)dr_value0;
    }
    return 1;
}

static void arm(struct MsgPort *port, struct Message *msg, APTR caller)
{
    if (!KernelBase || !msg)
        return;
    wp_port = port;
    wp_addr = (IPTR)&msg->mn_Node.ln_Succ;
    dr_set(wp_addr);
    record(OP_ARM, port, msg, 0, caller);
}

static void disarm_for(struct MsgPort *port)
{
    if (wp_addr && wp_port == port) {
        wp_addr = 0;
        dr_set(0);
    }
}

/*
 * Process ports of watched tasks, kept from AddTask to RemTask. Every patched
 * call checks them: a list whose head points at the list itself, or whose
 * tail field is not NULL, is reported once as CORRUPT with the call that
 * found it.
 */
#define PORTS_MAX 64
static struct MsgPort *volatile ports[PORTS_MAX];
static volatile UBYTE reported[PORTS_MAX];

static void track(struct MsgPort *port, BOOL add)
{
    int i;
    Disable();
    for (i = 0; i < PORTS_MAX; i++) {
        if (add && !ports[i]) {
            ports[i] = port;
            reported[i] = 0;
            break;
        }
        if (!add && ports[i] == port) {
            ports[i] = NULL;
            break;
        }
    }
    Enable();
}

/* A sane exec list: head..tail linked both ways, ending at &lh_Tail. */
static BOOL list_sane(struct List *l)
{
    struct Node *prev = (struct Node *)&l->lh_Head, *n = l->lh_Head;
    int k;
    if (l->lh_Tail != NULL || n == (struct Node *)l)
        return FALSE;
    for (k = 0; k < 16 && n != (struct Node *)&l->lh_Tail; k++) {
        if (!n || n == (struct Node *)l || n->ln_Pred != prev)
            return FALSE;
        prev = n;
        n = n->ln_Succ;
    }
    return k == 16 || l->lh_TailPred == prev;
}

static void check(UBYTE op, APTR caller)
{
    int i;
    Disable();
    for (i = 0; i < PORTS_MAX; i++) {
        struct MsgPort *p = ports[i];
        if (p && !reported[i] && !list_sane(&p->mp_MsgList)) {
            reported[i] = 1;
            record(OP_CORRUPT, p, NULL, op, caller);
            if (!stack_words) {
                struct Task *me = SysBase->ThisTask;
                IPTR *sp = (IPTR *)__builtin_frame_address(0);
                int k;
                for (k = 0; k < STACK_WORDS && (APTR)(sp + k) < me->tc_SPUpper; k++)
                    stack_copy[k] = sp[k];
                stack_task = me;
                stack_words = k;
            }
        }
    }
    Enable();
}

static BOOL tracked(struct MsgPort *port)
{
    int i;
    for (i = 0; i < PORTS_MAX; i++)
        if (port && ports[i] == port)
            return TRUE;
    return FALSE;
}

/* Does a list call touch a watched process port's message list? */
static struct MsgPort *touched(struct List *list, struct Node *node)
{
    int i;
    for (i = 0; i < PORTS_MAX; i++) {
        struct MsgPort *p = ports[i];
        struct Node *h, *t;
        if (!p)
            continue;
        h = (struct Node *)&p->mp_MsgList.lh_Head;
        t = (struct Node *)&p->mp_MsgList.lh_Tail;
        if (list == &p->mp_MsgList || node == h || node == t)
            return p;
        if (node && (node->ln_Succ == h || node->ln_Succ == t || node->ln_Pred == h))
            return p;
    }
    return NULL;
}

static void list_record(UBYTE op, struct List *list, struct Node *node, APTR caller)
{
    struct MsgPort *p = touched(list, node);
    if (p)
        record(op, p, (struct Message *)node, (ULONG)(IPTR)list, caller);
}

AROS_UFH4(void, new_Insert,
    AROS_UFHA(struct List *, list, A0),
    AROS_UFHA(struct Node *, node, A1),
    AROS_UFHA(struct Node *, pred, A2),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    list_record(OP_INSERT, list, pred ? pred : node, caller);
    AROS_UFC4NR(void, old_Insert, AROS_UFCA(struct List *, list, A0), AROS_UFCA(struct Node *, node, A1),
        AROS_UFCA(struct Node *, pred, A2), AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}

AROS_UFH3(void, new_AddHead,
    AROS_UFHA(struct List *, list, A0),
    AROS_UFHA(struct Node *, node, A1),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    list_record(OP_ADDHEAD, list, node, caller);
    AROS_UFC3NR(void, old_AddHead, AROS_UFCA(struct List *, list, A0), AROS_UFCA(struct Node *, node, A1),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}

AROS_UFH3(void, new_AddTail,
    AROS_UFHA(struct List *, list, A0),
    AROS_UFHA(struct Node *, node, A1),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    list_record(OP_ADDTAIL, list, node, caller);
    AROS_UFC3NR(void, old_AddTail, AROS_UFCA(struct List *, list, A0), AROS_UFCA(struct Node *, node, A1),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}

AROS_UFH2(void, new_Remove,
    AROS_UFHA(struct Node *, node, A1),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    list_record(OP_REMOVE, NULL, node, caller);
    AROS_UFC2NR(void, old_Remove, AROS_UFCA(struct Node *, node, A1), AROS_UFCA(struct ExecBase *, sysbase, A6));
    check(OP_REMOVE, caller);
    AROS_USERFUNC_EXIT
}

AROS_UFH2(struct Node *, new_RemHead,
    AROS_UFHA(struct List *, list, A0),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    list_record(OP_REMHEAD, list, NULL, caller);
    return AROS_UFC2(struct Node *, old_RemHead, AROS_UFCA(struct List *, list, A0),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}

AROS_UFH2(struct Node *, new_RemTail,
    AROS_UFHA(struct List *, list, A0),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    list_record(OP_REMTAIL, list, NULL, caller);
    return AROS_UFC2(struct Node *, old_RemTail, AROS_UFCA(struct List *, list, A0),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}

AROS_UFH3(void, new_Enqueue,
    AROS_UFHA(struct List *, list, A0),
    AROS_UFHA(struct Node *, node, A1),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    list_record(OP_ENQUEUE, list, node, caller);
    AROS_UFC3NR(void, old_Enqueue, AROS_UFCA(struct List *, list, A0), AROS_UFCA(struct Node *, node, A1),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}

AROS_UFH3(void, new_PutMsg,
    AROS_UFHA(struct MsgPort *, port, A0),
    AROS_UFHA(struct Message *, msg, A1),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    BOOL w = watched(sysbase->ThisTask) || watched(port_owner(port));
    BOOL t = tracked(port);
    check(OP_PUT, caller);
    if (w)
        record(OP_PUT, port, msg, 0, caller);
    if (t)
        disarm_for(port);
    if (msg && wp_addr == (IPTR)&msg->mn_Node.ln_Succ)
        disarm_for((struct MsgPort *)wp_port);
    AROS_UFC3NR(void, old_PutMsg,
        AROS_UFCA(struct MsgPort *, port, A0),
        AROS_UFCA(struct Message *, msg, A1),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    if (w)
        record(OP_PUTD, port, msg, 0, caller);
    check(OP_PUTD, caller);
    if (t && !frozen) {
        /* Still queued (the receiver may have run): arm without a gap. */
        Disable();
        if (port->mp_MsgList.lh_TailPred == (struct Node *)msg)
            arm(port, msg, caller);
        Enable();
    }
    AROS_USERFUNC_EXIT
}

AROS_UFH2(void, new_ReplyMsg,
    AROS_UFHA(struct Message *, msg, A1),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    struct MsgPort *port = msg ? msg->mn_ReplyPort : NULL;
    BOOL w = watched(sysbase->ThisTask) || watched(port_owner(port));
    BOOL t = tracked(port);
    check(OP_REPLY, caller);
    if (w)
        record(OP_REPLY, port, msg, 0, caller);
    if (t)
        disarm_for(port);
    if (msg && wp_addr == (IPTR)&msg->mn_Node.ln_Succ)
        disarm_for((struct MsgPort *)wp_port);
    AROS_UFC2NR(void, old_ReplyMsg,
        AROS_UFCA(struct Message *, msg, A1),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    if (w)
        record(OP_REPLYD, port, msg, 0, caller);
    check(OP_REPLYD, caller);
    if (t && !frozen) {
        /* Still queued (the receiver may have run): arm without a gap. */
        Disable();
        if (port->mp_MsgList.lh_TailPred == (struct Node *)msg)
            arm(port, msg, caller);
        Enable();
    }
    AROS_USERFUNC_EXIT
}

AROS_UFH2(struct Message *, new_GetMsg,
    AROS_UFHA(struct MsgPort *, port, A0),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    struct Message *msg;
    check(OP_GET, caller);
    msg = AROS_UFC2(struct Message *, old_GetMsg,
        AROS_UFCA(struct MsgPort *, port, A0),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    if (msg && watched(port_owner(port)))
        record(OP_GET, port, msg, 0, caller);
    if (msg && wp_addr == (IPTR)&msg->mn_Node.ln_Succ)
        disarm_for(port);
    return msg;
    AROS_USERFUNC_EXIT
}

AROS_UFH4(APTR, new_AddTask,
    AROS_UFHA(struct Task *, task, A1),
    AROS_UFHA(APTR, initialPC, A2),
    AROS_UFHA(APTR, finalPC, A3),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    struct MsgPort *port = task->tc_Node.ln_Type == NT_PROCESS ? &((struct Process *)task)->pr_MsgPort : NULL;
    BOOL w = watched(task);
    APTR result;
    /* Tracked before the task can run; dropped again if AddTask fails. */
    if (w && port)
        track(port, TRUE);
    result = AROS_UFC4(APTR, old_AddTask,
        AROS_UFCA(struct Task *, task, A1),
        AROS_UFCA(APTR, initialPC, A2),
        AROS_UFCA(APTR, finalPC, A3),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    if (w && port && !result)
        track(port, FALSE);
    /* The record's message field carries the new task. */
    if (w && result)
        record(OP_ADDTASK, port, (struct Message *)task, 0, caller);
    return result;
    AROS_USERFUNC_EXIT
}

AROS_UFH2(void, new_RemTask,
    AROS_UFHA(struct Task *, task, A1),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    struct Task *t = task ? task : sysbase->ThisTask;
    struct MsgPort *port = t->tc_Node.ln_Type == NT_PROCESS ? &((struct Process *)t)->pr_MsgPort : NULL;
    check(OP_REMTASK, caller);
    if (watched(t))
        record(OP_REMTASK, port, (struct Message *)t, 0, caller);
    if (port) {
        disarm_for(port);
        track(port, FALSE);
    }
    AROS_UFC2NR(void, old_RemTask,
        AROS_UFCA(struct Task *, task, A1),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}

AROS_UFH2(void, new_Alert,
    AROS_UFHA(ULONG, alertNum, D7),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    struct Task *me = sysbase->ThisTask;
    if (alertNum == AN_AsyncPkt && !frozen) {
        struct MsgPort *port = me->tc_Node.ln_Type == NT_PROCESS ? &((struct Process *)me)->pr_MsgPort : NULL;
        check(OP_ALERT, caller);
        record(OP_ALERT, port, NULL, alertNum, caller);
        if (port) {
            struct Node *n;
            ULONG k = 0;
            Disable();
            for (n = port->mp_MsgList.lh_Head; n->ln_Succ && k < 8; n = n->ln_Succ, k++)
                record(OP_PEND, port, (struct Message *)n, k, caller);
            Enable();
        }
        frozen = TRUE;
        Signal(trap_task, SIGBREAKF_CTRL_F);
    }
    AROS_UFC2NR(void, old_Alert,
        AROS_UFCA(ULONG, alertNum, D7),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}


AROS_UFH3(APTR, new_AllocMem,
    AROS_UFHA(IPTR, size, D0),
    AROS_UFHA(ULONG, req, D1),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    check(0x21, __builtin_return_address(0));
    return AROS_UFC3(APTR, old_AllocMem, AROS_UFCA(IPTR, size, D0), AROS_UFCA(ULONG, req, D1),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}

AROS_UFH3(void, new_FreeMem,
    AROS_UFHA(APTR, mem, A1),
    AROS_UFHA(IPTR, size, D0),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    check(0x23, caller);
    AROS_UFC3NR(void, old_FreeMem, AROS_UFCA(APTR, mem, A1), AROS_UFCA(IPTR, size, D0),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    check(0x23, caller);
    AROS_USERFUNC_EXIT
}

AROS_UFH3(APTR, new_AllocVec,
    AROS_UFHA(IPTR, size, D0),
    AROS_UFHA(ULONG, req, D1),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    check(0x72, __builtin_return_address(0));
    return AROS_UFC3(APTR, old_AllocVec, AROS_UFCA(IPTR, size, D0), AROS_UFCA(ULONG, req, D1),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}

AROS_UFH2(void, new_FreeVec,
    AROS_UFHA(APTR, mem, A1),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    check(0x73, caller);
    AROS_UFC2NR(void, old_FreeVec, AROS_UFCA(APTR, mem, A1), AROS_UFCA(struct ExecBase *, sysbase, A6));
    check(0x73, caller);
    AROS_USERFUNC_EXIT
}

AROS_UFH2(void, new_ObtainSemaphore,
    AROS_UFHA(struct SignalSemaphore *, sem, A0),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    check(0x5E, __builtin_return_address(0));
    AROS_UFC2NR(void, old_ObtainSemaphore, AROS_UFCA(struct SignalSemaphore *, sem, A0),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}

AROS_UFH2(void, new_ReleaseSemaphore,
    AROS_UFHA(struct SignalSemaphore *, sem, A0),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    check(0x5F, __builtin_return_address(0));
    AROS_UFC2NR(void, old_ReleaseSemaphore, AROS_UFCA(struct SignalSemaphore *, sem, A0),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}

AROS_UFH3(void, new_Signal,
    AROS_UFHA(struct Task *, task, A1),
    AROS_UFHA(ULONG, sigs, D0),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    check(0x36, __builtin_return_address(0));
    AROS_UFC3NR(void, old_Signal, AROS_UFCA(struct Task *, task, A1), AROS_UFCA(ULONG, sigs, D0),
        AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}


/* The C library bases a watched task opens and closes, with the handles a
   stdcio base cached at open (intstdout.fh at +0x168, intstdin.fh at +0x138
   in this nightly's stdcio.library, from its __close_stdio). */
static int crt_lib(const char *name)
{
    if (!name)
        return 0;
    if (strcmp(name, "stdcio.library") == 0)
        return 2;
    return strcmp(name, "stdc.library") == 0 || strcmp(name, "posixc.library") == 0;
}

static void lib_record(UBYTE op, struct Library *base, APTR caller)
{
    struct Task *me = SysBase->ThisTask;
    struct rec *r;
    int kind;
    if (!base || frozen || !watched(me))
        return;
    kind = crt_lib(base->lib_Node.ln_Name);
    if (!kind)
        return;
    record(op, NULL, NULL, base->lib_OpenCnt, caller);
    r = &ring[(next_seq - 1) % RING];
    if (r->task != me || r->op != op)
        return;
    r->dp = base;
    r->is_pkt = 1;
    r->dp_type = kind;
    if (kind == 2) {
        r->dp_arg1 = *(SIPTR *)((UBYTE *)base + 0x168);
        r->dp_res1 = *(SIPTR *)((UBYTE *)base + 0x138);
    }
    if (me->tc_Node.ln_Type == NT_PROCESS) {
        r->dp_res2 = (SIPTR)((struct Process *)me)->pr_COS;
        r->dp_port = (struct MsgPort *)((struct Process *)me)->pr_CIS;
    }
}

AROS_UFH3(struct Library *, new_OpenLibrary,
    AROS_UFHA(CONST_STRPTR, name, A1),
    AROS_UFHA(ULONG, version, D0),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    struct Library *base = AROS_UFC3(struct Library *, old_OpenLibrary, AROS_UFCA(CONST_STRPTR, name, A1),
        AROS_UFCA(ULONG, version, D0), AROS_UFCA(struct ExecBase *, sysbase, A6));
    lib_record(OP_OPENLIB, base, caller);
    return base;
    AROS_USERFUNC_EXIT
}

AROS_UFH2(void, new_CloseLibrary,
    AROS_UFHA(struct Library *, base, A1),
    AROS_UFHA(struct ExecBase *, sysbase, A6))
{
    AROS_USERFUNC_INIT
    APTR caller = __builtin_return_address(0);
    lib_record(OP_CLOSELIB, base, caller);
    AROS_UFC2NR(void, old_CloseLibrary, AROS_UFCA(struct Library *, base, A1), AROS_UFCA(struct ExecBase *, sysbase, A6));
    AROS_USERFUNC_EXIT
}

static void decode(BPTR fh, IPTR addr)
{
    STRPTR module = NULL, symbol = NULL;
    IPTR segstart = 0, symstart = 0;
    if (!DebugBase || !addr)
        return;
    if (DecodeLocation((APTR)addr, DL_ModuleName, (IPTR)&module, DL_SegmentStart, (IPTR)&segstart,
                       DL_SymbolName, (IPTR)&symbol, DL_SymbolStart, (IPTR)&symstart, TAG_DONE) && module)
        FPrintf(fh, (CONST_STRPTR)" %s+%lx %s+%lx", (IPTR)module, addr - segstart,
                (IPTR)(symbol ? symbol : (STRPTR)"?"), symbol ? addr - symstart : 0);
}

static void dump(const char *path, const char *why)
{
    BPTR fh = Open((CONST_STRPTR)path, MODE_NEWFILE);
    ULONG last, first, s;
    if (!fh)
        return;
    last = next_seq;
    first = last > RING ? last - RING : 0;
    FPrintf(fh, (CONST_STRPTR)"pkttrap dump (%s): records %lu..%lu, eclock %lu Hz, frozen %ld\n",
            (IPTR)why, (unsigned long)first, (unsigned long)last, (unsigned long)eclock_freq, (LONG)frozen);
    FPrintf(fh, (CONST_STRPTR)"seq time_us op task(name) port owner(name) msg reply_port | dp type arg1 res1 res2 dp_port"
            " | extra caller | head tail tailpred | msucc mpred\n");
    for (s = first; s < last; s++) {
        struct rec *r = &ring[s % RING];
        UQUAD us = eclock_freq ? r->time * 1000000ULL / eclock_freq : 0;
        if (r->seq != s || r->op == 0 || r->op > OP_LAST)
            continue;
        FPrintf(fh, (CONST_STRPTR)"%lu %lu %s %lx(%s) %lx %lx(%s) %lx %lx",
                (unsigned long)r->seq, (unsigned long)us, (IPTR)opname[r->op],
                (IPTR)r->task, (IPTR)r->task_name, (IPTR)r->port, (IPTR)r->owner, (IPTR)r->owner_name,
                (IPTR)r->msg, (IPTR)r->reply_port);
        if (r->is_pkt)
            FPrintf(fh, (CONST_STRPTR)" | dp %lx type %ld arg1 %lx res1 %ld res2 %ld dp_port %lx",
                    (IPTR)r->dp, (LONG)r->dp_type, (IPTR)r->dp_arg1, (LONG)r->dp_res1, (LONG)r->dp_res2,
                    (IPTR)r->dp_port);
        FPrintf(fh, (CONST_STRPTR)" | %lx %lx | %lx %lx %lx | %lx %lx\n", (unsigned long)r->extra, (IPTR)r->caller,
                (IPTR)r->head, (IPTR)r->tail, (IPTR)r->tailpred, (IPTR)r->msucc, (IPTR)r->mpred);
    }
    {
        ULONG k;
        for (k = first; k < last; k++) {
            struct rec *r = &ring[k % RING];
            if (r->seq == k && (r->op == OP_CORRUPT || r->op == OP_ALERT || r->op == OP_OPENLIB || r->op == OP_CLOSELIB)) {
                FPrintf(fh, (CONST_STRPTR)"%s seq %lu task %lx base %lx caller %lx", (IPTR)opname[r->op], (unsigned long)k,
                        (IPTR)r->task, (IPTR)r->dp, (IPTR)r->caller);
                decode(fh, (IPTR)r->caller);
                FPrintf(fh, (CONST_STRPTR)"\n");
            }
        }
    }
    if (watch_task) {
        int k;
        FPrintf(fh, (CONST_STRPTR)"WATCH: task %lx wrote %lx at rip %lx", (IPTR)watch_task, dr_value0, watch_ctx.rip);
        decode(fh, watch_ctx.rip);
        FPrintf(fh, (CONST_STRPTR)"\n  rax %lx rbx %lx rcx %lx rdx %lx rsi %lx rdi %lx rbp %lx rsp %lx\n",
                watch_ctx.rax, watch_ctx.rbx, watch_ctx.rcx, watch_ctx.rdx, watch_ctx.rsi, watch_ctx.rdi,
                watch_ctx.rbp, watch_ctx.rsp);
        FPrintf(fh, (CONST_STRPTR)"  r8 %lx r9 %lx r10 %lx r11 %lx r12 %lx r13 %lx r14 %lx r15 %lx\n",
                watch_ctx.r8, watch_ctx.r9, watch_ctx.r10, watch_ctx.r11, watch_ctx.r12, watch_ctx.r13,
                watch_ctx.r14, watch_ctx.r15);
        for (k = 0; k < watch_words; k++) {
            STRPTR module = NULL;
            if (DebugBase && DecodeLocation((APTR)watch_stack[k], DL_ModuleName, (IPTR)&module, TAG_DONE) && module) {
                FPrintf(fh, (CONST_STRPTR)"  [%ld] %lx", (LONG)k, watch_stack[k]);
                decode(fh, watch_stack[k]);
                FPrintf(fh, (CONST_STRPTR)"\n");
            }
        }
    }
    if (stack_words) {
        int k;
        FPrintf(fh, (CONST_STRPTR)"stack of %lx at the first damaged list (%ld words):\n", (IPTR)stack_task, (LONG)stack_words);
        for (k = 0; k < stack_words; k++) {
            FPrintf(fh, (CONST_STRPTR)"  [%ld] %lx", (LONG)k, stack_copy[k]);
            decode(fh, stack_copy[k]);
            FPrintf(fh, (CONST_STRPTR)"\n");
        }
    }
    FPrintf(fh, (CONST_STRPTR)"end\n");
    Close(fh);
}

int main(int argc, char **argv)
{
    struct MsgPort *tport;
    struct timerequest *treq;
    struct EClockVal ev;
    const char *log;
    int i;

    if (argc == 2 && strcmp(argv[1], "TEST") == 0) {
        /* Watchpoint self-test: arm on a variable, write it, expect one capture. */
        static volatile IPTR target;
        void *handle = NULL;
        KernelBase = OpenResource((CONST_STRPTR)"kernel.resource");
        ring = AllocVec(sizeof(struct rec) * 16, MEMF_PUBLIC | MEMF_CLEAR);
        if (!KernelBase || !ring || !(handle = KrnAddExceptionHandler(1, debug_handler, NULL, NULL))) {
            PutStr((CONST_STRPTR)"pkttrap TEST: setup failed\n");
            return 20;
        }
        wp_addr = (IPTR)&target;
        dr_set(wp_addr);
        target = 42;
        dr_set(0);
        Printf((CONST_STRPTR)"pkttrap TEST: %s (writer rip %lx, task %lx)\n",
               (IPTR)(watch_task ? "watchpoint fired" : "watchpoint did NOT fire"), watch_ctx.rip, (IPTR)watch_task);
        KrnRemExceptionHandler(handle);
        FreeVec(ring);
        return watch_task ? 0 : 5;
    }
    if (argc < 3 || argc - 2 > NAMES_MAX) {
        PutStr((CONST_STRPTR)"usage: pkttrap LOG NAME... | pkttrap TEST\n");
        return 20;
    }
    log = argv[1];
    for (i = 2; i < argc; i++) {
        strncpy(watch[nwatch], argv[i], sizeof watch[0] - 1);
        nwatch++;
    }
    tport = CreateMsgPort();
    treq = tport ? (struct timerequest *)CreateIORequest(tport, sizeof *treq) : NULL;
    if (!treq || OpenDevice((CONST_STRPTR)TIMERNAME, UNIT_ECLOCK, (struct IORequest *)treq, 0) != 0) {
        PutStr((CONST_STRPTR)"pkttrap: timer.device failed\n");
        return 20;
    }
    TimerBase = treq->tr_node.io_Device;
    eclock_freq = ReadEClock(&ev);
    ring = AllocVec(sizeof(struct rec) * RING, MEMF_PUBLIC | MEMF_CLEAR);
    if (!ring) {
        PutStr((CONST_STRPTR)"pkttrap: no memory\n");
        return 20;
    }
    trap_task = FindTask(NULL);
    DebugBase = OpenLibrary((CONST_STRPTR)"debug.library", 0);
    KernelBase = OpenResource((CONST_STRPTR)"kernel.resource");
    if (KernelBase && !KrnAddExceptionHandler(1, debug_handler, NULL, NULL)) {
        PutStr((CONST_STRPTR)"pkttrap: no debug exception handler, watchpoint off\n");
        KernelBase = NULL;
    }

    Forbid();
    old_PutMsg = SetFunction((struct Library *)SysBase, -61 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_PutMsg));
    old_GetMsg = SetFunction((struct Library *)SysBase, -62 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_GetMsg));
    old_ReplyMsg = SetFunction((struct Library *)SysBase, -63 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_ReplyMsg));
    old_AddTask = SetFunction((struct Library *)SysBase, -47 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_AddTask));
    old_RemTask = SetFunction((struct Library *)SysBase, -48 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_RemTask));
    old_Alert = SetFunction((struct Library *)SysBase, -18 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_Alert));
    old_Insert = SetFunction((struct Library *)SysBase, -39 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_Insert));
    old_AddHead = SetFunction((struct Library *)SysBase, -40 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_AddHead));
    old_AddTail = SetFunction((struct Library *)SysBase, -41 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_AddTail));
    old_Remove = SetFunction((struct Library *)SysBase, -42 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_Remove));
    old_RemHead = SetFunction((struct Library *)SysBase, -43 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_RemHead));
    old_RemTail = SetFunction((struct Library *)SysBase, -44 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_RemTail));
    old_Enqueue = SetFunction((struct Library *)SysBase, -45 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_Enqueue));
    old_AllocMem = SetFunction((struct Library *)SysBase, -33 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_AllocMem));
    old_FreeMem = SetFunction((struct Library *)SysBase, -35 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_FreeMem));
    old_AllocVec = SetFunction((struct Library *)SysBase, -114 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_AllocVec));
    old_FreeVec = SetFunction((struct Library *)SysBase, -115 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_FreeVec));
    old_ObtainSemaphore = SetFunction((struct Library *)SysBase, -94 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_ObtainSemaphore));
    old_ReleaseSemaphore = SetFunction((struct Library *)SysBase, -95 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_ReleaseSemaphore));
    old_Signal = SetFunction((struct Library *)SysBase, -54 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_Signal));
    old_OpenLibrary = SetFunction((struct Library *)SysBase, -92 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_OpenLibrary));
    old_CloseLibrary = SetFunction((struct Library *)SysBase, -69 * LIB_VECTSIZE, (APTR)AROS_ASMSYMNAME(new_CloseLibrary));
    Permit();

    dump(log, "start");
    for (;;) {
        ULONG sig = Wait(SIGBREAKF_CTRL_E | SIGBREAKF_CTRL_F);
        if (sig & SIGBREAKF_CTRL_F)
            dump(log, "alert");
        else if (sig & SIGBREAKF_CTRL_E)
            dump(log, "ctrl-e");
    }
    return 0;
}
