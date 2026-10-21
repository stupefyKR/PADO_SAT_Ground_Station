#ifndef GS_STAGE6_ROUTES_H
#define GS_STAGE6_ROUTES_H

#include "gs/sftp.h"

#include <stddef.h>

typedef enum { GS_STAGE6_PUSH = 1, GS_STAGE6_PULL = 2 } gs_stage6_direction_t;

/* Only N2 routes with a confirmed internal SFTP connection are listed. */
typedef struct {
    const char *id;
    const char *data_from;
    const char *data_to;
    const char *connection_from;
    const char *connection_to;
    gs_stage6_direction_t direction;
    const char *const *types;
    size_t type_count;
} gs_stage6_route_t;

const gs_stage6_route_t *gs_stage6_route(const char *id);
int gs_stage6_route_allows(const char *id, const char *data_from,
                           const char *data_to, const char *type,
                           const char *connection_from,
                           const char *connection_to,
                           gs_stage6_direction_t direction);
int gs_stage6_upload(gs_sftp_client_t *client, const char *id,
                     const char *data_path, const char *manifest_path,
                     const char *remote_directory);
int gs_stage6_download(gs_sftp_client_t *client, const char *id,
                       const char *remote_directory, const char *filename,
                       const char *local_directory, size_t max_data_bytes);

#endif
