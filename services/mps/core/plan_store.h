#ifndef GS_MPS_PLAN_STORE_H
#define GS_MPS_PLAN_STORE_H

#include "inputs.h"

#include <stddef.h>
#include <stdint.h>

#define GS_MPS_MAX_STEPS 64u
#define GS_MPS_MAX_REVISIONS 32u
#define GS_MPS_COMMAND_ID_SIZE 64u
#define GS_MPS_ACTOR_SIZE 64u

/* Command properties are a test-only contract until the TC ICD is approved. */
typedef struct {
    uint32_t step_id;
    char command_id[GS_MPS_COMMAND_ID_SIZE];
    uint32_t offset_s;
    uint32_t duration_s;
    uint32_t resource_mask;
    uint32_t require_set;
    uint32_t require_clear;
    uint32_t effect_set;
    uint32_t effect_clear;
    uint64_t uplink_bytes;
} gs_mps_step_t;

typedef struct {
    uint32_t revision;
    unsigned int command_db_version;
    gs_mps_pass_t pass;
    char edited_by[GS_MPS_ACTOR_SIZE];
    gs_mps_step_t steps[GS_MPS_MAX_STEPS];
    size_t step_count;
    int non_operational;
} gs_mps_plan_revision_t;

/* One bounded plan and its immutable revision history. No operational TC path. */
typedef struct {
    gs_mps_plan_revision_t history[GS_MPS_MAX_REVISIONS];
    size_t revision_count;
} gs_mps_plan_store_t;

typedef enum {
    GS_MPS_EDIT_ADD = 1,
    GS_MPS_EDIT_REPLACE = 2,
    GS_MPS_EDIT_REMOVE = 3
} gs_mps_edit_kind_t;

typedef struct {
    gs_mps_edit_kind_t kind;
    uint32_t step_id;
    gs_mps_step_t step;
} gs_mps_edit_t;

int gs_mps_plan_create(gs_mps_plan_store_t *store,
                       const gs_mps_pass_catalog_t *passes, const char *pass_id,
                       const gs_mps_command_catalog_t *commands, const char *actor);
int gs_mps_plan_latest(const gs_mps_plan_store_t *store,
                       gs_mps_plan_revision_t *out);
int gs_mps_plan_revision(const gs_mps_plan_store_t *store, uint32_t revision,
                         gs_mps_plan_revision_t *out);
/* Returns -1 for invalid input, stale revision, changed pass/DB, or capacity. */
int gs_mps_plan_edit(gs_mps_plan_store_t *store, uint32_t expected_revision,
                     const gs_mps_pass_catalog_t *passes,
                     const gs_mps_command_catalog_t *commands,
                     const gs_mps_edit_t *edit, const char *actor,
                     uint32_t *new_revision);

#endif
