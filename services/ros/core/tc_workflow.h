#ifndef GS_ROS_TC_WORKFLOW_H
#define GS_ROS_TC_WORKFLOW_H

#include "sequence_verify.h"

#include <stdint.h>

#define GS_TEST_TC_MAX_COMMANDS 16u

typedef enum {
    GS_TEST_TC_NORMAL = 1,
    GS_TEST_TC_CAUTION = 2,
    GS_TEST_TC_DANGEROUS = 3
} gs_test_tc_risk_t;

typedef enum {
    GS_TEST_TC_OPERATOR = 1,
    GS_TEST_TC_PEER = 2,
    GS_TEST_TC_LEAD = 3
} gs_test_tc_role_t;

typedef struct {
    char command_id[GS_MPS_COMMAND_ID_SIZE];
    uint32_t test_code;
    int32_t min_parameter;
    int32_t max_parameter;
    uint32_t require_set;
    uint32_t require_clear;
    gs_test_tc_risk_t risk;
} gs_test_tc_command_t;

typedef struct {
    unsigned int command_db_version;
    size_t count;
    gs_test_tc_command_t commands[GS_TEST_TC_MAX_COMMANDS];
    int test_only;
} gs_test_tc_catalog_t;

typedef struct {
    uint32_t step_id;
    char command_id[GS_MPS_COMMAND_ID_SIZE];
    int32_t parameter;
    uint32_t recent_state;
    uint32_t command_db_version;
    uint64_t send_at_utc_s;
    char author[GS_MPS_ACTOR_SIZE];
} gs_test_tc_request_t;

typedef struct {
    char actor[GS_MPS_ACTOR_SIZE];
    gs_test_tc_role_t role;
    int reauthenticated;
} gs_test_tc_identity_t;

typedef struct {
    uint32_t test_code;
    uint32_t command_db_version;
    int32_t parameter;
    uint64_t send_at_utc_s;
    uint64_t aos_utc_s;
    uint64_t los_utc_s;
    gs_test_tc_risk_t risk;
    char author[GS_MPS_ACTOR_SIZE];
    char approved_by[GS_MPS_ACTOR_SIZE];
    gs_test_tc_role_t approved_role;
    int approval_reauthenticated;
    uint64_t approved_at_utc_s;
    unsigned char sequence_digest[32];
    int validated;
    int approved;
    int test_only;
} gs_test_tc_ticket_t;

/* Test-only inputs stand in for the pending TC ICD, live TM, and Portal identity. */
int gs_ros_tc_validate_test(const char *sequence_path, const char *manifest_path,
                            const gs_mps_pass_catalog_t *passes,
                            const gs_mps_command_catalog_t *mps_commands,
                            const gs_mps_test_profile_t *profile,
                            const gs_test_tc_catalog_t *tc_catalog,
                            const gs_test_tc_request_t *request,
                            gs_test_tc_ticket_t *ticket);
int gs_ros_tc_approve_test(gs_test_tc_ticket_t *ticket,
                           const gs_test_tc_identity_t *identity,
                           uint64_t approved_at_utc_s);

#endif
