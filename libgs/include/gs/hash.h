#ifndef GS_HASH_H
#define GS_HASH_H

#include <stddef.h>

#define GS_SHA256_SIZE 32u
#define GS_SHA256_HEX_SIZE 65u

int gs_sha256_bytes(const unsigned char *data, size_t length, char out[GS_SHA256_HEX_SIZE]);
int gs_sha256_file(const char *path, char out[GS_SHA256_HEX_SIZE]);

#endif
