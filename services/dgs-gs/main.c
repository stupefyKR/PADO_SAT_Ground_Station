#define _POSIX_C_SOURCE 200809L

#include "gs/framing.h"
#include "tm_frame.h"
#include "tm_sim_support.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

static int parse_address(struct sockaddr_in *address, const char *ip, const char *port)
{
    char *end = NULL;
    unsigned long value = strtoul(port, &end, 10);
    if (end == port || *end != '\0' || value == 0ul || value > 65535ul ||
            inet_pton(AF_INET, ip, &address->sin_addr) != 1 ||
            address->sin_addr.s_addr == INADDR_ANY) return -1;
    address->sin_family = AF_INET;
    address->sin_port = htons((unsigned short)value);
    return 0;
}

static int socket_timeout(int fd)
{
    struct timeval timeout = {5, 0};
    return setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) == 0 &&
           setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)) == 0 ? 0 : -1;
}

static size_t read_bytes(unsigned char *buffer, size_t length)
{
    size_t done = 0u;
    while (done < length) {
        size_t count = fread(buffer + done, 1u, length - done, stdin);
        if (count == 0u) break;
        done += count;
    }
    return done;
}

static int reject_frame(const char *quarantine, gs_sim_tm_result_t reason,
                        const unsigned char *frame, size_t length, const char *detail)
{
    if (gs_sim_record_append(quarantine, (unsigned char)reason, frame, length) != 0)
        return -1;
    return gs_sim_log(stderr, "dgs-gs", "dmz-gs", "tm_quarantined", detail);
}

static int forward_input(SSL *session, const char *quarantine)
{
    unsigned char prefix[4];
    unsigned char frame[GS_SIM_TM_MAX_FRAME];
    unsigned char message[1u + GS_SIM_TM_MAX_FRAME];
    for (;;) {
        uint32_t declared;
        gs_sim_tm_result_t verdict;
        uint32_t sequence = 0u;
        size_t got = read_bytes(prefix, sizeof(prefix));
        char detail[80];
        if (got == 0u && feof(stdin)) break;
        if (got != sizeof(prefix)) {
            if (reject_frame(quarantine, GS_SIM_TM_BAD_LENGTH, prefix, got,
                             "truncated_input_prefix") != 0) return -1;
            break;
        }
        declared = ((uint32_t)prefix[0] << 24u) | ((uint32_t)prefix[1] << 16u) |
                   ((uint32_t)prefix[2] << 8u) | (uint32_t)prefix[3];
        if (declared == 0u || declared > GS_SIM_TM_MAX_FRAME) {
            if (reject_frame(quarantine, GS_SIM_TM_BAD_LENGTH, prefix, sizeof(prefix),
                             "invalid_input_length") != 0) return -1;
            if (declared > GS_SIM_TM_MAX_FRAME) break;
            continue;
        }
        got = read_bytes(frame, declared);
        if (got != declared) {
            if (reject_frame(quarantine, GS_SIM_TM_BAD_LENGTH, frame, got,
                             "truncated_input_frame") != 0) return -1;
            break;
        }
        verdict = gs_sim_tm_validate(frame, got, &sequence);
        if (verdict != GS_SIM_TM_VALID) {
            if (reject_frame(quarantine, verdict, frame, got,
                             gs_sim_tm_result_name(verdict)) != 0) return -1;
            continue;
        }
        message[0] = GS_SIM_MSG_TM;
        memcpy(message + 1u, frame, got);
        if (gs_tls_send_frame(session, message, got + 1u) != 0) return -1;
        if (snprintf(detail, sizeof(detail), "sim_sequence=%u", sequence) < 0 ||
                gs_sim_log(stderr, "dgs-gs", "dmz-gs", "tm_forwarded", detail) != 0)
            return -1;
    }
    if (ferror(stdin)) return -1;
    message[0] = GS_SIM_MSG_END;
    return gs_tls_send_frame(session, message, 1u);
}

int main(int argc, char **argv)
{
    struct sockaddr_in local = {0};
    struct sockaddr_in expected = {0};
    struct sockaddr_in actual = {0};
    socklen_t actual_size = sizeof(actual);
    SSL_CTX *context = NULL;
    SSL *session = NULL;
    int listener = -1;
    int peer = -1;
    int result = 1;
    if (argc != 9 || strcmp(argv[1], "--sim-only") != 0 ||
            parse_address(&local, argv[2], argv[4]) != 0 ||
            parse_address(&expected, argv[3], argv[4]) != 0) {
        fprintf(stderr, "usage: dgs_gs_sim --sim-only BIND_IP ROS_IP PORT CERT KEY CA QUARANTINE\n");
        return 2;
    }
    (void)signal(SIGPIPE, SIG_IGN);
    context = gs_tls_context_new(argv[5], argv[6], argv[7], 1);
    if (context == NULL) goto done;
    listener = socket(AF_INET, SOCK_STREAM, 0);
    if (listener < 0 || socket_timeout(listener) != 0 ||
            bind(listener, (struct sockaddr *)&local, sizeof(local)) != 0 ||
            listen(listener, 1) != 0) goto done;
    peer = accept(listener, (struct sockaddr *)&actual, &actual_size);
    if (peer < 0 || actual_size != sizeof(actual) ||
            actual.sin_addr.s_addr != expected.sin_addr.s_addr ||
            socket_timeout(peer) != 0) goto done;
    session = gs_tls_session_new(context, peer, 1, argv[3]);
    if (session == NULL || forward_input(session, argv[8]) != 0) goto done;
    result = 0;
done:
    if (session != NULL) (void)SSL_shutdown(session);
    SSL_free(session);
    SSL_CTX_free(context);
    if (peer >= 0) (void)close(peer);
    if (listener >= 0) (void)close(listener);
    return result;
}
