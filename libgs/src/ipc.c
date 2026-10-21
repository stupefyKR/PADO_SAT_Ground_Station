#define _POSIX_C_SOURCE 200809L

#include "gs/ipc.h"
#include "cJSON.h"

#include <errno.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>

static int valid_json_object(const char *json, size_t length)
{
    char input[GS_IPC_MAX_BYTES + 1u];
    cJSON *root;
    if (json == NULL || length == 0u || length > GS_IPC_MAX_BYTES ||
            memchr(json, '\0', length) != NULL) return 0;
    memcpy(input, json, length);
    input[length] = '\0';
    root = cJSON_ParseWithLengthOpts(input, length + 1u, NULL, 1);
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return 0;
    }
    cJSON_Delete(root);
    return 1;
}

static int write_all(int fd, const unsigned char *data, size_t length)
{
    size_t done = 0u;
    while (done < length) {
        ssize_t count = send(fd, data + done, length - done, MSG_NOSIGNAL);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return -1;
        done += (size_t)count;
    }
    return 0;
}

static int read_all(int fd, unsigned char *data, size_t length)
{
    size_t done = 0u;
    while (done < length) {
        ssize_t count = recv(fd, data + done, length - done, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return -1;
        done += (size_t)count;
    }
    return 0;
}

int gs_ipc_send_json(int fd, const char *json, size_t length)
{
    unsigned char header[4];
    if (fd < 0 || !valid_json_object(json, length)) return -1;
    header[0] = (unsigned char)(length >> 24u);
    header[1] = (unsigned char)(length >> 16u);
    header[2] = (unsigned char)(length >> 8u);
    header[3] = (unsigned char)length;
    if (write_all(fd, header, sizeof(header)) != 0) return -1;
    return write_all(fd, (const unsigned char *)json, length);
}

int gs_ipc_recv_json(int fd, char *buffer, size_t capacity, size_t *length)
{
    unsigned char header[4];
    uint32_t frame_length;
    if (fd < 0 || buffer == NULL || length == NULL || capacity == 0u) return -1;
    if (read_all(fd, header, sizeof(header)) != 0) return -1;
    frame_length = ((uint32_t)header[0] << 24u) | ((uint32_t)header[1] << 16u) |
                   ((uint32_t)header[2] << 8u) | (uint32_t)header[3];
    if (frame_length == 0u || frame_length > GS_IPC_MAX_BYTES ||
            frame_length >= capacity) return -1;
    if (read_all(fd, (unsigned char *)buffer, frame_length) != 0) return -1;
    buffer[frame_length] = '\0';
    if (!valid_json_object(buffer, frame_length)) return -1;
    *length = frame_length;
    return 0;
}
