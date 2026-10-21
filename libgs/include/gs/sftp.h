#ifndef GS_SFTP_H
#define GS_SFTP_H

#include <stddef.h>

typedef struct gs_sftp_client gs_sftp_client_t;

/* SHA-256 is the 64-character lowercase hexadecimal digest of the SSH host key. */
gs_sftp_client_t *gs_sftp_connect(const char *host, unsigned int port, const char *user,
                                  const char *private_key_path, const char *host_key_sha256);
void gs_sftp_disconnect(gs_sftp_client_t *client);

/* Data is published before its manifest; the receiver ignores orphan data. */
int gs_sftp_upload_pair(gs_sftp_client_t *client, const char *data_path,
                        const char *manifest_path, const char *remote_directory,
                        const char *expected_from, const char *expected_to,
                        const char *const *allowed_types, size_t allowed_count);

/* The caller chooses a bounded file size and an N2-approved route. */
int gs_sftp_download_pair(gs_sftp_client_t *client, const char *remote_directory,
                          const char *filename, const char *local_directory,
                          const char *expected_from, const char *expected_to,
                          const char *const *allowed_types, size_t allowed_count,
                          size_t max_data_bytes);

#endif
