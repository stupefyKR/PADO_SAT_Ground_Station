#include "tc_gate.h"

#include <string.h>

int gs_dgs_tc_gate_check_test(gs_test_tc_gate_t *gate,
                              const unsigned char *frame, size_t length,
                              int peer_ros_authenticated,
                              uint64_t now_utc_s)
{
    static const unsigned char magic[8] = {'P','A','D','O','T','C','5','!'};
    uint32_t sequence;
    uint64_t sent_at;
    uint64_t aos;
    uint64_t los;
    size_t index;
    unsigned char digest_nonzero = 0u;
    if (gate == NULL || frame == NULL || length != GS_TEST_TC_FRAME_SIZE ||
            gate->test_only != 1 || peer_ros_authenticated != 1 ||
            gate->expected_test_scid == 0u || gate->expected_db_version == 0u ||
            gate->max_per_pass == 0u ||
            memcmp(frame, magic, sizeof(magic)) != 0 ||
            frame[8] != 1u || frame[9] < 1u || frame[9] > 3u ||
            frame[10] != 0u || frame[11] != 0u ||
            gs_test_tc_get32(frame + 12u) != gate->expected_test_scid ||
            gs_test_tc_get32(frame + 16u) == 0u ||
            gs_test_tc_get32(frame + 52u) != gate->expected_db_version)
        return -1;
    sequence = gs_test_tc_get32(frame + 20u);
    sent_at = gs_test_tc_get64(frame + 24u);
    aos = gs_test_tc_get64(frame + 32u);
    los = gs_test_tc_get64(frame + 40u);
    for (index = 56u; index < 88u; ++index)
        digest_nonzero |= frame[index];
    if (digest_nonzero == 0u || sequence == 0u ||
            sequence <= gate->last_sequence ||
            gate->accepted_count >= gate->max_per_pass ||
            aos == 0u || los <= aos || sent_at < aos || sent_at >= los ||
            now_utc_s < aos || now_utc_s >= los ||
            now_utc_s != sent_at ||
            (gate->accepted_count > 0u &&
             (sent_at < gate->last_sent_at_utc_s ||
              sent_at - gate->last_sent_at_utc_s < gate->min_spacing_s)))
        return -1;
    gate->accepted_count++;
    gate->last_sequence = sequence;
    gate->last_sent_at_utc_s = sent_at;
    return 0;
}
