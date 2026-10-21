#include "sequence_export.h"

#include "gs/hash.h"
#include "gs/manifest.h"
#include "cJSON.h"

#include <limits.h>
#include <stdio.h>
#include <string.h>

static int copy_actor(char destination[GS_MPS_ACTOR_SIZE], const char *actor)
{
    size_t index;
    if (actor == NULL) return -1;
    for (index = 0u; index < GS_MPS_ACTOR_SIZE; ++index) {
        const unsigned char c = (unsigned char)actor[index];
        if (c == 0u) return index == 0u ? -1 : 0;
        if (c < 33u || c > 126u) return -1;
        destination[index] = actor[index];
    }
    return -1;
}

static int same_pass(const gs_mps_pass_t *left, const gs_mps_pass_t *right)
{
    return strcmp(left->pass_id, right->pass_id) == 0 &&
           left->aos_utc_s == right->aos_utc_s &&
           left->los_utc_s == right->los_utc_s &&
           left->guard_before_s == right->guard_before_s &&
           left->guard_after_s == right->guard_after_s;
}

static int find_current_pass(const gs_mps_pass_catalog_t *passes,
                             const gs_mps_pass_t *selected)
{
    size_t index;
    if (passes == NULL || passes->count > GS_MPS_MAX_PASSES) return -1;
    for (index = 0u; index < passes->count; ++index)
        if (same_pass(&passes->passes[index], selected)) return 0;
    return -1;
}

int gs_mps_sequence_approve_test(const gs_mps_plan_store_t *store,
                                 uint32_t expected_revision,
                                 const gs_mps_pass_catalog_t *passes,
                                 const gs_mps_command_catalog_t *commands,
                                 const gs_mps_test_profile_t *profile,
                                 const char *approver, int lead_role,
                                 int reauthenticated, uint32_t approved_at_utc_s,
                                 gs_mps_test_approval_t *out)
{
    gs_mps_plan_revision_t plan;
    gs_mps_analysis_t analysis;
    gs_mps_test_approval_t approved = {0};
    size_t index;
    if (out == NULL || commands == NULL || profile == NULL ||
            lead_role != 1 || reauthenticated != 1 ||
            approved_at_utc_s == 0u ||
            copy_actor(approved.approved_by, approver) != 0 ||
            gs_mps_plan_latest(store, &plan) != 0 ||
            plan.revision != expected_revision || plan.non_operational != 1 ||
            plan.step_count == 0u || commands->operational != 0 ||
            commands->command_count != 0u ||
            commands->version != plan.command_db_version ||
            find_current_pass(passes, &plan.pass) != 0 ||
            strcmp(plan.edited_by, approved.approved_by) == 0 ||
            gs_mps_analyze_test_plan(&plan, profile, &analysis) != 0 ||
            analysis.violations != 0u) return -1;
    /* JSON numbers in the test contract must round-trip exactly. */
    if (plan.pass.aos_utc_s > 9007199254740991LL ||
            plan.pass.los_utc_s > 9007199254740991LL) return -1;
    for (index = 0u; index < plan.step_count; ++index)
        if (plan.steps[index].uplink_bytes > UINT_MAX) return -1;
    approved.plan = plan;
    approved.profile = *profile;
    approved.approved_at_utc_s = approved_at_utc_s;
    approved.test_only = 1;
    *out = approved;
    return 0;
}

static int add_number(cJSON *object, const char *name, double value)
{
    return cJSON_AddNumberToObject(object, name, value) == NULL ? -1 : 0;
}

static int add_string(cJSON *object, const char *name, const char *value)
{
    return cJSON_AddStringToObject(object, name, value) == NULL ? -1 : 0;
}

static int add_step(cJSON *array, const gs_mps_step_t *step)
{
    cJSON *item = cJSON_CreateObject();
    if (item == NULL) return -1;
    if (add_number(item, "step_id", step->step_id) != 0 ||
            add_string(item, "command_id", step->command_id) != 0 ||
            add_number(item, "offset_s", step->offset_s) != 0 ||
            add_number(item, "duration_s", step->duration_s) != 0 ||
            add_number(item, "resource_mask", step->resource_mask) != 0 ||
            add_number(item, "require_set", step->require_set) != 0 ||
            add_number(item, "require_clear", step->require_clear) != 0 ||
            add_number(item, "effect_set", step->effect_set) != 0 ||
            add_number(item, "effect_clear", step->effect_clear) != 0 ||
            add_number(item, "uplink_bytes", (double)step->uplink_bytes) != 0) {
        cJSON_Delete(item);
        return -1;
    }
    if (!cJSON_AddItemToArray(array, item)) {
        cJSON_Delete(item);
        return -1;
    }
    return 0;
}

static int build_sequence(const gs_mps_test_approval_t *approval,
                          char *sequence, size_t capacity)
{
    const gs_mps_plan_revision_t *plan = &approval->plan;
    cJSON *root = cJSON_CreateObject();
    cJSON *steps = cJSON_CreateArray();
    char *printed;
    size_t length;
    size_t index;
    if (root == NULL || steps == NULL) {
        cJSON_Delete(root);
        cJSON_Delete(steps);
        return -1;
    }
    if (add_string(root, "type", "TEST_ONLY_SEQUENCE") != 0 ||
            cJSON_AddTrueToObject(root, "non_operational") == NULL ||
            add_number(root, "revision", plan->revision) != 0 ||
            add_number(root, "command_db_version", plan->command_db_version) != 0 ||
            add_string(root, "pass_id", plan->pass.pass_id) != 0 ||
            add_number(root, "aos_utc_s", (double)plan->pass.aos_utc_s) != 0 ||
            add_number(root, "los_utc_s", (double)plan->pass.los_utc_s) != 0 ||
            add_number(root, "guard_before_s", plan->pass.guard_before_s) != 0 ||
            add_number(root, "guard_after_s", plan->pass.guard_after_s) != 0 ||
            add_number(root, "initial_state", approval->profile.initial_state) != 0 ||
            add_number(root, "transfer_rate_bps", approval->profile.transfer_rate_bps) != 0 ||
            add_number(root, "max_commands", approval->profile.max_commands) != 0 ||
            add_string(root, "edited_by", plan->edited_by) != 0 ||
            add_string(root, "approved_by", approval->approved_by) != 0 ||
            add_number(root, "approved_at_utc_s", approval->approved_at_utc_s) != 0) goto invalid;
    for (index = 0u; index < plan->step_count; ++index)
        if (add_step(steps, &plan->steps[index]) != 0) goto invalid;
    if (!cJSON_AddItemToObject(root, "steps", steps)) goto invalid;
    steps = NULL;
    printed = cJSON_PrintUnformatted(root);
    if (printed == NULL) goto invalid;
    length = strlen(printed);
    if (length >= capacity || length > GS_MPS_TEST_SEQUENCE_MAX_BYTES) {
        cJSON_free(printed);
        goto invalid;
    }
    for (index = 0u; index <= length; ++index) sequence[index] = printed[index];
    cJSON_free(printed);
    cJSON_Delete(root);
    return 0;
invalid:
    cJSON_Delete(steps);
    cJSON_Delete(root);
    return -1;
}

int gs_mps_sequence_export_test(const gs_mps_plan_store_t *store,
                                const gs_mps_pass_catalog_t *passes,
                                const gs_mps_command_catalog_t *commands,
                                const gs_mps_test_approval_t *approval,
                                const char *filename, const char *created_utc,
                                char *sequence, size_t sequence_capacity,
                                char *manifest, size_t manifest_capacity)
{
    char digest[GS_SHA256_HEX_SIZE];
    char checked_approver[GS_MPS_ACTOR_SIZE] = {0};
    gs_manifest_t parsed;
    gs_mps_plan_revision_t latest;
    gs_mps_analysis_t analysis;
    int written;
    if (approval == NULL || commands == NULL || filename == NULL || created_utc == NULL ||
            sequence == NULL || manifest == NULL || approval->test_only != 1 ||
            approval->plan.non_operational != 1 ||
            approval->profile.test_only != 1 ||
            approval->plan.step_count == 0u ||
            approval->plan.step_count > GS_MPS_MAX_STEPS ||
            approval->approved_at_utc_s == 0u ||
            gs_mps_plan_latest(store, &latest) != 0 ||
            latest.revision != approval->plan.revision ||
            memcmp(&latest, &approval->plan, sizeof(latest)) != 0 ||
            copy_actor(checked_approver, approval->approved_by) != 0 ||
            strcmp(checked_approver, latest.edited_by) == 0 ||
            commands->operational != 0 || commands->command_count != 0u ||
            commands->version != latest.command_db_version ||
            find_current_pass(passes, &latest.pass) != 0 ||
            gs_mps_analyze_test_plan(&latest, &approval->profile, &analysis) != 0 ||
            analysis.violations != 0u ||
            sequence_capacity == 0u || manifest_capacity == 0u ||
            strncmp(filename, "TEST_ONLY_", 10u) != 0 ||
            build_sequence(approval, sequence, sequence_capacity) != 0 ||
            gs_sha256_bytes((const unsigned char *)sequence, strlen(sequence),
                            digest) != 0) return -1;
    written = snprintf(manifest, manifest_capacity,
        "{\"file\":\"%s\",\"type\":\"TEST_ONLY_SEQUENCE\","
        "\"from\":\"mps\",\"to\":\"ros\",\"version\":%u,"
        "\"created\":\"%s\",\"sha256\":\"%s\"}",
        filename, approval->plan.revision, created_utc, digest);
    if (written < 0 || (size_t)written >= manifest_capacity ||
            gs_manifest_parse(manifest, (size_t)written, &parsed) != 0) return -1;
    return 0;
}
