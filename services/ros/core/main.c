#define _POSIX_C_SOURCE 200809L

#include "gs/framing.h"
#include "gs/hash.h"
#include "tm_frame.h"
#include "tm_sim_support.h"

#include <arpa/inet.h>
#include <errno.h>
#include <inttypes.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
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

static int connect_to_dgs(const struct sockaddr_in *local,
                          const struct sockaddr_in *destination)
{
    struct timespec pause = {0, 50000000L};
    unsigned int attempt;
    for (attempt = 0u; attempt < 100u; ++attempt) {
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0) return -1;
        if (socket_timeout(fd) != 0 ||
                bind(fd, (const struct sockaddr *)local, sizeof(*local)) != 0) {
            (void)close(fd);
            return -1;
        }
        if (connect(fd, (const struct sockaddr *)destination, sizeof(*destination)) == 0)
            return fd;
        {
            int connect_error = errno;
            (void)close(fd);
            if (connect_error != ECONNREFUSED && connect_error != ENETUNREACH) return -1;
        }
        (void)nanosleep(&pause, NULL);
    }
    return -1;
}

static int receive_frames(SSL *session, const char *store)
{
    unsigned char message[1u + GS_SIM_TM_MAX_FRAME];
    for (;;) {
        size_t length = 0u;
        uint32_t sequence = 0u;
        char hash[GS_SHA256_HEX_SIZE];
        char detail[128];
        if (gs_tls_recv_frame(session, message, sizeof(message), &length) != 0)
            return -1;
        if (length == 1u && message[0] == GS_SIM_MSG_END) return 0;
        if (length < 2u || message[0] != GS_SIM_MSG_TM ||
                gs_sim_tm_validate(message + 1u, length - 1u, &sequence) != GS_SIM_TM_VALID)
            return -1;
        if (gs_sim_record_append(store, 1u, message + 1u, length - 1u) != 0 ||
                gs_sha256_bytes(message + 1u, length - 1u, hash) != 0)
            return -1;
        if (snprintf(detail, sizeof(detail), "sim_sequence=%" PRIu32 " sha256=%s",
                     sequence, hash) < 0 ||
                gs_sim_log(stdout, "ros-core", "operations", "tm_recorded", detail) != 0)
            return -1;
    }
}

int main(int argc, char **argv)
{
    struct sockaddr_in destination = {0};
    struct sockaddr_in local = {0};
    SSL_CTX *context = NULL;
    SSL *session = NULL;
    int fd = -1;
    int result = 1;
    if (argc != 9 || strcmp(argv[1], "--sim-only") != 0 ||
            parse_address(&destination, argv[2], argv[4]) != 0 ||
            parse_address(&local, argv[3], argv[4]) != 0) {
        fprintf(stderr, "usage: ros_core_sim --sim-only DGS_IP ROS_IP PORT CERT KEY CA STORE\n");
        return 2;
    }
    local.sin_port = 0;
    (void)signal(SIGPIPE, SIG_IGN);
    context = gs_tls_context_new(argv[5], argv[6], argv[7], 0);
    if (context == NULL) goto done;
    fd = connect_to_dgs(&local, &destination);
    if (fd < 0) goto done;
    session = gs_tls_session_new(context, fd, 0, argv[2]);
    if (session == NULL || receive_frames(session, argv[8]) != 0) goto done;
    result = 0;
done:
    if (session != NULL) (void)SSL_shutdown(session);
    SSL_free(session);
    SSL_CTX_free(context);
    if (fd >= 0) (void)close(fd);
    return result;
}
