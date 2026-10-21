#ifndef GS_ROS_SEQUENCE_VERIFY_H
#define GS_ROS_SEQUENCE_VERIFY_H

#include "inputs.h"
#include "conflict.h"

/* Independent ROS test-contract parser and scheduler checks. Never queues TC. */
int gs_ros_verify_test_sequence(const char *data_path, const char *manifest_path,
                                const gs_mps_pass_catalog_t *passes,
                                const gs_mps_command_catalog_t *commands,
                                const gs_mps_test_profile_t *trusted_profile);

#endif
