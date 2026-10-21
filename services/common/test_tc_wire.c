#include "test_tc_wire.h"

#include "gs/hash.h"

#include <string.h>

void gs_test_tc_put32(unsigned char *out, uint32_t value)
{
    out[0] = (unsigned char)(value >> 24);
    out[1] = (unsigned char)(value >> 16);
    out[2] = (unsigned char)(value >> 8);
    out[3] = (unsigned char)value;
}

void gs_test_tc_put64(unsigned char *out, uint64_t value)
{
    size_t index;
    for (index = 0u; index < 8u; ++index)
        out[index] = (unsigned char)(value >> (56u - 8u * index));
}

uint32_t gs_test_tc_get32(const unsigned char *in)
{
    return ((uint32_t)in[0] << 24) | ((uint32_t)in[1] << 16) |
           ((uint32_t)in[2] << 8) | in[3];
}

uint64_t gs_test_tc_get64(const unsigned char *in)
{
    uint64_t value = 0u;
    size_t index;
    for (index = 0u; index < 8u; ++index) value = (value << 8) | in[index];
    return value;
}

static int nibble(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

static int sha256_raw(const unsigned char *data, size_t length,
                      unsigned char digest[32])
{
    char hex[GS_SHA256_HEX_SIZE];
    size_t index;
    if (gs_sha256_bytes(data, length, hex) != 0) return -1;
    for (index = 0u; index < 32u; ++index) {
        int high = nibble(hex[index * 2u]);
        int low = nibble(hex[index * 2u + 1u]);
        if (high < 0 || low < 0) return -1;
        digest[index] = (unsigned char)((high << 4) | low);
    }
    return 0;
}

int gs_test_hmac_sha256(const unsigned char *key, size_t key_length,
                        const unsigned char *data, size_t data_length,
                        unsigned char tag[GS_TEST_TC_TAG_SIZE])
{
    unsigned char inner[64u + GS_TEST_TC_HEADER_SIZE];
    unsigned char outer[64u + 32u];
    unsigned char inner_digest[32];
    size_t index;
    int result;
    if (key == NULL || data == NULL || tag == NULL || key_length < 16u ||
            key_length > 64u || data_length != GS_TEST_TC_HEADER_SIZE) return -1;
    for (index = 0u; index < 64u; ++index) {
        unsigned char byte = index < key_length ? key[index] : 0u;
        inner[index] = (unsigned char)(byte ^ 0x36u);
        outer[index] = (unsigned char)(byte ^ 0x5cu);
    }
    for (index = 0u; index < data_length; ++index)
        inner[64u + index] = data[index];
    result = sha256_raw(inner, 64u + data_length, inner_digest);
    if (result == 0) {
        for (index = 0u; index < 32u; ++index)
            outer[64u + index] = inner_digest[index];
        result = sha256_raw(outer, sizeof(outer), tag);
    }
    for (index = 0u; index < sizeof(inner); ++index) inner[index] = 0u;
    for (index = 0u; index < sizeof(outer); ++index) outer[index] = 0u;
    for (index = 0u; index < sizeof(inner_digest); ++index)
        inner_digest[index] = 0u;
    return result;
}
