#ifndef GS_MANIFEST_H
#define GS_MANIFEST_H

#include <stddef.h>

#define GS_MANIFEST_MAX_BYTES 4096u

typedef struct {
    char file[256];
    char type[64];
    char from[64];
    char to[64];
    unsigned int version;
    char created[21];
    char sha256[65];
} gs_manifest_t;

/* Caller owns the output struct; no heap ownership crosses this API. */
int gs_manifest_parse(const char *json, size_t length, gs_manifest_t *out);
int gs_manifest_verify(const gs_manifest_t *manifest, const char *expected_from,
                       const char *expected_to, const char *const *allowed_types,
                       size_t allowed_count, const char *data_path);

#endif
