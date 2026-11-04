/*
 * hx: starts Helix (hx-editor in the same drawer) with the stack it needs.
 *
 * Helix for AROS runs its editor on the program's own task, which needs a
 * 16 MiB stack; a Shell's default stack is far smaller. This launcher loads
 * PROGDIR:hx-editor and runs it with RunCommand() on a 16 MiB stack (or the
 * Shell's own, if larger), in the same process and console, passing the
 * argument line through unchanged. Before that it sets the local variable
 * HELIX_PROGDIR to the absolute path of its drawer, so hx-editor can find
 * the language servers installed next to it, and removes the variable
 * afterwards. The return code is hx-editor's, or 20 if it could not be run.
 *
 * Only exec and dos calls; no C library state is shared with hx-editor,
 * which RunCommand() starts with its own task storage.
 */
#include <exec/types.h>
#include <exec/tasks.h>
#include <dos/dos.h>
#include <dos/dosextens.h>
#include <dos/var.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <string.h>

#define EDITOR "PROGDIR:hx-editor"
#define VARIABLE "HELIX_PROGDIR"
#define STACK (16UL * 1024 * 1024)

static const char version[] __attribute__((used)) = "$VER: hx 25.07.1-aros3 (04.11.2026) Helix launcher";

int main(void)
{
    struct Task *self = FindTask(NULL);
    ULONG stack = (ULONG)((UBYTE *)self->tc_SPUpper - (UBYTE *)self->tc_SPLower);
    char drawer[512];
    STRPTR args = GetArgStr();
    BPTR seg;
    LONG rc;

    if (stack < STACK)
        stack = STACK;
    if (!GetProgramDir() || !NameFromLock(GetProgramDir(), (STRPTR)drawer, sizeof drawer)) {
        PrintFault(IoErr(), (CONST_STRPTR)"hx: cannot name the Helix drawer");
        return RETURN_FAIL;
    }
    seg = LoadSeg((CONST_STRPTR)EDITOR);
    if (!seg) {
        PrintFault(IoErr(), (CONST_STRPTR)"hx: cannot load " EDITOR);
        return RETURN_FAIL;
    }
    if (!SetVar((CONST_STRPTR)VARIABLE, (CONST_STRPTR)drawer, -1, GVF_LOCAL_ONLY)) {
        PrintFault(IoErr(), (CONST_STRPTR)"hx: cannot set " VARIABLE);
        UnLoadSeg(seg);
        return RETURN_FAIL;
    }
    if (!args)
        args = (STRPTR)"\n";
    rc = RunCommand(seg, stack, args, strlen((const char *)args));
    if (rc == -1) {
        PrintFault(IoErr(), (CONST_STRPTR)"hx: cannot run " EDITOR);
        rc = RETURN_FAIL;
    }
    DeleteVar((CONST_STRPTR)VARIABLE, GVF_LOCAL_ONLY);
    UnLoadSeg(seg);
    return rc;
}
