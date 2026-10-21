#include "gs/manifest.h"
#include "gs/hash.h"
#include "cJSON.h"

#include <ctype.h>
#include <limits.h>
#include <string.h>

static int copy_string(const cJSON *object, const char *key, char *destination,
                       size_t capacity)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(object, key);
    size_t length;
    if (!cJSON_IsString(value) || value->valuestring == NULL) return -1;
    length = strlen(value->valuestring);
    if (length == 0 || length >= capacity) return -1;
    memcpy(destination, value->valuestring, length + 1u);
    return 0;
}

static int valid_identifier(const char *value, int uppercase)
{
    const unsigned char *cursor = (const unsigned char *)value;
    if (*cursor == '\0') return 0;
    for (; *cursor != '\0'; ++cursor) {
        if (uppercase) {
            if (!(*cursor >= 'A' && *cursor <= 'Z') &&
                    !(*cursor >= '0' && *cursor <= '9') && *cursor != '_') return 0;
        } else if (!(*cursor >= 'a' && *cursor <= 'z') &&
                   !(*cursor >= '0' && *cursor <= '9') && *cursor != '-') return 0;
    }
    return 1;
}

static int valid_filename(const char *value)
{
    const unsigned char *cursor = (const unsigned char *)value;
    if (strcmp(value, ".") == 0 || strcmp(value, "..") == 0) return 0;
    for (; *cursor != '\0'; ++cursor) {
        if (!(*cursor >= 'A' && *cursor <= 'Z') &&
                !(*cursor >= 'a' && *cursor <= 'z') &&
                !(*cursor >= '0' && *cursor <= '9') &&
                *cursor != '.' && *cursor != '_' && *cursor != '-') return 0;
    }
    return 1;
}

static int valid_utc(const char *value)
{
    size_t index;
    unsigned int year;
    unsigned int month;
    unsigned int day;
    unsigned int days_in_month;
    static const unsigned int month_days[] = {31u, 28u, 31u, 30u, 31u, 30u,
                                              31u, 31u, 30u, 31u, 30u, 31u};
    if (strlen(value) != 20u) return 0;
    if (value[4] != '-' || value[7] != '-' || value[10] != 'T' ||
            value[13] != ':' || value[16] != ':' || value[19] != 'Z') return 0;
    for (index = 0; index < 19u; ++index) {
        if (index == 4u || index == 7u || index == 10u ||
                index == 13u || index == 16u) continue;
        if (!isdigit((unsigned char)value[index])) return 0;
    }
    year = (unsigned int)(value[0] - '0') * 1000u + (unsigned int)(value[1] - '0') * 100u +
           (unsigned int)(value[2] - '0') * 10u + (unsigned int)(value[3] - '0');
    month = (unsigned int)(value[5] - '0') * 10u + (unsigned int)(value[6] - '0');
    day = (unsigned int)(value[8] - '0') * 10u + (unsigned int)(value[9] - '0');
    if (year == 0u || month == 0u || month > 12u) return 0;
    days_in_month = month_days[month - 1u];
    if (month == 2u && (year % 400u == 0u ||
            (year % 4u == 0u && year % 100u != 0u))) ++days_in_month;
    if (day == 0u || day > days_in_month) return 0;
    if (value[11] > '2' || (value[11] == '2' && value[12] > '3')) return 0;
    if (value[14] > '5' || value[17] > '5') return 0;
    return 1;
}

static int valid_sha256(const char *value)
{
    size_t index;
    if (strlen(value) != 64u) return 0;
    for (index = 0; index < 64u; ++index) {
        if (!((value[index] >= '0' && value[index] <= '9') ||
                (value[index] >= 'a' && value[index] <= 'f'))) return 0;
    }
    return 1;
}

static int exact_fields(const cJSON *root)
{
    static const char *const keys[] = {
        "file", "type", "from", "to", "version", "created", "sha256"
    };
    const cJSON *item;
    size_t key_index;
    unsigned int counts[7] = {0};
    for (item = root->child; item != NULL; item = item->next) {
        if (item->string == NULL) return 0;
        for (key_index = 0; key_index < 7u; ++key_index) {
            if (strcmp(item->string, keys[key_index]) == 0) break;
        }
        if (key_index == 7u || ++counts[key_index] != 1u) return 0;
    }
    for (key_index = 0; key_index < 7u; ++key_index) {
        if (counts[key_index] != 1u) return 0;
    }
    return 1;
}

static int contains_unicode_nul(const char *json, size_t length)
{
    size_t index;
    for (index = 0; index + 5u < length; ++index) {
        if (json[index] == '\\' && json[index + 1u] == 'u' &&
                json[index + 2u] == '0' && json[index + 3u] == '0' &&
                json[index + 4u] == '0' && json[index + 5u] == '0') return 1;
    }
    return 0;
}

int gs_manifest_parse(const char *json, size_t length, gs_manifest_t *out)
{
    char input[GS_MANIFEST_MAX_BYTES + 1u];
    gs_manifest_t parsed = {0};
    const cJSON *version;
    cJSON *root;
    if (json == NULL || out == NULL || length == 0 ||
            length > GS_MANIFEST_MAX_BYTES || memchr(json, '\0', length) != NULL ||
            contains_unicode_nul(json, length)) return -1;
    memcpy(input, json, length);
    input[length] = '\0';
    root = cJSON_ParseWithLengthOpts(input, length + 1u, NULL, 1);
    if (!cJSON_IsObject(root) || !exact_fields(root)) goto invalid;
    if (copy_string(root, "file", parsed.file, sizeof(parsed.file)) != 0 ||
            copy_string(root, "type", parsed.type, sizeof(parsed.type)) != 0 ||
            copy_string(root, "from", parsed.from, sizeof(parsed.from)) != 0 ||
            copy_string(root, "to", parsed.to, sizeof(parsed.to)) != 0 ||
            copy_string(root, "created", parsed.created, sizeof(parsed.created)) != 0 ||
            copy_string(root, "sha256", parsed.sha256, sizeof(parsed.sha256)) != 0) goto invalid;
    version = cJSON_GetObjectItemCaseSensitive(root, "version");
    if (!cJSON_IsNumber(version) || version->valuedouble < 1.0 ||
            version->valuedouble > (double)UINT_MAX ||
            version->valuedouble != (double)(unsigned int)version->valuedouble) goto invalid;
    parsed.version = (unsigned int)version->valuedouble;
    if (!valid_filename(parsed.file) || !valid_identifier(parsed.type, 1) ||
            !valid_identifier(parsed.from, 0) || !valid_identifier(parsed.to, 0) ||
            !valid_utc(parsed.created) || !valid_sha256(parsed.sha256)) goto invalid;
    *out = parsed;
    cJSON_Delete(root);
    return 0;
invalid:
    cJSON_Delete(root);
    return -1;
}

int gs_manifest_verify(const gs_manifest_t *manifest, const char *expected_from,
                       const char *expected_to, const char *const *allowed_types,
                       size_t allowed_count, const char *data_path)
{
    char digest[GS_SHA256_HEX_SIZE];
    const char *basename;
    const char *separator;
    size_t index;
    int type_allowed = 0;
    if (manifest == NULL || expected_from == NULL || expected_to == NULL ||
            allowed_types == NULL || data_path == NULL) return -1;
    if (strcmp(manifest->from, expected_from) != 0 ||
            strcmp(manifest->to, expected_to) != 0) return -1;
    basename = data_path;
    separator = strrchr(basename, '/');
    if (separator != NULL) basename = separator + 1;
    separator = strrchr(basename, '\\');
    if (separator != NULL) basename = separator + 1;
    if (strcmp(manifest->file, basename) != 0) return -1;
    for (index = 0; index < allowed_count; ++index) {
        if (allowed_types[index] != NULL &&
                strcmp(manifest->type, allowed_types[index]) == 0) type_allowed = 1;
    }
    if (!type_allowed || gs_sha256_file(data_path, digest) != 0) return -1;
    return strcmp(manifest->sha256, digest) == 0 ? 0 : -1;
}
