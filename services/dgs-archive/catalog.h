#ifndef GS_DGS_ARCHIVE_CATALOG_H
#define GS_DGS_ARCHIVE_CATALOG_H

#include "gs/manifest.h"

#include <stddef.h>

#define GS_ARCHIVE_MAX_ITEMS 64u
#define GS_ARCHIVE_PATH_SIZE 512u

typedef struct {
    char path[GS_ARCHIVE_PATH_SIZE];
    gs_manifest_t inbound;
    char route_id[4];
} gs_archive_item_t;

typedef struct {
    gs_archive_item_t items[GS_ARCHIVE_MAX_ITEMS];
    size_t count;
} gs_archive_catalog_t;

/* SFTP intake is performed first; only a verified pair enters the catalog. */
int gs_archive_ingest(gs_archive_catalog_t *catalog, const char *route_id,
                      const char *data_path, const char *manifest_path,
                      size_t max_data_bytes);
/* A new manifest names archive as sender; original input is never relabelled. */
int gs_archive_prepare_forward(const gs_archive_catalog_t *catalog,
                               size_t item_index, const char *route_id,
                               gs_manifest_t *out);
int gs_archive_manifest_json(const gs_manifest_t *manifest,
                             char *output, size_t capacity);

#endif
