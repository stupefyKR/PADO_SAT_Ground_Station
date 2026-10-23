#include "stage6_routes.h"
#include "gs/sftp.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int port_number(const char *text, unsigned int *out)
{
    char *end;
    unsigned long value;
    errno = 0;
    value = strtoul(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value == 0u ||
            value > 65535u) return -1;
    *out = (unsigned int)value;
    return 0;
}

int main(int argc, char **argv)
{
    gs_sftp_client_t *client;
    unsigned int port;
    int result;
    if (argc != 10 || port_number(argv[3], &port) != 0 ||
            (strcmp(argv[1], "pull-n05") != 0 &&
             strcmp(argv[1], "push-n13") != 0 &&
             strcmp(argv[1], "push-n15") != 0)) {
        (void)fprintf(stderr,
                      "usage: gs_fds_exchange pull-n05|push-n13|push-n15 "
                      "HOST PORT USER PRIVATE_KEY HOST_KEY_SHA256 REMOTE_DIR "
                      "FILE_OR_NAME MANIFEST_OR_LOCAL_DIR\n");
        return 2;
    }
    client = gs_sftp_connect(argv[2], port, argv[4], argv[5], argv[6]);
    if (client == NULL) {
        (void)fprintf(stderr, "FDS SFTP peer authentication failed\n");
        return 1;
    }
    if (strcmp(argv[1], "pull-n05") == 0)
        result = gs_stage6_download(client, "N05", argv[7], argv[8], argv[9], 256u);
    else
        result = gs_stage6_upload(client,
                                  strcmp(argv[1], "push-n13") == 0 ? "N13" : "N15",
                                  argv[8], argv[9], argv[7]);
    gs_sftp_disconnect(client);
    if (result != 0) {
        (void)fprintf(stderr, "FDS SFTP file pair transfer failed\n");
        return 1;
    }
    return 0;
}
