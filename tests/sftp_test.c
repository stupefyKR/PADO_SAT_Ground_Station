#include "gs/sftp.h"

#include <stdio.h>
#include <stdlib.h>

static int check(int condition, const char *name)
{
    if (!condition) fprintf(stderr, "FAIL: %s\n", name);
    return condition ? 0 : 1;
}

int main(int argc, char **argv)
{
    const char *const ack_types[] = {"ACK"};
    const char *const raw_types[] = {"CREST_RAW", "SIREN_RAW"};
    gs_sftp_client_t *client;
    unsigned int port;
    int failures = 0;
    if (argc != 14) {
        fprintf(stderr, "usage: sftp_test HOST PORT USER KEY PIN ACK_DATA ACK_MANIFEST "
                        "RAW_DATA RAW_MANIFEST INBOX OUTBOX DOWNLOAD WRONG_ROUTE_DIR\n");
        return 2;
    }
    port = (unsigned int)strtoul(argv[2], NULL, 10);
    client = gs_sftp_connect(argv[1], port, argv[3], argv[4],
                             "0000000000000000000000000000000000000000000000000000000000000000");
    failures += check(client == NULL, "reject wrong host key");
    gs_sftp_disconnect(client);
    client = gs_sftp_connect(argv[1], port, argv[3], argv[4], argv[5]);
    failures += check(client != NULL, "pinned host key and public key login");
    if (client == NULL) return 1;
    failures += check(gs_sftp_upload_pair(client, argv[6], argv[7], argv[10],
                                          "ros", "mps", ack_types, 1u) == 0,
                      "upload data then manifest");
    failures += check(gs_sftp_upload_pair(client, argv[6], argv[7], argv[10],
                                          "ros", "mps", ack_types, 1u) != 0,
                      "reject overwrite");
    failures += check(gs_sftp_upload_pair(client, argv[6], argv[7], argv[10],
                                          "mps", "ros", ack_types, 1u) != 0,
                      "reject wrong route");
    failures += check(gs_sftp_download_pair(client, argv[11], "raw.bin", argv[12],
                                            "dgs-gs", "dgs-archive", raw_types, 2u,
                                            1024u) == 0,
                      "F19 read-only raw pull");
    failures += check(gs_sftp_download_pair(client, argv[11], "raw.bin", argv[13],
                                            "ros", "mps", raw_types, 2u, 1024u) != 0,
                      "reject wrong pull route");
    failures += check(gs_sftp_upload_pair(client, argv[8], argv[9], argv[11],
                                          "dgs-gs", "dgs-archive", raw_types, 2u) != 0,
                      "reject write into read-only outbox");
    gs_sftp_disconnect(client);
    return failures == 0 ? 0 : 1;
}
