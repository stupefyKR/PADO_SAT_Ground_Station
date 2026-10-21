#include "stage6_routes.h"

#include <stdio.h>
#include <stdlib.h>

static int path(char *out, size_t capacity, const char *root,
                const char *id, const char *suffix)
{
    int length = snprintf(out, capacity, "%s/%s%s", root, id, suffix);
    return length > 0 && (size_t)length < capacity ? 0 : -1;
}

int main(int argc, char **argv)
{
    static const char *const ids[] = {"N05", "N08", "N09", "N10", "N11", "N12",
                                      "N13", "N14", "N15", "N16", "N17", "N18"};
    gs_sftp_client_t *client;
    unsigned long port;
    size_t index;
    if (argc != 9) return 2;
    port = strtoul(argv[2], NULL, 10);
    if (port == 0ul || port > 65535ul) return 2;
    client = gs_sftp_connect(argv[1], (unsigned int)port, argv[3],
                             argv[4], argv[5]);
    if (client == NULL) return 1;
    for (index = 0u; index < sizeof(ids) / sizeof(ids[0]); ++index) {
        const gs_stage6_route_t *route = gs_stage6_route(ids[index]);
        char data[1024];
        char manifest[1024];
        char remote[1024];
        char downloaded[1024];
        int result;
        if (route == NULL ||
                path(data, sizeof(data), argv[6], route->id, ".bin") != 0 ||
                path(manifest, sizeof(manifest), argv[6], route->id,
                     ".bin.manifest.json") != 0 ||
                path(remote, sizeof(remote), argv[7], route->id, "") != 0 ||
                path(downloaded, sizeof(downloaded), argv[8], route->id, "") != 0) {
            gs_sftp_disconnect(client);
            return 1;
        }
        if (route->direction == GS_STAGE6_PUSH)
            result = gs_stage6_upload(client, route->id, data, manifest, remote);
        else {
            char filename[32];
            int length = snprintf(filename, sizeof(filename), "%s.bin", route->id);
            if (length <= 0 || (size_t)length >= sizeof(filename)) result = -1;
            else result = gs_stage6_download(client, route->id, remote,
                                              filename, downloaded, 1024u);
        }
        if (result != 0) {
            fprintf(stderr, "N2 SFTP route failed: %s\n", route->id);
            gs_sftp_disconnect(client);
            return 1;
        }
    }
    gs_sftp_disconnect(client);
    return 0;
}
