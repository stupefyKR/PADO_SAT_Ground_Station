#define _GNU_SOURCE

#include "gs/ipc.h"
#include "inputs.h"
#include "tm_frame.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/un.h>
#include <unistd.h>

#define ROS_RECORD_MAX_BYTES (1024u * 1024u)

static int read_regular(const char *path, size_t maximum, int private_owner,
                        int allow_absent, unsigned char **data, size_t *length,
                        int *absent)
{
    struct stat status;
    unsigned char *buffer;
    size_t done = 0u;
    int fd;
    int result = -1;
    *absent = 0;
    fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        if (allow_absent && errno == ENOENT) {
            *absent = 1;
            *data = NULL;
            *length = 0u;
            return 0;
        }
        return -1;
    }
    if (fstat(fd, &status) != 0 || !S_ISREG(status.st_mode) || status.st_nlink != 1 ||
            status.st_size < 0 || (uintmax_t)status.st_size > maximum ||
            (status.st_mode & 0022) != 0 ||
            (private_owner && (status.st_uid != geteuid() ||
                               (status.st_mode & 0077) != 0))) goto done;
    buffer = malloc((size_t)status.st_size + 1u);
    if (buffer == NULL) goto done;
    while (done < (size_t)status.st_size) {
        ssize_t count = read(fd, buffer + done, (size_t)status.st_size - done);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) {
            free(buffer);
            goto done;
        }
        done += (size_t)count;
    }
    buffer[done] = '\0';
    *data = buffer;
    *length = done;
    result = 0;
done:
    if (close(fd) != 0) {
        if (result == 0) {
            free(*data);
            *data = NULL;
            *length = 0u;
        }
        result = -1;
    }
    return result;
}

static int catalog(const char *path, char *output, size_t capacity)
{
    unsigned char *data = NULL;
    size_t length = 0u;
    int absent = 0;
    gs_mps_command_catalog_t parsed = {0};
    int result;
    if (read_regular(path, GS_MPS_COMMAND_DB_MAX_BYTES, 0, 0,
                     &data, &length, &absent) != 0) return -1;
    result = gs_mps_command_catalog_parse((const char *)data, length, &parsed);
    free(data);
    if (result != 0 || parsed.operational != 0 || parsed.command_count != 0u)
        return -1;
    result = snprintf(output, capacity,
                      "{\"version\":%u,\"command_count\":0,\"operational\":false}",
                      parsed.version);
    return result < 0 || (size_t)result >= capacity ? -1 : 0;
}

static uint32_t read32(const unsigned char *data)
{
    return ((uint32_t)data[0] << 24u) | ((uint32_t)data[1] << 16u) |
           ((uint32_t)data[2] << 8u) | (uint32_t)data[3];
}

static int ros_summary(const char *path, char *output, size_t capacity)
{
    unsigned char *data = NULL;
    size_t length = 0u;
    size_t cursor = 0u;
    unsigned int frames = 0u;
    uint32_t last_sequence = 0u;
    int absent = 0;
    int result;
    if (read_regular(path, ROS_RECORD_MAX_BYTES, 1, 1,
                     &data, &length, &absent) != 0) return -1;
    while (cursor < length) {
        size_t frame_length;
        if (length - cursor < 5u || data[cursor] != 1u) goto invalid;
        frame_length = (size_t)read32(data + cursor + 1u);
        if (frame_length < GS_SIM_TM_HEADER_SIZE + 5u ||
                frame_length > GS_SIM_TM_MAX_FRAME ||
                frame_length > length - cursor - 5u || frames >= 4096u ||
                gs_sim_tm_validate(data + cursor + 5u, frame_length,
                                   &last_sequence) != GS_SIM_TM_VALID) goto invalid;
        cursor += 5u + frame_length;
        ++frames;
    }
    free(data);
    if (frames == 0u)
        result = snprintf(output, capacity,
                          "{\"sim_frame_count\":0,\"last_sequence\":null,\"record_state\":\"%s\"}",
                          absent ? "absent" : "empty");
    else
        result = snprintf(output, capacity,
                          "{\"sim_frame_count\":%u,\"last_sequence\":%" PRIu32 ",\"record_state\":\"validated\"}",
                          frames, last_sequence);
    return result < 0 || (size_t)result >= capacity ? -1 : 0;
invalid:
    free(data);
    return -1;
}

static volatile sig_atomic_t stopping = 0;

static void request_stop(int signal_number)
{
    (void)signal_number;
    stopping = 1;
}

static int serve(const char *mode, const char *socket_path, const char *source)
{
    struct sockaddr_un address = {0};
    struct stat existing;
    struct sigaction action = {0};
    int listener = -1;
    int result = -1;
    int is_ros = strcmp(mode, "ros") == 0;
    if (!is_ros && strcmp(mode, "mps") != 0) return -1;
    if (strlen(socket_path) >= sizeof(address.sun_path)) return -1;
    address.sun_family = AF_UNIX;
    if (snprintf(address.sun_path, sizeof(address.sun_path), "%s",
                 socket_path) < 0) return -1;
    (void)umask(0077);
    if (lstat(socket_path, &existing) == 0) {
        if (!S_ISSOCK(existing.st_mode) || existing.st_uid != geteuid() ||
                unlink(socket_path) != 0) return -1;
    } else if (errno != ENOENT) return -1;
    listener = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (listener < 0 || bind(listener, (const struct sockaddr *)&address,
                             sizeof(address)) != 0) goto done;
    if (chmod(socket_path, 0600) != 0 || listen(listener, 16) != 0) goto done;
    action.sa_handler = request_stop;
    (void)sigemptyset(&action.sa_mask);
    if (sigaction(SIGTERM, &action, NULL) != 0 ||
            sigaction(SIGINT, &action, NULL) != 0) goto done;
    result = 0;
    while (!stopping) {
        char request[128];
        char response[256];
        size_t request_length = 0u;
        struct ucred peer;
        socklen_t peer_length = sizeof(peer);
        struct timeval timeout = {.tv_sec = 2, .tv_usec = 0};
        int client = accept(listener, NULL, NULL);
        if (client < 0) {
            if (errno == EINTR) continue;
            result = -1;
            break;
        }
        if (getsockopt(client, SOL_SOCKET, SO_PEERCRED, &peer, &peer_length) != 0 ||
                peer_length != sizeof(peer) || peer.uid != geteuid() ||
                setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout,
                           sizeof(timeout)) != 0 ||
                setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout,
                           sizeof(timeout)) != 0) {
            (void)close(client);
            continue;
        }
        if (gs_ipc_recv_json(client, request, sizeof(request),
                             &request_length) == 0 &&
                ((is_ros && strcmp(request, "{\"op\":\"overview\"}") == 0) ||
                 (!is_ros && strcmp(request, "{\"op\":\"catalog\"}") == 0))) {
            int read_result = is_ros ? ros_summary(source, response,
                                                   sizeof(response)) :
                                       catalog(source, response, sizeof(response));
            const char *reply = read_result == 0 ? response :
                                "{\"error\":\"core_unavailable\"}";
            (void)gs_ipc_send_json(client, reply, strlen(reply));
        }
        (void)close(client);
    }
done:
    if (listener >= 0) (void)close(listener);
    if (lstat(socket_path, &existing) == 0 && S_ISSOCK(existing.st_mode) &&
            existing.st_uid == geteuid()) (void)unlink(socket_path);
    return result;
}

int main(int argc, char **argv)
{
    int result = -1;
    char response[256];
    if (argc == 3 && strcmp(argv[1], "mps-catalog") == 0)
        result = catalog(argv[2], response, sizeof(response));
    else if (argc == 3 && strcmp(argv[1], "ros-summary") == 0)
        result = ros_summary(argv[2], response, sizeof(response));
    else if (argc == 5 && strcmp(argv[1], "serve") == 0)
        return serve(argv[2], argv[3], argv[4]) == 0 ? 0 : 1;
    if (result != 0) {
        (void)fprintf(stderr, "core probe unavailable\n");
        return 1;
    }
    (void)puts(response);
    return 0;
}
