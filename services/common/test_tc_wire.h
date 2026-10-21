#ifndef GS_TEST_TC_WIRE_H
#define GS_TEST_TC_WIRE_H

#include <stddef.h>
#include <stdint.h>

#define GS_TEST_TC_HEADER_SIZE 88u
#define GS_TEST_TC_FRAME_SIZE 120u
#define GS_TEST_TC_TAG_SIZE 32u

/* Synthetic wire layout. It is never a spacecraft TC packet or CLTU. */
void gs_test_tc_put32(unsigned char *out, uint32_t value);
void gs_test_tc_put64(unsigned char *out, uint64_t value);
uint32_t gs_test_tc_get32(const unsigned char *in);
uint64_t gs_test_tc_get64(const unsigned char *in);
int gs_test_hmac_sha256(const unsigned char *key, size_t key_length,
                        const unsigned char *data, size_t data_length,
                        unsigned char tag[GS_TEST_TC_TAG_SIZE]);

#endif
