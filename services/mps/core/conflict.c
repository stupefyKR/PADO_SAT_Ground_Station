#include "conflict.h"

#include <limits.h>

static int valid_plan(const gs_mps_plan_revision_t *plan)
{
    return plan != NULL && plan->non_operational == 1 &&
           plan->step_count <= GS_MPS_MAX_STEPS &&
           plan->pass.aos_utc_s > 0 &&
           plan->pass.los_utc_s > plan->pass.aos_utc_s &&
           (uint64_t)plan->pass.guard_before_s + plan->pass.guard_after_s <
               (uint64_t)(plan->pass.los_utc_s - plan->pass.aos_utc_s);
}

static uint64_t step_end(const gs_mps_step_t *step)
{
    return (uint64_t)step->offset_s + step->duration_s;
}

static int overlap(const gs_mps_step_t *left, const gs_mps_step_t *right)
{
    return (uint64_t)left->offset_s < step_end(right) &&
           (uint64_t)right->offset_s < step_end(left);
}

int gs_mps_check_time_window(const gs_mps_plan_revision_t *plan)
{
    size_t index;
    uint64_t usable_end;
    if (!valid_plan(plan)) return -1;
    usable_end = (uint64_t)(plan->pass.los_utc_s - plan->pass.aos_utc_s) -
                 plan->pass.guard_after_s;
    for (index = 0u; index < plan->step_count; ++index) {
        const gs_mps_step_t *step = &plan->steps[index];
        if (step->duration_s == 0u ||
                step->offset_s < plan->pass.guard_before_s ||
                step_end(step) > usable_end) return 1;
    }
    return 0;
}

int gs_mps_check_overlap(const gs_mps_plan_revision_t *plan)
{
    size_t left;
    size_t right;
    if (!valid_plan(plan)) return -1;
    for (left = 0u; left < plan->step_count; ++left)
        for (right = left + 1u; right < plan->step_count; ++right)
            if (overlap(&plan->steps[left], &plan->steps[right])) return 1;
    return 0;
}

int gs_mps_check_resource(const gs_mps_plan_revision_t *plan)
{
    size_t left;
    size_t right;
    if (!valid_plan(plan)) return -1;
    for (left = 0u; left < plan->step_count; ++left)
        for (right = left + 1u; right < plan->step_count; ++right)
            if ((plan->steps[left].resource_mask &
                 plan->steps[right].resource_mask) != 0u &&
                    overlap(&plan->steps[left], &plan->steps[right])) return 1;
    return 0;
}

int gs_mps_check_precondition(const gs_mps_plan_revision_t *plan,
                              uint32_t initial_state)
{
    uint32_t state = initial_state;
    size_t index;
    if (!valid_plan(plan)) return -1;
    for (index = 0u; index < plan->step_count; ++index) {
        const gs_mps_step_t *step = &plan->steps[index];
        if ((state & step->require_set) != step->require_set ||
                (state & step->require_clear) != 0u ||
                (step->require_set & step->require_clear) != 0u ||
                (step->effect_set & step->effect_clear) != 0u) return 1;
        state = (state & ~step->effect_clear) | step->effect_set;
    }
    return 0;
}

int gs_mps_check_data_volume(const gs_mps_plan_revision_t *plan,
                              uint32_t transfer_rate_bps)
{
    uint64_t bytes = 0u;
    uint64_t usable_seconds;
    uint64_t whole_bytes;
    uint64_t fractional_bytes;
    size_t index;
    if (!valid_plan(plan) || transfer_rate_bps == 0u) return -1;
    for (index = 0u; index < plan->step_count; ++index) {
        if (plan->steps[index].uplink_bytes > UINT64_MAX - bytes) return 1;
        bytes += plan->steps[index].uplink_bytes;
    }
    usable_seconds = (uint64_t)(plan->pass.los_utc_s - plan->pass.aos_utc_s) -
                     plan->pass.guard_before_s - plan->pass.guard_after_s;
    if (usable_seconds / 8u > UINT64_MAX / transfer_rate_bps) return 0;
    whole_bytes = (usable_seconds / 8u) * transfer_rate_bps;
    fractional_bytes = ((usable_seconds % 8u) * transfer_rate_bps) / 8u;
    if (fractional_bytes > UINT64_MAX - whole_bytes) return 0;
    return bytes > whole_bytes + fractional_bytes ? 1 : 0;
}

int gs_mps_check_command_count(const gs_mps_plan_revision_t *plan,
                                uint32_t max_commands)
{
    if (!valid_plan(plan) || max_commands == 0u ||
            max_commands > GS_MPS_MAX_STEPS) return -1;
    return plan->step_count > max_commands ? 1 : 0;
}

int gs_mps_analyze_test_plan(const gs_mps_plan_revision_t *plan,
                             const gs_mps_test_profile_t *profile,
                             gs_mps_analysis_t *out)
{
    int results[6];
    uint32_t violations = 0u;
    static const uint32_t masks[6] = {
        GS_MPS_CONFLICT_WINDOW, GS_MPS_CONFLICT_OVERLAP,
        GS_MPS_CONFLICT_RESOURCE, GS_MPS_CONFLICT_PRECONDITION,
        GS_MPS_CONFLICT_DATA, GS_MPS_CONFLICT_COUNT
    };
    size_t index;
    if (!valid_plan(plan) || profile == NULL || out == NULL ||
            profile->test_only != 1) return -1;
    results[0] = gs_mps_check_time_window(plan);
    results[1] = gs_mps_check_overlap(plan);
    results[2] = gs_mps_check_resource(plan);
    results[3] = gs_mps_check_precondition(plan, profile->initial_state);
    results[4] = gs_mps_check_data_volume(plan, profile->transfer_rate_bps);
    results[5] = gs_mps_check_command_count(plan, profile->max_commands);
    for (index = 0u; index < 6u; ++index) {
        if (results[index] < 0) return -1;
        if (results[index] != 0) violations |= masks[index];
    }
    out->violations = violations;
    out->non_operational = 1;
    return 0;
}
