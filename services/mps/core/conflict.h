#ifndef GS_MPS_CONFLICT_H
#define GS_MPS_CONFLICT_H

#include "plan_store.h"

#include <stdint.h>

#define GS_MPS_CONFLICT_WINDOW 1u
#define GS_MPS_CONFLICT_OVERLAP 2u
#define GS_MPS_CONFLICT_RESOURCE 4u
#define GS_MPS_CONFLICT_PRECONDITION 8u
#define GS_MPS_CONFLICT_DATA 16u
#define GS_MPS_CONFLICT_COUNT 32u

/* All values are synthetic test inputs; no operational clearance is produced. */
typedef struct {
    uint32_t initial_state;
    uint32_t transfer_rate_bps;
    uint32_t max_commands;
    int test_only;
} gs_mps_test_profile_t;

typedef struct {
    uint32_t violations;
    int non_operational;
} gs_mps_analysis_t;

/* Each rule returns 0 on pass, 1 on violation, -1 on invalid input. */
int gs_mps_check_time_window(const gs_mps_plan_revision_t *plan);
int gs_mps_check_overlap(const gs_mps_plan_revision_t *plan);
int gs_mps_check_resource(const gs_mps_plan_revision_t *plan);
int gs_mps_check_precondition(const gs_mps_plan_revision_t *plan,
                              uint32_t initial_state);
int gs_mps_check_data_volume(const gs_mps_plan_revision_t *plan,
                              uint32_t transfer_rate_bps);
int gs_mps_check_command_count(const gs_mps_plan_revision_t *plan,
                                uint32_t max_commands);
int gs_mps_analyze_test_plan(const gs_mps_plan_revision_t *plan,
                             const gs_mps_test_profile_t *profile,
                             gs_mps_analysis_t *out);

#endif
