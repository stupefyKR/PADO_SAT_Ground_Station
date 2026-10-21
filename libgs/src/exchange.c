#ifndef _WIN32
#define _POSIX_C_SOURCE 200809L
#endif

#include "gs/exchange.h"
#include "gs/manifest.h"

#include <stdio.h>

#ifdef _WIN32
#include <windows.h>
#else
#include <sys/stat.h>
#include <unistd.h>
#endif

int gs_exchange_publish(const char *temporary_path, const char *published_path)
{
    if (temporary_path == NULL || published_path == NULL) return -1;
#ifdef _WIN32
    {
        DWORD attributes = GetFileAttributesA(temporary_path);
        if (attributes == INVALID_FILE_ATTRIBUTES ||
                (attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) != 0) return -1;
        /* MoveFileA fails if the destination exists; never replace published data. */
        return MoveFileA(temporary_path, published_path) != 0 ? 0 : -1;
    }
#else
    {
        struct stat details;
        if (lstat(temporary_path, &details) != 0 || !S_ISREG(details.st_mode)) return -1;
        /* link() fails on an existing name and cannot cross filesystems. */
        if (link(temporary_path, published_path) != 0) return -1;
        if (unlink(temporary_path) == 0) return 0;
        (void)unlink(published_path);
        return -1;
    }
#endif
}

int gs_exchange_publish_pair(const char *temporary_data, const char *published_data,
                             const char *temporary_manifest, const char *published_manifest)
{
    if (gs_exchange_publish(temporary_data, published_data) != 0) return -1;
    /* The receiver only processes a pair after this final manifest rename. */
    return gs_exchange_publish(temporary_manifest, published_manifest);
}

int gs_exchange_verify_pair(const char *published_data, const char *published_manifest,
                            const char *expected_from, const char *expected_to,
                            const char *const *allowed_types, size_t allowed_count)
{
    FILE *file;
    char json[GS_MANIFEST_MAX_BYTES + 1u];
    size_t length;
    gs_manifest_t manifest;
    int result;
    if (published_data == NULL || published_manifest == NULL) return -1;
#ifdef _WIN32
    if (fopen_s(&file, published_manifest, "rb") != 0) return -1;
#else
    file = fopen(published_manifest, "rb");
#endif
    if (file == NULL) return -1;
    length = fread(json, 1u, sizeof(json), file);
    result = ferror(file) || length == sizeof(json) ? -1 : 0;
    if (fclose(file) != 0) result = -1;
    if (result != 0 || gs_manifest_parse(json, length, &manifest) != 0) return -1;
    return gs_manifest_verify(&manifest, expected_from, expected_to,
                              allowed_types, allowed_count, published_data);
}
