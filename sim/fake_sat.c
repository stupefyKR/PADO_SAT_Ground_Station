#include "fake_sat.h"

#include <string.h>

int gs_fake_sat_receive_test(gs_fake_sat_t *sat,
                             const unsigned char *frame, size_t length,
                             const unsigned char *key, size_t key_length,
                             gs_fake_sat_ack_t *ack)
{
    static const unsigned char magic[8] = {'P','A','D','O','T','C','5','!'};
    unsigned char expected[GS_TEST_TC_TAG_SIZE];
    unsigned char difference = 0u;
    uint32_t sequence;
    size_t index;
    if (ack != NULL) {
        ack->sequence = 0u;
        ack->test_code = 0u;
        ack->accepted = 0;
    }
    if (sat == NULL || frame == NULL || ack == NULL || key == NULL ||
            sat->test_only != 1 || length != GS_TEST_TC_FRAME_SIZE ||
            sat->expected_test_scid == 0u ||
            memcmp(frame, magic, sizeof(magic)) != 0 ||
            frame[8] != 1u || frame[10] != 0u || frame[11] != 0u ||
            gs_test_tc_get32(frame + 12u) != sat->expected_test_scid ||
            gs_test_tc_get32(frame + 16u) == 0u ||
            gs_test_hmac_sha256(key, key_length, frame,
                                GS_TEST_TC_HEADER_SIZE, expected) != 0)
        return -1;
    for (index = 0u; index < GS_TEST_TC_TAG_SIZE; ++index)
        difference |= (unsigned char)(expected[index] ^
                                      frame[GS_TEST_TC_HEADER_SIZE + index]);
    for (index = 0u; index < sizeof(expected); ++index) expected[index] = 0u;
    sequence = gs_test_tc_get32(frame + 20u);
    if (difference != 0u || sequence == 0u ||
            sequence <= sat->last_sequence) return -1;
    sat->last_sequence = sequence;
    ack->sequence = sequence;
    ack->test_code = gs_test_tc_get32(frame + 16u);
    ack->accepted = 1;
    return 0;
}
