#include "gs/sftp.h"
#include "stage6_routes.h"
#include "catalog.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
    gs_archive_catalog_t catalog = {0};
    gs_manifest_t forwarded;
    char forwarded_json[GS_MANIFEST_MAX_BYTES];
    char forwarded_path[1024];
    FILE *forwarded_file;
    int forwarded_length;
    unsigned int port;
    int failures = 0;
    if (argc != 19) {
        fprintf(stderr, "usage: sftp_test HOST PORT USER KEY PIN ACK_DATA ACK_MANIFEST "
                        "RAW_DATA RAW_MANIFEST INBOX OUTBOX DOWNLOAD WRONG_ROUTE_DIR "
                        "MISSION_DATA MISSION_MANIFEST MPS_INBOX TLE_OUTBOX TLE_DOWNLOAD\n");
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
    failures += check(gs_stage6_upload(client, "N10", argv[6], argv[7],
                                       argv[10]) == 0,
                      "N10 upload data then manifest");
    failures += check(gs_sftp_upload_pair(client, argv[6], argv[7], argv[10],
                                          "ros", "mps", ack_types, 1u) != 0,
                      "reject overwrite");
    failures += check(gs_sftp_upload_pair(client, argv[6], argv[7], argv[10],
                                          "mps", "ros", ack_types, 1u) != 0,
                      "reject wrong route");
    {
        FILE *tampered = fopen(argv[6], "wb");
        failures += check(tampered != NULL, "open tampered fixture");
        if (tampered != NULL) {
            failures += check(fwrite("changed", 1u, 7u, tampered) == 7u,
                              "write tampered fixture");
            failures += check(fclose(tampered) == 0, "close tampered fixture");
            failures += check(gs_sftp_upload_pair(client, argv[6], argv[7], argv[10],
                                                  "ros", "mps", ack_types, 1u) != 0,
                              "reject altered payload hash");
        }
    }
    failures += check(gs_stage6_download(client, "N09", argv[11], "raw.bin",
                                         argv[12], 1024u) == 0,
                      "N09 read-only raw pull");
    failures += check(gs_sftp_download_pair(client, argv[11], "raw.bin", argv[13],
                                            "dgs-gs", "dgs-archive", raw_types, 2u,
                                            3u) != 0,
                      "reject oversized raw pull");
    failures += check(gs_sftp_download_pair(client, argv[11], "raw.bin", argv[13],
                                            "ros", "mps", raw_types, 2u, 1024u) != 0,
                      "reject wrong pull route");
    failures += check(gs_sftp_upload_pair(client, argv[8], argv[9], argv[11],
                                          "dgs-gs", "dgs-archive", raw_types, 2u) != 0,
                      "reject write into read-only outbox");
    failures += check(gs_stage6_upload(client, "N05", argv[14], argv[15],
                                       argv[10]) != 0,
                      "pull route cannot be used for upload");
    failures += check(gs_stage6_download(client, "N05", argv[17], "tle.bin",
                                         argv[18], 1024u) == 0,
                      "N05 FDS-initiated TLE pull");
    failures += check(gs_stage6_upload(client, "N16", argv[14], argv[15],
                                       argv[10]) == 0,
                      "N16 SDC-to-Archive upload");
    failures += check(gs_stage6_upload(client, "N18", argv[14], argv[15],
                                       argv[16]) != 0,
                      "N18 requires Archive sender manifest");
    failures += check(gs_archive_ingest(&catalog, "N16", argv[14], argv[15],
                                        1024u) == 0,
                      "Archive validates SDC input");
    failures += check(gs_archive_prepare_forward(&catalog, 0u, "N18",
                                                 &forwarded) == 0,
                      "Archive prepares MPS manifest");
    forwarded_length = gs_archive_manifest_json(&forwarded,
                                                 forwarded_json,
                                                 sizeof(forwarded_json));
    failures += check(forwarded_length > 0, "Archive serializes MPS manifest");
    if (forwarded_length > 0 &&
            snprintf(forwarded_path, sizeof(forwarded_path), "%s.forward",
                     argv[15]) > 0) {
        forwarded_file = fopen(forwarded_path, "wb");
        failures += check(forwarded_file != NULL, "open forwarded manifest");
        if (forwarded_file != NULL) {
            failures += check(fwrite(forwarded_json, 1u,
                                     (size_t)forwarded_length,
                                     forwarded_file) == (size_t)forwarded_length,
                              "write forwarded manifest");
            failures += check(fclose(forwarded_file) == 0,
                              "close forwarded manifest");
            failures += check(gs_stage6_upload(client, "N18", argv[14],
                                               forwarded_path,
                                               argv[16]) == 0,
                              "N18 Archive-to-MPS upload");
        }
    }
    gs_sftp_disconnect(client);
    return failures == 0 ? 0 : 1;
}
