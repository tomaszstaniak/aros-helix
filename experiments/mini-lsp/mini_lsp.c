/* Finite language server fixture: Content-Length framing over stdio.
 *   mini-lsp MODE LOG [SECONDS]
 *   normal           answers initialize, hover and shutdown, ends on exit.
 *   ignore-shutdown  never answers shutdown; ends SECONDS later by itself.
 *   spin             never answers shutdown; computes for SECONDS, then ends.
 *   crash            exits 3 straight after textDocument/didOpen.
 *   bad-init         answers initialize with an error, then waits for exit.
 *   stall            stops reading after didOpen; ends SECONDS later by itself.
 * LOG receives one line per event, so the session can be read back.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <unistd.h>
#include <proto/dos.h>

static FILE *logfile;
static char body[65536];

static void note(const char *text)
{
    fprintf(logfile, "%s\n", text);
    fflush(logfile);
}

static int read_all(char *out, int size)
{
    int got = 0;
    while (got < size) {
        int n = read(0, out + got, size - got);
        if (n <= 0) return 0;
        got += n;
    }
    return 1;
}

static int read_message(void)
{
    char line[128];
    int length = -1, used = 0;

    for (;;) {
        char c;
        if (!read_all(&c, 1)) return 0;
        if (c == '\r') continue;
        if (c != '\n') {
            if (used < (int)sizeof line - 1) line[used++] = c;
            continue;
        }
        line[used] = 0;
        if (used == 0) break;
        if (strncmp(line, "Content-Length:", 15) == 0) length = atoi(line + 15);
        used = 0;
    }
    if (length < 0 || length >= (int)sizeof body) return 0;
    if (!read_all(body, length)) return 0;
    body[length] = 0;
    return 1;
}

static void send_message(const char *json)
{
    char header[64];
    int size = snprintf(header, sizeof header, "Content-Length: %d\r\n\r\n", (int)strlen(json));
    write(1, header, size);
    write(1, json, strlen(json));
}

static const char *id_of(char *out, int size)
{
    const char *at = strstr(body, "\"id\":");
    int used = 0;
    out[0] = '0';
    out[1] = 0;
    if (!at) return out;
    at += 5;
    while (*at && *at != ',' && *at != '}' && used < size - 1) out[used++] = *at++;
    out[used] = 0;
    return out;
}

static void quote_uri(char *out, int size)
{
    const char *at = strstr(body, "\"uri\":\"");
    int used = 0;
    out[0] = 0;
    if (!at) return;
    at += 7;
    while (*at && *at != '"' && used < size - 1) out[used++] = *at++;
    out[used] = 0;
}

int main(int argc, char **argv)
{
    char id[32], uri[512], reply[1024];
    const char *mode;
    int seconds;

    if (argc < 3) return 20;
    mode = argv[1];
    seconds = argc > 3 ? atoi(argv[3]) : 5;
    logfile = fopen(argv[2], "w");
    if (!logfile) return 20;
    note("started");
    while (read_message()) {
        if (strstr(body, "\"method\":\"initialize\"") && strcmp(mode, "bad-init") == 0) {
            snprintf(reply, sizeof reply, "{\"jsonrpc\":\"2.0\",\"id\":%s,\"error\":{\"code\":-32603,\"message\":\"mini-lsp refuses to initialize\"}}", id_of(id, sizeof id));
            send_message(reply);
            note("initialize refused");
        } else if (strstr(body, "\"method\":\"initialize\"")) {
            snprintf(reply, sizeof reply, "{\"jsonrpc\":\"2.0\",\"id\":%s,\"result\":{\"capabilities\":{\"textDocumentSync\":1,\"hoverProvider\":true},\"serverInfo\":{\"name\":\"mini-lsp\"}}}", id_of(id, sizeof id));
            send_message(reply);
            note("initialize answered");
        } else if (strstr(body, "\"method\":\"initialized\"")) {
            note("initialized");
        } else if (strstr(body, "\"method\":\"textDocument/didOpen\"")) {
            quote_uri(uri, sizeof uri);
            note("didOpen");
            if (strcmp(mode, "crash") == 0) { note("crash"); return 3; }
            snprintf(reply, sizeof reply, "{\"jsonrpc\":\"2.0\",\"method\":\"textDocument/publishDiagnostics\",\"params\":{\"uri\":\"%s\",\"diagnostics\":[{\"range\":{\"start\":{\"line\":0,\"character\":0},\"end\":{\"line\":0,\"character\":4}},\"severity\":1,\"message\":\"mini-lsp diagnostic\"}]}}", uri);
            send_message(reply);
            note("diagnostics sent");
            if (strcmp(mode, "stall") == 0) { note("stall"); Delay(seconds * 50); note("stall expired"); return 0; }
        } else if (strstr(body, "\"method\":\"textDocument/hover\"")) {
            snprintf(reply, sizeof reply, "{\"jsonrpc\":\"2.0\",\"id\":%s,\"result\":{\"contents\":\"mini-lsp hover\"}}", id_of(id, sizeof id));
            send_message(reply);
            note("hover answered");
        } else if (strstr(body, "\"method\":\"shutdown\"")) {
            note("shutdown received");
            if (strcmp(mode, "spin") == 0) {
                struct timeval now, end;
                gettimeofday(&end, NULL);
                end.tv_sec += seconds;
                do {
                    volatile unsigned long loop;
                    for (loop = 0; loop < 2000000; loop++)
                        ;
                    gettimeofday(&now, NULL);
                } while (now.tv_sec < end.tv_sec);
                note("spin expired");
                return 0;
            }
            if (strcmp(mode, "ignore-shutdown") == 0) { Delay(seconds * 50); note("ignored shutdown expired"); return 0; }
            snprintf(reply, sizeof reply, "{\"jsonrpc\":\"2.0\",\"id\":%s,\"result\":null}", id_of(id, sizeof id));
            send_message(reply);
        } else if (strstr(body, "\"method\":\"exit\"")) {
            note("exit received");
            return 0;
        }
    }
    note("stdin closed");
    return 0;
}
