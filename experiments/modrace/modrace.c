/*
 * modrace: does debug.library publish a module before its segment array exists?
 *
 *   modrace DETECT FILE SECONDS
 *       One process loads and unloads FILE (LoadSeg/UnLoadSeg) in a loop while
 *       this task walks debug.library's module list under its semaphore, as
 *       UnregisterModule's FindModule does. It counts modules that are in the
 *       list with a segment count but no segment array (or a NULL entry in it):
 *       the state FindModule cannot walk. Read-only; it never follows such a
 *       pointer. Uses debug.library's private layout (rom/debug/debug_intern.h).
 *       Status 0: none seen; 10: seen; 20: usage or setup failure.
 *
 *   modrace CRASH FILE SECONDS
 *       Public calls only: two processes load and unload FILE while short
 *       processes are created with NP_Entry and end (each end unloads its
 *       seglist, so UnregisterModule walks the module list). Where the window
 *       exists this ends in a Software Failure in Debug_UnregisterModule;
 *       otherwise it prints the counts and ends with status 0.
 *
 * Nothing is written to disk. Children use exec and dos only.
 */
#include <exec/types.h>
#include <exec/lists.h>
#include <exec/semaphores.h>
#include <exec/libraries.h>
#include <exec/tasks.h>
#include <dos/dos.h>
#include <dos/dosextens.h>
#include <dos/dostags.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Prefix of debug.library's private structures (same in deadwood2 5376f09 and
   mainline master, 2026-11-03). Only fields up to m_segcnt are used. */
struct dbg_module {
    struct MinNode  m_node;
    BPTR            m_seg;
    char           *m_shstr;
    char           *m_str;
    void          **m_segments;
    unsigned int    m_segcnt;
};
struct dbg_base {
    struct Library          db_Lib;
    struct MinList          db_Modules;
    struct SignalSemaphore  db_ModSem;
};

static const char *file;
static volatile int stop;
static volatile ULONG loads, load_fail, procs_done;
static volatile LONG running;   /* loader processes still in their loop */
static volatile LONG alive;     /* short processes not yet returned */

static void loader(void)
{
    while (!stop) {
        BPTR seg = LoadSeg((CONST_STRPTR)file);
        if (seg) {
            UnLoadSeg(seg);
            loads++;
        } else
            load_fail++;
    }
    Forbid();
    running--;
    Permit();
}

static void short_proc(void)
{
    Forbid();
    alive--;
    procs_done++;
    Permit();
}

static int start(void (*entry)(void), const char *name)
{
    struct Process *p = CreateNewProcTags(NP_Entry, (IPTR)entry, NP_Name, (IPTR)name,
                                          NP_StackSize, 16384, NP_Output, 0, NP_Input, 0,
                                          NP_CloseOutput, FALSE, NP_CloseInput, FALSE, TAG_END);
    return p != NULL;
}

static ULONG now_seconds(void)
{
    struct DateStamp ds;
    DateStamp(&ds);
    return ds.ds_Days * 86400 + ds.ds_Minute * 60 + ds.ds_Tick / TICKS_PER_SECOND;
}

static int usage(const char *why)
{
    printf("modrace: %s\nusage: modrace DETECT|CRASH FILE SECONDS\n", why);
    return 20;
}

int main(int argc, char **argv)
{
    int detect, i;
    long seconds;
    ULONG end;
    BPTR probe;

    if (argc != 4)
        return usage("wrong number of arguments");
    if (!strcmp(argv[1], "DETECT"))
        detect = 1;
    else if (!strcmp(argv[1], "CRASH"))
        detect = 0;
    else
        return usage("unknown mode");
    file = argv[2];
    seconds = atol(argv[3]);
    if (seconds < 1)
        return usage("SECONDS must be at least 1");
    probe = LoadSeg((CONST_STRPTR)file);
    if (!probe)
        return usage("FILE does not load with LoadSeg");
    UnLoadSeg(probe);

    end = now_seconds() + seconds;
    if (detect) {
        struct dbg_base *db = (struct dbg_base *)OpenLibrary((CONST_STRPTR)"debug.library", 0);
        ULONG walks = 0, modules = 0, null_array = 0, null_entry = 0, max_mods = 0;
        char first[64] = "";
        if (!db)
            return usage("debug.library does not open");
        running = 1;
        if (!start(loader, "modrace loader")) {
            CloseLibrary(&db->db_Lib);
            return usage("loader process not created");
        }
        while (now_seconds() < end) {
            for (i = 0; i < 64; i++) {
                struct MinNode *n;
                ULONG count = 0;
                ObtainSemaphoreShared(&db->db_ModSem);
                for (n = db->db_Modules.mlh_Head; n->mln_Succ && count < 100000; n = n->mln_Succ, count++) {
                    struct dbg_module *m = (struct dbg_module *)n;
                    unsigned int j;
                    if (!m->m_segcnt)
                        continue;
                    if (!m->m_segments) {
                        null_array++;
                        if (!first[0])
                            snprintf(first, sizeof first, "segcnt %u, m_seg %p", m->m_segcnt, (void *)m->m_seg);
                        continue;
                    }
                    for (j = 0; j < m->m_segcnt; j++)
                        if (!m->m_segments[j]) {
                            null_entry++;
                            break;
                        }
                }
                ReleaseSemaphore(&db->db_ModSem);
                walks++;
                modules += count;
                if (count > max_mods)
                    max_mods = count;
            }
        }
        stop = 1;
        while (running)
            Delay(5);
        CloseLibrary(&db->db_Lib);
        printf("modrace DETECT: %lu loads (%lu failed), %lu walks (up to %lu modules)\n",
               (unsigned long)loads, (unsigned long)load_fail, (unsigned long)walks, (unsigned long)max_mods);
        printf("modules listed without a segment array: %lu%s%s\n", (unsigned long)null_array,
               first[0] ? ", first: " : "", first);
        printf("modules listed with a NULL segment entry: %lu\n", (unsigned long)null_entry);
        printf("result: %s\n", null_array || null_entry ? "window seen" : "none seen");
        return null_array || null_entry ? 10 : 0;
    }

    running = 2;
    if (!start(loader, "modrace loader 1") || !start(loader, "modrace loader 2")) {
        stop = 1;
        return usage("loader processes not created");
    }
    {
        ULONG started = 0, failed = 0;
        while (now_seconds() < end) {
            if (alive < 4) {
                Forbid();
                alive++;
                Permit();
                if (start(short_proc, "modrace short"))
                    started++;
                else {
                    Forbid();
                    alive--;
                    Permit();
                    failed++;
                }
            } else
                Delay(1);
        }
        stop = 1;
        while (running || alive)
            Delay(5);
        Delay(50); /* let the last short processes finish their exit */
        printf("modrace CRASH: %lu loads (%lu failed), %lu short processes started, %lu ended, %lu not created\n",
               (unsigned long)loads, (unsigned long)load_fail, (unsigned long)started,
               (unsigned long)procs_done, (unsigned long)failed);
        printf("result: no failure\n");
    }
    return 0;
}
