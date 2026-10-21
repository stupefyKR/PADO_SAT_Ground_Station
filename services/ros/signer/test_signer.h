#ifndef GS_ROS_TEST_SIGNER_H
#define GS_ROS_TEST_SIGNER_H

#include "tc_workflow.h"
#include "test_tc_wire.h"

#include <stddef.h>
#include <stdint.h>

/* Key is supplied at runtime to this test seam; no operational key or frame. */
int gs_ros_sign_test_tc(const gs_test_tc_ticket_t *ticket,
                        uint32_t test_scid, uint32_t sequence_number,
                        const unsigned char *key, size_t key_length,
                        unsigned char frame[GS_TEST_TC_FRAME_SIZE]);

#endif
