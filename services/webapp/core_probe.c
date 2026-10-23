#define _POSIX_C_SOURCE 200809L

#include "inputs.h"
#include "tm_frame.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
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

static int catalog(const char *path)
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
    return printf("{\"version\":%u,\"command_count\":0,\"operational\":false}\n",
                  parsed.version) < 0 ? -1 : 0;
}

static uint32_t read32(const unsigned char *data)
{
    return ((uint32_t)data[0] << 24u) | ((uint32_t)data[1] << 16u) |
           ((uint32_t)data[2] << 8u) | (uint32_t)data[3];
}

static int ros_summary(const char *path)
{
    unsigned char *data = NULL;
    size_t length = 0u;
    size_t cursor = 0u;
    unsigned int frames = 0u;
    uint32_t last_sequence = 0u;
    int absent = 0;
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
        return printf("{\"sim_frame_count\":0,\"last_sequence\":null,\"record_state\":\"%s\"}\n",
                      absent ? "absent" : "empty") < 0 ? -1 : 0;
    return printf("{\"sim_frame_count\":%u,\"last_sequence\":%" PRIu32 ",\"record_state\":\"validated\"}\n",
                  frames, last_sequence) < 0 ? -1 : 0;
invalid:
    free(data);
    return -1;
}

int main(int argc, char **argv)
{
    int result = -1;
    if (argc == 3 && strcmp(argv[1], "mps-catalog") == 0)
        result = catalog(argv[2]);
    else if (argc == 3 && strcmp(argv[1], "ros-summary") == 0)
        result = ros_summary(argv[2]);
    if (result != 0) {
        (void)fprintf(stderr, "core probe unavailable\n");
        return 1;
    }
    return 0;
}
