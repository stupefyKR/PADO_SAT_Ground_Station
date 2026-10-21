#ifndef GS_FAKE_SAT_H
#define GS_FAKE_SAT_H

#include "test_tc_wire.h"

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t expected_test_scid;
    uint32_t last_sequence;
    int test_only;
} gs_fake_sat_t;

typedef struct {
    uint32_t sequence;
    uint32_t test_code;
    int accepted;
} gs_fake_sat_ack_t;

int gs_fake_sat_receive_test(gs_fake_sat_t *sat,
                             const unsigned char *frame, size_t length,
                             const unsigned char *key, size_t key_length,
                             gs_fake_sat_ack_t *ack);

#endif
