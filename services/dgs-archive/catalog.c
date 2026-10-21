#ifndef _WIN32
#define _POSIX_C_SOURCE 200809L
#endif

#include "catalog.h"

#include "gs/hash.h"
#include "stage6_routes.h"

#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#include <windows.h>
#else
#include <sys/stat.h>
#endif

static int read_manifest(const char *path, gs_manifest_t *out)
{
    char json[GS_MANIFEST_MAX_BYTES + 1u];
    FILE *file;
    size_t length;
    int failed;
#ifdef _WIN32
    if (fopen_s(&file, path, "rb") != 0) return -1;
#else
    file = fopen(path, "rb");
#endif
    if (file == NULL) return -1;
    length = fread(json, 1u, sizeof(json), file);
    failed = ferror(file) || length == sizeof(json);
    if (fclose(file) != 0) failed = 1;
    return failed ? -1 : gs_manifest_parse(json, length, out);
}

static int bounded_file(const char *path, size_t limit)
{
    FILE *file;
    size_t total = 0u;
    unsigned char buffer[4096];
    size_t length;
    int failed = 0;
#ifdef _WIN32
    DWORD attributes = GetFileAttributesA(path);
    if (attributes == INVALID_FILE_ATTRIBUTES ||
            (attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) != 0)
        return -1;
    if (fopen_s(&file, path, "rb") != 0) return -1;
#else
    struct stat details;
    if (lstat(path, &details) != 0 || !S_ISREG(details.st_mode)) return -1;
    file = fopen(path, "rb");
#endif
    if (file == NULL) return -1;
    while ((length = fread(buffer, 1u, sizeof(buffer), file)) != 0u) {
        if (length > limit - total) {
            failed = 1;
            break;
        }
        total += length;
    }
    if (ferror(file)) failed = 1;
    if (fclose(file) != 0) failed = 1;
    return failed || total == 0u ? -1 : 0;
}

int gs_archive_ingest(gs_archive_catalog_t *catalog, const char *route_id,
                      const char *data_path, const char *manifest_path,
                      size_t max_data_bytes)
{
    const gs_stage6_route_t *route = gs_stage6_route(route_id);
    gs_archive_item_t item = {0};
    size_t index;
    size_t path_length;
    if (catalog == NULL || route == NULL || data_path == NULL ||
            manifest_path == NULL || max_data_bytes == 0u ||
            catalog->count >= GS_ARCHIVE_MAX_ITEMS ||
            strcmp(route->data_to, "dgs-archive") != 0 ||
            (strcmp(route_id, "N09") != 0 &&
             strcmp(route_id, "N14") != 0 &&
             strcmp(route_id, "N15") != 0 &&
             strcmp(route_id, "N16") != 0) ||
            read_manifest(manifest_path, &item.inbound) != 0 ||
            gs_stage6_route_allows(route_id, item.inbound.from,
                                   item.inbound.to, item.inbound.type,
                                   route->connection_from,
                                   route->connection_to,
                                   route->direction) != 0 ||
            bounded_file(data_path, max_data_bytes) != 0 ||
            gs_manifest_verify(&item.inbound, route->data_from,
                               route->data_to, route->types,
                               route->type_count, data_path) != 0)
        return -1;
    path_length = strlen(data_path);
    if (path_length == 0u || path_length >= sizeof(item.path)) return -1;
    for (index = 0u; index <= path_length; ++index) item.path[index] = data_path[index];
    for (index = 0u; index < 4u; ++index) item.route_id[index] = route_id[index];
    for (index = 0u; index < catalog->count; ++index)
        if (strcmp(catalog->items[index].inbound.file, item.inbound.file) == 0 &&
                strcmp(catalog->items[index].inbound.from, item.inbound.from) == 0)
            return -1;
    catalog->items[catalog->count++] = item;
    return 0;
}

int gs_archive_prepare_forward(const gs_archive_catalog_t *catalog,
                               size_t item_index, const char *route_id,
                               gs_manifest_t *out)
{
    const gs_archive_item_t *item;
    const gs_stage6_route_t *route = gs_stage6_route(route_id);
    char digest[GS_SHA256_HEX_SIZE];
    gs_manifest_t candidate;
    size_t index;
    int permitted;
    if (catalog == NULL || out == NULL || route == NULL ||
            item_index >= catalog->count ||
            (strcmp(route_id, "N17") != 0 && strcmp(route_id, "N18") != 0))
        return -1;
    item = &catalog->items[item_index];
    permitted = (strcmp(route_id, "N17") == 0 &&
                 (strcmp(item->route_id, "N09") == 0 ||
                  strcmp(item->route_id, "N14") == 0 ||
                  strcmp(item->route_id, "N15") == 0)) ||
                (strcmp(route_id, "N18") == 0 &&
                 strcmp(item->route_id, "N16") == 0);
    if (!permitted ||
            gs_sha256_file(item->path, digest) != 0 ||
            strcmp(digest, item->inbound.sha256) != 0 ||
            gs_stage6_route_allows(route_id, route->data_from,
                                   route->data_to, item->inbound.type,
                                   route->connection_from,
                                   route->connection_to,
                                   route->direction) != 0) return -1;
    candidate = item->inbound;
    for (index = 0u; index < sizeof(candidate.from); ++index) candidate.from[index] = 0;
    for (index = 0u; index < sizeof(candidate.to); ++index) candidate.to[index] = 0;
    (void)snprintf(candidate.from, sizeof(candidate.from), "%s", route->data_from);
    (void)snprintf(candidate.to, sizeof(candidate.to), "%s", route->data_to);
    *out = candidate;
    return 0;
}

int gs_archive_manifest_json(const gs_manifest_t *manifest,
                             char *output, size_t capacity)
{
    gs_manifest_t parsed;
    int length;
    if (manifest == NULL || output == NULL || capacity == 0u) return -1;
    /* All fields were checked by the strict parser before catalog insertion. */
    length = snprintf(output, capacity,
                      "{\"file\":\"%s\",\"type\":\"%s\",\"from\":\"%s\","
                      "\"to\":\"%s\",\"version\":%u,\"created\":\"%s\","
                      "\"sha256\":\"%s\"}",
                      manifest->file, manifest->type, manifest->from,
                      manifest->to, manifest->version, manifest->created,
                      manifest->sha256);
    if (length < 0 || (size_t)length >= capacity ||
            gs_manifest_parse(output, (size_t)length, &parsed) != 0 ||
            strcmp(parsed.file, manifest->file) != 0 ||
            strcmp(parsed.type, manifest->type) != 0 ||
            strcmp(parsed.from, manifest->from) != 0 ||
            strcmp(parsed.to, manifest->to) != 0 ||
            parsed.version != manifest->version ||
            strcmp(parsed.sha256, manifest->sha256) != 0) return -1;
    return length;
}
