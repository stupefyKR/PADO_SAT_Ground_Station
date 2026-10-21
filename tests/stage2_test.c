#define _POSIX_C_SOURCE 200809L

#include "gs/framing.h"
#include "gs/ipc.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

static int failures = 0;

static void expect(int condition, const char *name)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", name);
        ++failures;
    }
}

static void test_ipc(void)
{
    int pair[2];
    char buffer[128];
    size_t length = 0u;
    const unsigned char invalid[] = {0, 0, 0, 2, '[', ']'};
    const unsigned char oversized[] = {0, 2, 0, 1};
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, pair) != 0) {
        expect(0, "IPC socketpair");
        return;
    }
    expect(gs_ipc_send_json(pair[0], "{\"ok\":true}", 11u) == 0, "IPC send JSON");
    expect(gs_ipc_recv_json(pair[1], buffer, sizeof(buffer), &length) == 0,
           "IPC receive JSON");
    expect(length == 11u && strcmp(buffer, "{\"ok\":true}") == 0, "IPC content");
    expect(gs_ipc_send_json(pair[0], "[]", 2u) != 0, "IPC reject array");
    expect(gs_ipc_send_json(pair[0], "{}{}", 4u) != 0, "IPC reject trailing JSON");
    expect(send(pair[0], invalid, sizeof(invalid), 0) == (ssize_t)sizeof(invalid),
           "IPC inject invalid frame");
    expect(gs_ipc_recv_json(pair[1], buffer, sizeof(buffer), &length) != 0,
           "IPC reject non-object frame");
    expect(send(pair[0], oversized, sizeof(oversized), 0) == (ssize_t)sizeof(oversized),
           "IPC inject oversized length");
    expect(gs_ipc_recv_json(pair[1], buffer, sizeof(buffer), &length) != 0,
           "IPC reject oversized frame");
    expect(close(pair[0]) == 0, "IPC close sender");
    expect(gs_ipc_recv_json(pair[1], buffer, sizeof(buffer), &length) != 0,
           "IPC reject EOF");
    expect(close(pair[1]) == 0, "IPC close receiver");
}

static int bind_ip(int fd, const char *ip, unsigned short port)
{
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    if (inet_pton(AF_INET, ip, &address.sin_addr) != 1) return -1;
    return bind(fd, (const struct sockaddr *)&address, sizeof(address));
}

static int server_child(int listener, const char *server_cert, const char *server_key,
                        const char *ca_path, int mode)
{
    unsigned char buffer[16];
    const unsigned char oversized[] = {0, 16, 0, 1};
    size_t length = 0u;
    SSL_CTX *context = gs_tls_context_new(server_cert, server_key, ca_path, 1);
    SSL *session;
    int client;
    int result = 1;
    if (context == NULL) return 1;
    client = accept(listener, NULL, NULL);
    if (client < 0) goto done;
    session = gs_tls_session_new(context, client, 1, "127.0.0.3");
    if (mode == 1) {
        result = session == NULL ? 0 : 1;
    } else if (mode == 2) {
        result = session != NULL &&
                 SSL_write(session, oversized, sizeof(oversized)) == (int)sizeof(oversized) ? 0 : 1;
    } else if (session != NULL &&
               gs_tls_recv_frame(session, buffer, sizeof(buffer), &length) == 0 &&
               length == 3u && memcmp(buffer, "abc", 3u) == 0 &&
               gs_tls_send_frame(session, (const unsigned char *)"ok", 2u) == 0) {
        result = 0;
    }
    SSL_free(session);
    (void)close(client);
done:
    SSL_CTX_free(context);
    (void)close(listener);
    return result;
}

static void test_tls(const char *server_cert, const char *server_key,
                     const char *client_cert, const char *client_key,
                     const char *ca_path, int mode)
{
    struct sockaddr_in address = {0};
    socklen_t address_size = sizeof(address);
    unsigned char buffer[16];
    size_t length = 0u;
    int listener = socket(AF_INET, SOCK_STREAM, 0);
    int client = -1;
    int status = 0;
    pid_t child;
    SSL_CTX *context = NULL;
    SSL *session = NULL;
    expect(listener >= 0, "TLS listener socket");
    if (listener < 0) return;
    expect(bind_ip(listener, "127.0.0.2", 0) == 0, "TLS explicit server bind");
    if (getsockname(listener, (struct sockaddr *)&address, &address_size) != 0 ||
            listen(listener, 1) != 0) goto done;
    child = fork();
    expect(child >= 0, "TLS fork");
    if (child < 0) goto done;
    if (child == 0) _exit(server_child(listener, server_cert, server_key,
                                      ca_path, mode));
    client = socket(AF_INET, SOCK_STREAM, 0);
    if (client < 0 || bind_ip(client, "127.0.0.3", 0) != 0 ||
            connect(client, (const struct sockaddr *)&address, sizeof(address)) != 0) {
        expect(0, "TLS explicit client bind and connect");
        goto parent_done;
    }
    context = gs_tls_context_new(client_cert, client_key, ca_path, 0);
    expect(context != NULL, "TLS client context");
    if (context == NULL) goto parent_done;
    session = gs_tls_session_new(context, client, 0,
                                 mode == 1 ? "127.0.0.9" : "127.0.0.2");
    if (mode == 1) {
        expect(session == NULL, "TLS reject wrong peer IP");
    } else {
        expect(session != NULL, "TLS mTLS handshake");
        if (session != NULL && mode == 2) {
            expect(gs_tls_recv_frame(session, buffer, sizeof(buffer), &length) != 0,
                   "TLS reject oversized frame length");
        } else if (session != NULL) {
            expect(gs_tls_send_frame(session, (const unsigned char *)"abc", 3u) == 0,
                   "TLS frame send");
            expect(gs_tls_recv_frame(session, buffer, sizeof(buffer), &length) == 0 &&
                   length == 2u && memcmp(buffer, "ok", 2u) == 0,
                   "TLS frame receive");
            expect(gs_tls_send_frame(session, buffer, GS_TLS_FRAME_MAX_BYTES + 1u) != 0,
                   "TLS reject oversized send");
        }
    }
parent_done:
    SSL_free(session);
    SSL_CTX_free(context);
    if (client >= 0) (void)close(client);
    if (waitpid(child, &status, 0) < 0 || !WIFEXITED(status) || WEXITSTATUS(status) != 0)
        expect(0, "TLS server result");
done:
    (void)close(listener);
}

int main(int argc, char **argv)
{
    if (argc != 6) {
        fprintf(stderr, "usage: stage2_test SERVER_CERT SERVER_KEY CLIENT_CERT CLIENT_KEY CA\n");
        return 2;
    }
    test_ipc();
    test_tls(argv[1], argv[2], argv[3], argv[4], argv[5], 0);
    test_tls(argv[1], argv[2], argv[3], argv[4], argv[5], 1);
    test_tls(argv[1], argv[2], argv[3], argv[4], argv[5], 2);
    return failures == 0 ? 0 : 1;
}
