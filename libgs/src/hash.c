#include "gs/hash.h"

#include <stdio.h>
#include <stdlib.h>

#ifdef _WIN32
#include <windows.h>
#include <bcrypt.h>
#else
#include <openssl/evp.h>
#endif

static void hex_encode(const unsigned char digest[GS_SHA256_SIZE],
                       char out[GS_SHA256_HEX_SIZE])
{
    static const char digits[] = "0123456789abcdef";
    size_t index;
    for (index = 0; index < GS_SHA256_SIZE; ++index) {
        out[index * 2u] = digits[digest[index] >> 4u];
        out[index * 2u + 1u] = digits[digest[index] & 15u];
    }
    out[GS_SHA256_HEX_SIZE - 1u] = '\0';
}

#ifdef _WIN32
typedef struct {
    BCRYPT_ALG_HANDLE algorithm;
    BCRYPT_HASH_HANDLE hash;
    unsigned char *object;
} hash_context_t;

static int hash_init(hash_context_t *context)
{
    DWORD object_length = 0;
    DWORD result_length = 0;
    context->algorithm = NULL;
    context->hash = NULL;
    context->object = NULL;
    if (!BCRYPT_SUCCESS(BCryptOpenAlgorithmProvider(&context->algorithm,
            BCRYPT_SHA256_ALGORITHM, NULL, 0))) return -1;
    if (!BCRYPT_SUCCESS(BCryptGetProperty(context->algorithm, BCRYPT_OBJECT_LENGTH,
            (PUCHAR)&object_length, sizeof(object_length), &result_length, 0)) ||
            result_length != sizeof(object_length) || object_length == 0) return -1;
    context->object = (unsigned char *)malloc(object_length);
    if (context->object == NULL) return -1;
    if (!BCRYPT_SUCCESS(BCryptCreateHash(context->algorithm, &context->hash,
            context->object, object_length, NULL, 0, 0))) return -1;
    return 0;
}

static int hash_update(hash_context_t *context, const unsigned char *data, size_t length)
{
    if (length > 0xffffffffu) return -1;
    return BCRYPT_SUCCESS(BCryptHashData(context->hash, (PUCHAR)data,
                           (ULONG)length, 0)) ? 0 : -1;
}

static int hash_final(hash_context_t *context, unsigned char digest[GS_SHA256_SIZE])
{
    return BCRYPT_SUCCESS(BCryptFinishHash(context->hash, digest, GS_SHA256_SIZE, 0)) ? 0 : -1;
}

static void hash_cleanup(hash_context_t *context)
{
    if (context->hash != NULL) (void)BCryptDestroyHash(context->hash);
    free(context->object);
    if (context->algorithm != NULL) (void)BCryptCloseAlgorithmProvider(context->algorithm, 0);
}
#else
typedef struct {
    EVP_MD_CTX *context;
} hash_context_t;

static int hash_init(hash_context_t *context)
{
    context->context = EVP_MD_CTX_new();
    if (context->context == NULL) return -1;
    return EVP_DigestInit_ex(context->context, EVP_sha256(), NULL) == 1 ? 0 : -1;
}

static int hash_update(hash_context_t *context, const unsigned char *data, size_t length)
{
    return EVP_DigestUpdate(context->context, data, length) == 1 ? 0 : -1;
}

static int hash_final(hash_context_t *context, unsigned char digest[GS_SHA256_SIZE])
{
    unsigned int length = 0;
    if (EVP_DigestFinal_ex(context->context, digest, &length) != 1) return -1;
    return length == GS_SHA256_SIZE ? 0 : -1;
}

static void hash_cleanup(hash_context_t *context)
{
    EVP_MD_CTX_free(context->context);
}
#endif

int gs_sha256_bytes(const unsigned char *data, size_t length, char out[GS_SHA256_HEX_SIZE])
{
    hash_context_t context;
    unsigned char digest[GS_SHA256_SIZE];
    int result = -1;
    if (data == NULL || out == NULL) return -1;
    if (hash_init(&context) == 0 && hash_update(&context, data, length) == 0 &&
            hash_final(&context, digest) == 0) {
        hex_encode(digest, out);
        result = 0;
    }
    hash_cleanup(&context);
    return result;
}

int gs_sha256_file(const char *path, char out[GS_SHA256_HEX_SIZE])
{
    FILE *file;
    hash_context_t context;
    unsigned char buffer[65536];
    unsigned char digest[GS_SHA256_SIZE];
    size_t length;
    int result = -1;
    if (path == NULL || out == NULL) return -1;
#ifdef _WIN32
    if (fopen_s(&file, path, "rb") != 0) return -1;
#else
    file = fopen(path, "rb");
#endif
    if (file == NULL) return -1;
    if (hash_init(&context) != 0) goto cleanup;
    do {
        length = fread(buffer, 1u, sizeof(buffer), file);
        if (length > 0 && hash_update(&context, buffer, length) != 0) goto cleanup;
    } while (length == sizeof(buffer));
    if (ferror(file) || hash_final(&context, digest) != 0) goto cleanup;
    hex_encode(digest, out);
    result = 0;
cleanup:
    hash_cleanup(&context);
    (void)fclose(file);
    return result;
}
