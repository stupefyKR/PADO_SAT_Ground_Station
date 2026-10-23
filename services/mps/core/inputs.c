#include "inputs.h"
#include "cJSON.h"

#include <limits.h>
#include <string.h>

static int exact_db_fields(const cJSON *root)
{
    static const char *const names[] = {
        "version", "operational", "status", "source", "commands"
    };
    unsigned int counts[5] = {0};
    const cJSON *item;
    size_t index;
    for (item = root->child; item != NULL; item = item->next) {
        if (item->string == NULL) return 0;
        for (index = 0u; index < 5u; ++index) {
            if (strcmp(item->string, names[index]) == 0) break;
        }
        if (index == 5u || ++counts[index] != 1u) return 0;
    }
    for (index = 0u; index < 5u; ++index) {
        if (counts[index] != 1u) return 0;
    }
    return 1;
}

static int escaped_nul(const char *json, size_t length)
{
    size_t index;
    for (index = 0u; index + 5u < length; ++index) {
        if (json[index] == '\\' && json[index + 1u] == 'u' &&
                json[index + 2u] == '0' && json[index + 3u] == '0' &&
                json[index + 4u] == '0' && json[index + 5u] == '0') return 1;
    }
    return 0;
}

int gs_mps_command_catalog_parse(const char *json, size_t length,
                                 gs_mps_command_catalog_t *out)
{
    char input[GS_MPS_COMMAND_DB_MAX_BYTES + 1u];
    gs_mps_command_catalog_t parsed = {0};
    const cJSON *version;
    const cJSON *source;
    const cJSON *status;
    const cJSON *commands;
    const cJSON *operational;
    cJSON *root;
    size_t index;
    if (json == NULL || out == NULL || length == 0u ||
            length > GS_MPS_COMMAND_DB_MAX_BYTES ||
            memchr(json, '\0', length) != NULL || escaped_nul(json, length)) return -1;
    for (index = 0u; index < length; ++index) input[index] = json[index];
    input[length] = '\0';
    root = cJSON_ParseWithLengthOpts(input, length + 1u, NULL, 1);
    if (!cJSON_IsObject(root) || !exact_db_fields(root)) goto invalid;
    version = cJSON_GetObjectItemCaseSensitive(root, "version");
    source = cJSON_GetObjectItemCaseSensitive(root, "source");
    status = cJSON_GetObjectItemCaseSensitive(root, "status");
    commands = cJSON_GetObjectItemCaseSensitive(root, "commands");
    operational = cJSON_GetObjectItemCaseSensitive(root, "operational");
    if (!cJSON_IsNumber(version) || version->valuedouble < 1.0 ||
            version->valuedouble > (double)UINT_MAX ||
            version->valuedouble != (double)(unsigned int)version->valuedouble ||
            !cJSON_IsString(source) || source->valuestring == NULL ||
            strlen(source->valuestring) == 0u || strlen(source->valuestring) > 512u ||
            !cJSON_IsString(status) || status->valuestring == NULL ||
            strcmp(status->valuestring, "TBD(ICD)") != 0 ||
            !cJSON_IsFalse(operational) || !cJSON_IsArray(commands) ||
            cJSON_GetArraySize(commands) != 0) goto invalid;
    parsed.version = (unsigned int)version->valuedouble;
    *out = parsed;
    cJSON_Delete(root);
    return 0;
invalid:
    cJSON_Delete(root);
    return -1;
}

static int valid_pass_id(const char *id)
{
    size_t index;
    if (id[0] == '\0' || memchr(id, '\0', GS_MPS_PASS_ID_SIZE) == NULL) return 0;
    for (index = 0u; id[index] != '\0'; ++index) {
        if ((id[index] < 'A' || id[index] > 'Z') &&
                (id[index] < 'a' || id[index] > 'z') &&
                (id[index] < '0' || id[index] > '9') && id[index] != '-') return 0;
    }
    return 1;
}

int gs_mps_pass_catalog_add(gs_mps_pass_catalog_t *catalog,
                            const gs_mps_pass_t *pass)
{
    size_t index;
    size_t position;
    uint64_t duration;
    if (catalog == NULL || pass == NULL || catalog->count >= GS_MPS_MAX_PASSES ||
            !valid_pass_id(pass->pass_id) || pass->aos_utc_s <= 0 ||
            pass->los_utc_s <= pass->aos_utc_s) return -1;
    duration = (uint64_t)(pass->los_utc_s - pass->aos_utc_s);
    if ((uint64_t)pass->guard_before_s + pass->guard_after_s >= duration) return -1;
    position = catalog->count;
    for (index = 0u; index < catalog->count; ++index) {
        if (strcmp(catalog->passes[index].pass_id, pass->pass_id) == 0) return -1;
        if (position == catalog->count &&
                catalog->passes[index].aos_utc_s > pass->aos_utc_s) position = index;
    }
    for (index = catalog->count; index > position; --index)
        catalog->passes[index] = catalog->passes[index - 1u];
    catalog->passes[position] = *pass;
    ++catalog->count;
    return 0;
}

int gs_mps_pass_catalog_get(const gs_mps_pass_catalog_t *catalog, size_t index,
                            gs_mps_pass_t *out)
{
    if (catalog == NULL || out == NULL || catalog->count > GS_MPS_MAX_PASSES ||
            index >= catalog->count) return -1;
    *out = catalog->passes[index];
    return 0;
}
