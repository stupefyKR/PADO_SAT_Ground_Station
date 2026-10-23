#ifndef GS_MPS_TC_SELECTION_H
#define GS_MPS_TC_SELECTION_H

#include "fds_schedule.h"

#include <stddef.h>
#include <stdint.h>

#define GS_MPS_TC_CANDIDATE_SIZE 64u
#define GS_MPS_TC_CANDIDATES_MAX 64u
#define GS_MPS_TC_SELECTION_REVISIONS_MAX 32u

typedef struct {
    uint32_t revision;
    char schedule_sha256[65];
    gs_mps_pass_t pass;
    char candidate_ids[GS_MPS_TC_CANDIDATES_MAX][GS_MPS_TC_CANDIDATE_SIZE];
    size_t candidate_count;
    int non_operational;
} gs_mps_tc_selection_revision_t;

typedef struct {
    gs_mps_tc_selection_revision_t history[GS_MPS_TC_SELECTION_REVISIONS_MAX];
    size_t revision_count;
} gs_mps_tc_selection_store_t;

typedef enum {
    GS_MPS_TC_INSERT = 1,
    GS_MPS_TC_REPLACE = 2,
    GS_MPS_TC_REMOVE = 3
} gs_mps_tc_selection_edit_t;

/* Ordered candidate IDs only: no opcode, parameters, time, approval or transmission. */
int gs_mps_tc_selection_create(gs_mps_tc_selection_store_t *store,
                               const gs_mps_fds_schedule_t *schedule,
                               const char *pass_id);
int gs_mps_tc_selection_latest(const gs_mps_tc_selection_store_t *store,
                               gs_mps_tc_selection_revision_t *out);
int gs_mps_tc_selection_edit(gs_mps_tc_selection_store_t *store,
                             uint32_t expected_revision,
                             const gs_mps_fds_schedule_t *schedule,
                             gs_mps_tc_selection_edit_t action,
                             size_t position, const char *candidate_id,
                             uint32_t *new_revision);

#endif
