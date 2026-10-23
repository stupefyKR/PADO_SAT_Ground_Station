#define _GNU_SOURCE

#include "gs/ipc.h"
#include "gs/manifest.h"
#include "fds_schedule.h"
#include "inputs.h"
#include "tm_frame.h"
#include "cJSON.h"

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

static int schedule_name(const char *name)
{
    size_t index;
    size_t length = strlen(name);
    if (length < 5u || length > 80u || strncmp(name, "fds-", 4u) != 0)
        return 0;
    for (index = 4u; index < length; ++index)
        if (!((name[index] >= 'a' && name[index] <= 'z') ||
              (name[index] >= 'A' && name[index] <= 'Z') ||
              (name[index] >= '0' && name[index] <= '9') ||
              name[index] == '_' || name[index] == '-')) return 0;
    return 1;
}

static int schedule_passes(const char *root, char **output)
{
    char marker_path[1024];
    char run_path[1024];
    char csv_path[1024];
    char manifest_path[1024];
    unsigned char *marker = NULL, *csv = NULL, *manifest = NULL;
    size_t marker_length = 0u, csv_length = 0u, manifest_length = 0u;
    int absent = 0;
    int result = -1;
    struct stat status;
    gs_mps_fds_schedule_t *schedule = NULL;
    cJSON *document = NULL, *passes = NULL;
    size_t index;
    if (root == NULL || strlen(root) > 700u ||
            lstat(root, &status) != 0 || !S_ISDIR(status.st_mode) ||
            (status.st_mode & 0022) != 0 ||
            snprintf(marker_path, sizeof(marker_path), "%s/current.txt", root) >=
                (int)sizeof(marker_path) ||
            read_regular(marker_path, 81u, 0, 1, &marker, &marker_length, &absent) != 0)
        goto done;
    document = cJSON_CreateObject();
    passes = cJSON_CreateArray();
    if (document == NULL || passes == NULL ||
            !cJSON_AddItemToObject(document, "passes", passes)) goto done;
    passes = NULL;
    if (absent) {
        if (!cJSON_AddStringToObject(document, "schedule_state", "absent") ||
                !cJSON_AddNullToObject(document, "schedule_sha256")) goto done;
    } else {
        if (marker_length == 0u || marker[marker_length - 1u] != '\n' ||
                memchr(marker, '\0', marker_length - 1u) != NULL ||
                memchr(marker, '\n', marker_length - 1u) != NULL) goto done;
        marker[marker_length - 1u] = '\0';
        if (!schedule_name((const char *)marker) ||
                snprintf(run_path, sizeof(run_path), "%s/%s", root, marker) >=
                    (int)sizeof(run_path) ||
                lstat(run_path, &status) != 0 || !S_ISDIR(status.st_mode) ||
                (status.st_mode & 0022) != 0 ||
                snprintf(csv_path, sizeof(csv_path), "%s/contact_schedule.csv", run_path) >=
                    (int)sizeof(csv_path) ||
                snprintf(manifest_path, sizeof(manifest_path),
                         "%s/contact_schedule.csv.manifest.json", run_path) >=
                    (int)sizeof(manifest_path) ||
                read_regular(csv_path, GS_MPS_FDS_SCHEDULE_MAX_BYTES, 0, 0,
                             &csv, &csv_length, &absent) != 0 ||
                read_regular(manifest_path, GS_MANIFEST_MAX_BYTES, 0, 0,
                             &manifest, &manifest_length, &absent) != 0) goto done;
        schedule = calloc(1u, sizeof(*schedule));
        if (schedule == NULL ||
                gs_mps_fds_schedule_parse((const char *)csv, csv_length,
                                          (const char *)manifest, manifest_length,
                                          schedule) != 0) goto done;
        for (index = 0u; index < schedule->passes.count; ++index) {
            const gs_mps_pass_t *pass = &schedule->passes.passes[index];
            const gs_mps_fds_pass_info_t *info = &schedule->info[index];
            cJSON *item = cJSON_CreateObject();
            if (item == NULL ||
                    !cJSON_AddStringToObject(item, "pass_id", pass->pass_id) ||
                    !cJSON_AddStringToObject(item, "station_id", info->station_id) ||
                    !cJSON_AddStringToObject(item, "band", info->band) ||
                    !cJSON_AddNumberToObject(item, "aos_utc_s", (double)pass->aos_utc_s) ||
                    !cJSON_AddNumberToObject(item, "los_utc_s", (double)pass->los_utc_s) ||
                    !cJSON_AddNumberToObject(item, "peak_utc_s", (double)info->peak_utc_s) ||
                    !cJSON_AddNumberToObject(item, "max_elevation_deg", info->max_elevation_deg) ||
                    !cJSON_AddNumberToObject(item, "minimum_elevation_deg", info->minimum_elevation_deg) ||
                    !cJSON_AddBoolToObject(item, "clipped_start", info->clipped_start) ||
                    !cJSON_AddBoolToObject(item, "clipped_end", info->clipped_end) ||
                    !cJSON_AddItemToArray(cJSON_GetObjectItemCaseSensitive(document, "passes"), item)) {
                cJSON_Delete(item);
                goto done;
            }
        }
        if (!cJSON_AddStringToObject(document, "schedule_state", "validated") ||
                !cJSON_AddStringToObject(document, "schedule_sha256", schedule->sha256))
            goto done;
    }
    if (!cJSON_AddBoolToObject(document, "operational", 0)) goto done;
    *output = cJSON_PrintUnformatted(document);
    if (*output == NULL || strlen(*output) > 60000u) {
        free(*output);
        *output = NULL;
        goto done;
    }
    result = 0;
done:
    free(marker);
    free(csv);
    free(manifest);
    free(schedule);
    cJSON_Delete(document);
    cJSON_Delete(passes);
    return result;
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

static int serve(const char *mode, const char *socket_path, const char *source,
                 const char *schedule_root)
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
        char *schedule_response = NULL;
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
                 (!is_ros && (strcmp(request, "{\"op\":\"catalog\"}") == 0 ||
                              strcmp(request, "{\"op\":\"passes\"}") == 0)))) {
            int read_result;
            if (is_ros) read_result = ros_summary(source, response, sizeof(response));
            else if (strcmp(request, "{\"op\":\"catalog\"}") == 0)
                read_result = catalog(source, response, sizeof(response));
            else read_result = schedule_root == NULL ? -1 :
                schedule_passes(schedule_root, &schedule_response);
            const char *reply = read_result == 0 ?
                                (schedule_response == NULL ? response : schedule_response) :
                                "{\"error\":\"core_unavailable\"}";
            (void)gs_ipc_send_json(client, reply, strlen(reply));
            free(schedule_response);
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
    else if ((argc == 5 || argc == 6) && strcmp(argv[1], "serve") == 0)
        return serve(argv[2], argv[3], argv[4], argc == 6 ? argv[5] : NULL) == 0 ? 0 : 1;
    if (result != 0) {
        (void)fprintf(stderr, "core probe unavailable\n");
        return 1;
    }
    (void)puts(response);
    return 0;
}
