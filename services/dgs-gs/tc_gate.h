#ifndef GS_DGS_TC_GATE_H
#define GS_DGS_TC_GATE_H

#include "test_tc_wire.h"

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t expected_test_scid;
    uint32_t expected_db_version;
    uint32_t max_per_pass;
    uint32_t min_spacing_s;
    uint32_t accepted_count;
    uint32_t last_sequence;
    uint64_t last_sent_at_utc_s;
    int test_only;
} gs_test_tc_gate_t;

/* Caller supplies verified ROS transport identity in this test seam. */
int gs_dgs_tc_gate_check_test(gs_test_tc_gate_t *gate,
                              const unsigned char *frame, size_t length,
                              int peer_ros_authenticated,
                              uint64_t now_utc_s);

#endif
