#include "tc_selection.h"

#include <string.h>

static int candidate(const char *text)
{
    size_t index;
    if (text == NULL) return 0;
    for (index = 0u; index < GS_MPS_TC_CANDIDATE_SIZE; ++index) {
        char value = text[index];
        if (value == '\0') return index > 0u;
        if ((value < 'A' || value > 'Z') && (value < '0' || value > '9') &&
                value != '_' && value != '-') return 0;
    }
    return 0;
}

static void copy_candidate(char destination[GS_MPS_TC_CANDIDATE_SIZE],
                           const char *source)
{
    size_t index;
    for (index = 0u; index < GS_MPS_TC_CANDIDATE_SIZE; ++index) {
        destination[index] = source[index];
        if (source[index] == '\0') break;
    }
    for (; index < GS_MPS_TC_CANDIDATE_SIZE; ++index)
        destination[index] = '\0';
}

static const gs_mps_pass_t *find_pass(const gs_mps_fds_schedule_t *schedule,
                                      const char *pass_id)
{
    size_t index;
    if (schedule == NULL || schedule->non_operational != 1 || pass_id == NULL ||
            schedule->passes.count > GS_MPS_MAX_PASSES) return NULL;
    for (index = 0u; index < schedule->passes.count; ++index) {
        if (strcmp(schedule->passes.passes[index].pass_id, pass_id) == 0)
            return &schedule->passes.passes[index];
    }
    return NULL;
}

int gs_mps_tc_selection_create(gs_mps_tc_selection_store_t *store,
                               const gs_mps_fds_schedule_t *schedule,
                               const char *pass_id)
{
    const gs_mps_pass_t *pass = find_pass(schedule, pass_id);
    gs_mps_tc_selection_revision_t *first;
    size_t index;
    if (store == NULL || store->revision_count != 0u || pass == NULL) return -1;
    first = &store->history[0];
    *first = (gs_mps_tc_selection_revision_t){0};
    first->revision = 1u;
    first->pass = *pass;
    for (index = 0u; index < sizeof(first->schedule_sha256); ++index)
        first->schedule_sha256[index] = schedule->sha256[index];
    first->non_operational = 1;
    store->revision_count = 1u;
    return 0;
}

int gs_mps_tc_selection_latest(const gs_mps_tc_selection_store_t *store,
                               gs_mps_tc_selection_revision_t *out)
{
    if (store == NULL || out == NULL || store->revision_count == 0u ||
            store->revision_count > GS_MPS_TC_SELECTION_REVISIONS_MAX) return -1;
    *out = store->history[store->revision_count - 1u];
    return 0;
}

int gs_mps_tc_selection_edit(gs_mps_tc_selection_store_t *store,
                             uint32_t expected_revision,
                             const gs_mps_fds_schedule_t *schedule,
                             gs_mps_tc_selection_edit_t action,
                             size_t position, const char *candidate_id,
                             uint32_t *new_revision)
{
    gs_mps_tc_selection_revision_t next;
    const gs_mps_pass_t *pass;
    size_t index;
    if (store == NULL || new_revision == NULL || store->revision_count == 0u ||
            store->revision_count >= GS_MPS_TC_SELECTION_REVISIONS_MAX ||
            expected_revision != store->revision_count) return -1;
    next = store->history[store->revision_count - 1u];
    pass = find_pass(schedule, next.pass.pass_id);
    if (next.non_operational != 1 || pass == NULL ||
            strcmp(next.schedule_sha256, schedule->sha256) != 0 ||
            pass->aos_utc_s != next.pass.aos_utc_s ||
            pass->los_utc_s != next.pass.los_utc_s ||
            next.candidate_count > GS_MPS_TC_CANDIDATES_MAX) return -1;
    if (action == GS_MPS_TC_INSERT) {
        if (position > next.candidate_count ||
                next.candidate_count == GS_MPS_TC_CANDIDATES_MAX ||
                !candidate(candidate_id)) return -1;
        for (index = next.candidate_count; index > position; --index)
            copy_candidate(next.candidate_ids[index], next.candidate_ids[index - 1u]);
        copy_candidate(next.candidate_ids[position], candidate_id);
        ++next.candidate_count;
    } else if (action == GS_MPS_TC_REPLACE) {
        if (position >= next.candidate_count || !candidate(candidate_id)) return -1;
        copy_candidate(next.candidate_ids[position], candidate_id);
    } else if (action == GS_MPS_TC_REMOVE) {
        if (position >= next.candidate_count || candidate_id != NULL) return -1;
        for (index = position; index + 1u < next.candidate_count; ++index)
            copy_candidate(next.candidate_ids[index], next.candidate_ids[index + 1u]);
        --next.candidate_count;
        for (index = 0u; index < GS_MPS_TC_CANDIDATE_SIZE; ++index)
            next.candidate_ids[next.candidate_count][index] = '\0';
    } else return -1;
    next.revision = (uint32_t)store->revision_count + 1u;
    store->history[store->revision_count] = next;
    ++store->revision_count;
    *new_revision = next.revision;
    return 0;
}
