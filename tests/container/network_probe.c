#define _POSIX_C_SOURCE 200809L

#include "gs/framing.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

static int address_of(struct sockaddr_in *address, const char *ip, const char *port)
{
    char *end = NULL;
    unsigned long value = strtoul(port, &end, 10);
    if (end == port || *end != '\0' || value == 0 || value > 65535 ||
            inet_pton(AF_INET, ip, &address->sin_addr) != 1) return -1;
    address->sin_family = AF_INET;
    address->sin_port = htons((unsigned short)value);
    return 0;
}

static int set_timeout(int fd)
{
    struct timeval timeout = {5, 0};
    return setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) == 0 &&
           setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)) == 0 ? 0 : -1;
}

static int server(const char *bind_ip, const char *peer_ip, const char *cert,
                  const char *key, const char *ca, const char *port)
{
    struct sockaddr_in address = {0};
    unsigned char frame[8];
    size_t length = 0;
    SSL_CTX *context = NULL;
    SSL *session = NULL;
    int listener = -1;
    int peer = -1;
    int result = 1;
    if (address_of(&address, bind_ip, port) != 0) return 2;
    listener = socket(AF_INET, SOCK_STREAM, 0);
    if (listener < 0 || set_timeout(listener) != 0 ||
            bind(listener, (struct sockaddr *)&address, sizeof(address)) != 0 ||
            listen(listener, 1) != 0) goto done;
    context = gs_tls_context_new(cert, key, ca, 1);
    if (context == NULL) goto done;
    peer = accept(listener, NULL, NULL);
    if (peer < 0 || set_timeout(peer) != 0) goto done;
    session = gs_tls_session_new(context, peer, 1, peer_ip);
    if (session == NULL || gs_tls_recv_frame(session, frame, sizeof(frame), &length) != 0 ||
            length != 3u || memcmp(frame, "abc", 3u) != 0 ||
            gs_tls_send_frame(session, (const unsigned char *)"ok", 2u) != 0) goto done;
    result = 0;
done:
    SSL_free(session);
    SSL_CTX_free(context);
    if (peer >= 0) (void)close(peer);
    if (listener >= 0) (void)close(listener);
    return result;
}

static int client(const char *server_ip, const char *source_ip, const char *peer_ip,
                  const char *cert, const char *key, const char *ca, const char *port)
{
    struct sockaddr_in destination = {0};
    struct sockaddr_in source = {0};
    unsigned char frame[8];
    size_t length = 0;
    SSL_CTX *context = NULL;
    SSL *session = NULL;
    int fd = -1;
    int result = 1;
    if (address_of(&destination, server_ip, port) != 0 ||
            address_of(&source, source_ip, "1") != 0) return 2;
    source.sin_port = 0;
    fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0 || set_timeout(fd) != 0 ||
            bind(fd, (struct sockaddr *)&source, sizeof(source)) != 0 ||
            connect(fd, (struct sockaddr *)&destination, sizeof(destination)) != 0) goto done;
    context = gs_tls_context_new(cert, key, ca, 0);
    if (context == NULL) goto done;
    session = gs_tls_session_new(context, fd, 0, peer_ip);
    if (session == NULL || gs_tls_send_frame(session, (const unsigned char *)"abc", 3u) != 0 ||
            gs_tls_recv_frame(session, frame, sizeof(frame), &length) != 0 ||
            length != 2u || memcmp(frame, "ok", 2u) != 0) goto done;
    result = 0;
done:
    SSL_free(session);
    SSL_CTX_free(context);
    if (fd >= 0) (void)close(fd);
    return result;
}

int main(int argc, char **argv)
{
    if (argc == 8 && strcmp(argv[1], "server") == 0)
        return server(argv[2], argv[3], argv[4], argv[5], argv[6], argv[7]);
    if (argc == 9 && strcmp(argv[1], "client") == 0)
        return client(argv[2], argv[3], argv[4], argv[5], argv[6], argv[7], argv[8]);
    fprintf(stderr, "usage: network_probe server BIND_IP PEER_IP CERT KEY CA PORT\n"
                    "   or: network_probe client SERVER_IP SOURCE_IP PEER_IP CERT KEY CA PORT\n");
    return 2;
}
