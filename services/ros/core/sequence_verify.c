#include "sequence_verify.h"

#include "gs/exchange.h"
#include "gs/manifest.h"
#include "cJSON.h"

#include <limits.h>
#include <stdio.h>
#include <string.h>

#define TEST_SEQUENCE_MAX_BYTES 32768u

static int exact_keys(const cJSON *object, const char *const *keys, size_t count)
{
    const cJSON *item;
    size_t found = 0u;
    size_t index;
    if (!cJSON_IsObject(object)) return 0;
    for (item = object->child; item != NULL; item = item->next) {
        if (item->string == NULL) return 0;
        for (index = 0u; index < count; ++index)
            if (strcmp(item->string, keys[index]) == 0) break;
        if (index == count) return 0;
        for (const cJSON *other = object->child; other != item; other = other->next)
            if (strcmp(other->string, item->string) == 0) return 0;
        ++found;
    }
    return found == count;
}

static int uint_value(const cJSON *object, const char *key, uint32_t *out)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
    if (!cJSON_IsNumber(item) || item->valuedouble < 0.0 ||
            item->valuedouble > (double)UINT32_MAX ||
            item->valuedouble != (double)(uint32_t)item->valuedouble) return -1;
    *out = (uint32_t)item->valuedouble;
    return 0;
}

static int time_value(const cJSON *object, const char *key, int64_t *out)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
    if (!cJSON_IsNumber(item) || item->valuedouble < 1.0 ||
            item->valuedouble > 9007199254740991.0 ||
            item->valuedouble != (double)(int64_t)item->valuedouble) return -1;
    *out = (int64_t)item->valuedouble;
    return 0;
}

static int string_value(const cJSON *object, const char *key,
                        char *out, size_t capacity)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
    size_t index;
    if (!cJSON_IsString(item) || item->valuestring == NULL) return -1;
    for (index = 0u; index < capacity; ++index) {
        out[index] = item->valuestring[index];
        if (out[index] == '\0') return index == 0u ? -1 : 0;
    }
    return -1;
}

static int parse_step(const cJSON *object, gs_mps_step_t *step)
{
    static const char *const keys[] = {
        "step_id", "command_id", "offset_s", "duration_s", "resource_mask",
        "require_set", "require_clear", "effect_set", "effect_clear", "uplink_bytes"
    };
    uint32_t bytes;
    if (!exact_keys(object, keys, 10u) ||
            uint_value(object, "step_id", &step->step_id) != 0 ||
            string_value(object, "command_id", step->command_id,
                         sizeof(step->command_id)) != 0 ||
            uint_value(object, "offset_s", &step->offset_s) != 0 ||
            uint_value(object, "duration_s", &step->duration_s) != 0 ||
            uint_value(object, "resource_mask", &step->resource_mask) != 0 ||
            uint_value(object, "require_set", &step->require_set) != 0 ||
            uint_value(object, "require_clear", &step->require_clear) != 0 ||
            uint_value(object, "effect_set", &step->effect_set) != 0 ||
            uint_value(object, "effect_clear", &step->effect_clear) != 0 ||
            uint_value(object, "uplink_bytes", &bytes) != 0) return -1;
    step->uplink_bytes = bytes;
    return 0;
}

static int load_plan(const char *json, size_t length,
                     gs_mps_plan_revision_t *plan,
                     gs_mps_test_profile_t *profile,
                     char approver[GS_MPS_ACTOR_SIZE], uint32_t *approved_at)
{
    static const char *const keys[] = {
        "type", "non_operational", "revision", "command_db_version", "pass_id",
        "aos_utc_s", "los_utc_s", "guard_before_s", "guard_after_s",
        "initial_state", "transfer_rate_bps", "max_commands", "edited_by",
        "approved_by", "approved_at_utc_s", "steps"
    };
    const cJSON *steps;
    const cJSON *type;
    const cJSON *marker;
    cJSON *root;
    size_t index;
    if (length == 0u || length > TEST_SEQUENCE_MAX_BYTES ||
            memchr(json, '\0', length) != NULL ||
            strstr(json, "\\u0000") != NULL) return -1;
    root = cJSON_ParseWithLengthOpts(json, length + 1u, NULL, 1);
    if (!exact_keys(root, keys, 16u)) goto invalid;
    type = cJSON_GetObjectItemCaseSensitive(root, "type");
    marker = cJSON_GetObjectItemCaseSensitive(root, "non_operational");
    steps = cJSON_GetObjectItemCaseSensitive(root, "steps");
    if (!cJSON_IsString(type) || strcmp(type->valuestring,
                                        "TEST_ONLY_SEQUENCE") != 0 ||
            !cJSON_IsTrue(marker) || !cJSON_IsArray(steps) ||
            cJSON_GetArraySize(steps) <= 0 ||
            cJSON_GetArraySize(steps) > (int)GS_MPS_MAX_STEPS ||
            uint_value(root, "revision", &plan->revision) != 0 ||
            uint_value(root, "command_db_version", &plan->command_db_version) != 0 ||
            string_value(root, "pass_id", plan->pass.pass_id,
                         sizeof(plan->pass.pass_id)) != 0 ||
            time_value(root, "aos_utc_s", &plan->pass.aos_utc_s) != 0 ||
            time_value(root, "los_utc_s", &plan->pass.los_utc_s) != 0 ||
            uint_value(root, "guard_before_s", &plan->pass.guard_before_s) != 0 ||
            uint_value(root, "guard_after_s", &plan->pass.guard_after_s) != 0 ||
            uint_value(root, "initial_state", &profile->initial_state) != 0 ||
            uint_value(root, "transfer_rate_bps", &profile->transfer_rate_bps) != 0 ||
            uint_value(root, "max_commands", &profile->max_commands) != 0 ||
            string_value(root, "edited_by", plan->edited_by,
                         sizeof(plan->edited_by)) != 0 ||
            string_value(root, "approved_by", approver,
                         GS_MPS_ACTOR_SIZE) != 0 ||
            uint_value(root, "approved_at_utc_s", approved_at) != 0) goto invalid;
    plan->step_count = (size_t)cJSON_GetArraySize(steps);
    for (index = 0u; index < plan->step_count; ++index)
        if (parse_step(cJSON_GetArrayItem(steps, (int)index),
                       &plan->steps[index]) != 0) goto invalid;
    plan->non_operational = 1;
    profile->test_only = 1;
    cJSON_Delete(root);
    return 0;
invalid:
    cJSON_Delete(root);
    return -1;
}

static int verify_semantics(const gs_mps_plan_revision_t *plan,
                            const gs_mps_test_profile_t *profile,
                            const char *approver, uint32_t approved_at,
                            const gs_mps_pass_catalog_t *passes,
                            const gs_mps_command_catalog_t *commands,
                            const gs_mps_test_profile_t *trusted)
{
    uint64_t usable;
    uint64_t bytes = 0u;
    uint32_t state;
    size_t index;
    int pass_found = 0;
    if (passes == NULL || commands == NULL || trusted == NULL ||
            trusted->test_only != 1 || passes->count > GS_MPS_MAX_PASSES ||
            commands->operational != 0 || commands->command_count != 0u ||
            commands->version != plan->command_db_version ||
            plan->revision == 0u || approved_at == 0u ||
            strcmp(approver, plan->edited_by) == 0 ||
            profile->initial_state != trusted->initial_state ||
            profile->transfer_rate_bps != trusted->transfer_rate_bps ||
            profile->max_commands != trusted->max_commands ||
            profile->transfer_rate_bps == 0u || profile->max_commands == 0u ||
            plan->step_count > profile->max_commands) return -1;
    for (index = 0u; index < passes->count; ++index) {
        const gs_mps_pass_t *item = &passes->passes[index];
        if (strcmp(item->pass_id, plan->pass.pass_id) == 0 &&
                item->aos_utc_s == plan->pass.aos_utc_s &&
                item->los_utc_s == plan->pass.los_utc_s &&
                item->guard_before_s == plan->pass.guard_before_s &&
                item->guard_after_s == plan->pass.guard_after_s) pass_found = 1;
    }
    if (!pass_found || plan->pass.los_utc_s <= plan->pass.aos_utc_s ||
            (uint64_t)(plan->pass.los_utc_s - plan->pass.aos_utc_s) <=
                (uint64_t)plan->pass.guard_before_s +
                plan->pass.guard_after_s) return -1;
    usable = (uint64_t)(plan->pass.los_utc_s - plan->pass.aos_utc_s) -
             plan->pass.guard_before_s - plan->pass.guard_after_s;
    state = profile->initial_state;
    for (index = 0u; index < plan->step_count; ++index) {
        const gs_mps_step_t *item = &plan->steps[index];
        uint64_t end = (uint64_t)item->offset_s + item->duration_s;
        size_t other;
        if (item->step_id == 0u || strncmp(item->command_id, "TEST_", 5u) != 0 ||
                item->duration_s == 0u ||
                item->offset_s < plan->pass.guard_before_s ||
                end > (uint64_t)(plan->pass.los_utc_s - plan->pass.aos_utc_s) -
                      plan->pass.guard_after_s ||
                (state & item->require_set) != item->require_set ||
                (state & item->require_clear) != 0u ||
                (item->require_set & item->require_clear) != 0u ||
                (item->effect_set & item->effect_clear) != 0u ||
                item->uplink_bytes > UINT64_MAX - bytes) return -1;
        for (other = 0u; other < index; ++other) {
            const gs_mps_step_t *previous = &plan->steps[other];
            if (previous->step_id == item->step_id ||
                    ((uint64_t)previous->offset_s < end &&
                     (uint64_t)item->offset_s <
                         (uint64_t)previous->offset_s + previous->duration_s)) return -1;
        }
        bytes += item->uplink_bytes;
        state = (state & ~item->effect_clear) | item->effect_set;
    }
    if (usable / 8u <= UINT64_MAX / profile->transfer_rate_bps) {
        uint64_t capacity = (usable / 8u) * profile->transfer_rate_bps;
        uint64_t rest = ((usable % 8u) * profile->transfer_rate_bps) / 8u;
        if (rest <= UINT64_MAX - capacity && bytes > capacity + rest) return -1;
    }
    return 0;
}

int gs_ros_verify_test_sequence(const char *data_path, const char *manifest_path,
                                const gs_mps_pass_catalog_t *passes,
                                const gs_mps_command_catalog_t *commands,
                                const gs_mps_test_profile_t *trusted_profile)
{
    static const char *const allowed[] = {"TEST_ONLY_SEQUENCE"};
    char json[TEST_SEQUENCE_MAX_BYTES + 1u];
    char manifest_json[GS_MANIFEST_MAX_BYTES + 1u];
    gs_manifest_t manifest;
    gs_mps_plan_revision_t plan = {0};
    gs_mps_test_profile_t profile = {0};
    char approver[GS_MPS_ACTOR_SIZE] = {0};
    uint32_t approved_at = 0u;
    FILE *file;
    size_t length;
    if (data_path == NULL || manifest_path == NULL ||
            gs_exchange_verify_pair(data_path, manifest_path, "mps", "ros",
                                    allowed, 1u) != 0) return -1;
#ifdef _WIN32
    if (fopen_s(&file, manifest_path, "rb") != 0) return -1;
#else
    file = fopen(manifest_path, "rb");
#endif
    if (file == NULL) return -1;
    length = fread(manifest_json, 1u, sizeof(manifest_json), file);
    {
        int failed = ferror(file) || length == sizeof(manifest_json);
        if (fclose(file) != 0) failed = 1;
        if (failed || gs_manifest_parse(manifest_json, length, &manifest) != 0)
            return -1;
    }
#ifdef _WIN32
    if (fopen_s(&file, data_path, "rb") != 0) return -1;
#else
    file = fopen(data_path, "rb");
#endif
    if (file == NULL) return -1;
    length = fread(json, 1u, sizeof(json), file);
    {
        int failed = ferror(file) || length == sizeof(json);
        if (fclose(file) != 0) failed = 1;
        if (failed) return -1;
    }
    json[length] = '\0';
    if (load_plan(json, length, &plan, &profile, approver, &approved_at) != 0 ||
            manifest.version != plan.revision ||
            verify_semantics(&plan, &profile, approver, approved_at, passes,
                             commands, trusted_profile) != 0) return -1;
    return 0;
}
