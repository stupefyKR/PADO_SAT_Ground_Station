#include "tc_workflow.h"

#include "gs/hash.h"
#include "gs/manifest.h"
#include "cJSON.h"

#include <stdio.h>
#include <string.h>

#define TEST_SEQUENCE_BYTES 32768u

static int copy_token(char *out, size_t capacity, const char *in)
{
    size_t index;
    if (out == NULL || in == NULL) return -1;
    for (index = 0u; index < capacity; ++index) {
        unsigned char c = (unsigned char)in[index];
        if (c == 0u) return index == 0u ? -1 : 0;
        if (c < 33u || c > 126u) return -1;
        out[index] = in[index];
    }
    return -1;
}

static int read_sequence(const char *path, char out[TEST_SEQUENCE_BYTES + 1u],
                         size_t *length)
{
    FILE *file;
    int failed;
    if (path == NULL || length == NULL) return -1;
#ifdef _WIN32
    if (fopen_s(&file, path, "rb") != 0) return -1;
#else
    file = fopen(path, "rb");
#endif
    if (file == NULL) return -1;
    *length = fread(out, 1u, TEST_SEQUENCE_BYTES + 1u, file);
    failed = ferror(file) || *length == TEST_SEQUENCE_BYTES + 1u;
    if (fclose(file) != 0) failed = 1;
    if (failed) return -1;
    out[*length] = '\0';
    return 0;
}

static int hex_nibble(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

static int bounded_integer(const cJSON *item, double minimum, double maximum)
{
    return cJSON_IsNumber(item) && item->valuedouble >= minimum &&
           item->valuedouble <= maximum &&
           item->valuedouble == (double)(uint64_t)item->valuedouble;
}

static int digest_hex(const char hex[GS_SHA256_HEX_SIZE],
                      unsigned char out[32])
{
    size_t index;
    for (index = 0u; index < 32u; ++index) {
        int high = hex_nibble(hex[index * 2u]);
        int low = hex_nibble(hex[index * 2u + 1u]);
        if (high < 0 || low < 0) return -1;
        out[index] = (unsigned char)((high << 4) | low);
    }
    return 0;
}

static int find_scheduled_step(const cJSON *root,
                               const gs_test_tc_request_t *request,
                               uint64_t *aos, uint64_t *los)
{
    const cJSON *steps = cJSON_GetObjectItemCaseSensitive(root, "steps");
    const cJSON *aos_json = cJSON_GetObjectItemCaseSensitive(root, "aos_utc_s");
    const cJSON *los_json = cJSON_GetObjectItemCaseSensitive(root, "los_utc_s");
    int index;
    if (!cJSON_IsArray(steps) ||
            !bounded_integer(aos_json, 1.0, 9007199254740991.0) ||
            !bounded_integer(los_json, 1.0, 9007199254740991.0)) return -1;
    *aos = (uint64_t)aos_json->valuedouble;
    *los = (uint64_t)los_json->valuedouble;
    for (index = 0; index < cJSON_GetArraySize(steps); ++index) {
        const cJSON *step = cJSON_GetArrayItem(steps, index);
        const cJSON *id = cJSON_GetObjectItemCaseSensitive(step, "step_id");
        const cJSON *command = cJSON_GetObjectItemCaseSensitive(step, "command_id");
        const cJSON *offset = cJSON_GetObjectItemCaseSensitive(step, "offset_s");
        const cJSON *duration = cJSON_GetObjectItemCaseSensitive(step, "duration_s");
        if (!bounded_integer(id, 1.0, (double)UINT32_MAX) ||
                !cJSON_IsString(command) || command->valuestring == NULL ||
                !bounded_integer(offset, 0.0, (double)UINT32_MAX) ||
                !bounded_integer(duration, 1.0, (double)UINT32_MAX)) return -1;
        if (id->valuedouble == (double)request->step_id &&
                strcmp(command->valuestring, request->command_id) == 0 &&
                request->send_at_utc_s >= *aos + (uint64_t)offset->valuedouble &&
                request->send_at_utc_s < *aos + (uint64_t)offset->valuedouble +
                                           (uint64_t)duration->valuedouble &&
                request->send_at_utc_s < *los) return 0;
    }
    return -1;
}

int gs_ros_tc_validate_test(const char *sequence_path, const char *manifest_path,
                            const gs_mps_pass_catalog_t *passes,
                            const gs_mps_command_catalog_t *mps_commands,
                            const gs_mps_test_profile_t *profile,
                            const gs_test_tc_catalog_t *tc_catalog,
                            const gs_test_tc_request_t *request,
                            gs_test_tc_ticket_t *ticket)
{
    char sequence[TEST_SEQUENCE_BYTES + 1u];
    char manifest_json[TEST_SEQUENCE_BYTES + 1u];
    char digest[GS_SHA256_HEX_SIZE];
    gs_manifest_t manifest;
    char author[GS_MPS_ACTOR_SIZE] = {0};
    char command_id[GS_MPS_COMMAND_ID_SIZE] = {0};
    gs_test_tc_ticket_t prepared = {0};
    const gs_test_tc_command_t *command = NULL;
    cJSON *root;
    size_t length;
    size_t manifest_length;
    size_t index;
    if (ticket != NULL) {
        gs_test_tc_ticket_t empty = {0};
        *ticket = empty;
    }
    if (ticket == NULL || tc_catalog == NULL || request == NULL ||
            tc_catalog->test_only != 1 || tc_catalog->count == 0u ||
            tc_catalog->count > GS_TEST_TC_MAX_COMMANDS ||
            mps_commands == NULL ||
            tc_catalog->command_db_version != mps_commands->version ||
            request->command_db_version != mps_commands->version ||
            request->step_id == 0u ||
            copy_token(author, sizeof(author), request->author) != 0 ||
            copy_token(command_id, sizeof(command_id),
                       request->command_id) != 0 ||
            strncmp(command_id, "TEST_", 5u) != 0 ||
            gs_ros_verify_test_sequence(sequence_path, manifest_path, passes,
                                        mps_commands, profile) != 0 ||
            read_sequence(sequence_path, sequence, &length) != 0 ||
            read_sequence(manifest_path, manifest_json, &manifest_length) != 0 ||
            gs_manifest_parse(manifest_json, manifest_length,
                              &manifest) != 0 ||
            gs_sha256_bytes((const unsigned char *)sequence, length,
                            digest) != 0 ||
            strcmp(digest, manifest.sha256) != 0) return -1;
    for (index = 0u; index < tc_catalog->count; ++index) {
        const gs_test_tc_command_t *candidate = &tc_catalog->commands[index];
        char candidate_id[GS_MPS_COMMAND_ID_SIZE] = {0};
        size_t previous;
        if (copy_token(candidate_id, sizeof(candidate_id),
                       candidate->command_id) != 0 ||
                strncmp(candidate_id, "TEST_", 5u) != 0 ||
                candidate->test_code == 0u ||
                candidate->risk < GS_TEST_TC_NORMAL ||
                candidate->risk > GS_TEST_TC_DANGEROUS ||
                candidate->min_parameter > candidate->max_parameter)
            return -1;
        for (previous = 0u; previous < index; ++previous)
            if (candidate->test_code == tc_catalog->commands[previous].test_code ||
                    strcmp(candidate_id,
                           tc_catalog->commands[previous].command_id) == 0)
                return -1;
        if (strcmp(candidate_id, command_id) == 0) {
            if (command != NULL) return -1;
            command = candidate;
        }
    }
    if (command == NULL || command->test_code == 0u ||
            command->risk < GS_TEST_TC_NORMAL ||
            command->risk > GS_TEST_TC_DANGEROUS ||
            command->min_parameter > command->max_parameter ||
            request->parameter < command->min_parameter ||
            request->parameter > command->max_parameter ||
            (request->recent_state & command->require_set) !=
                command->require_set ||
            (request->recent_state & command->require_clear) != 0u) return -1;
    root = cJSON_ParseWithLengthOpts(sequence, length + 1u, NULL, 1);
    if (root == NULL) return -1;
    {
        const cJSON *type = cJSON_GetObjectItemCaseSensitive(root, "type");
        const cJSON *marker = cJSON_GetObjectItemCaseSensitive(root,
                                                               "non_operational");
        const cJSON *db_version = cJSON_GetObjectItemCaseSensitive(root,
                                                                   "command_db_version");
        const cJSON *revision = cJSON_GetObjectItemCaseSensitive(root,
                                                                 "revision");
        uint64_t aos = 0u;
        uint64_t los = 0u;
        int valid = !cJSON_IsString(type) || type->valuestring == NULL ||
                    strcmp(type->valuestring, "TEST_ONLY_SEQUENCE") != 0 ||
                    !cJSON_IsTrue(marker) ||
                    !bounded_integer(db_version, 1.0, (double)UINT32_MAX) ||
                    db_version->valuedouble != request->command_db_version ||
                    !bounded_integer(revision, 1.0, (double)UINT32_MAX) ||
                    revision->valuedouble != manifest.version ||
                    strcmp(manifest.type, "TEST_ONLY_SEQUENCE") != 0 ||
                    strcmp(manifest.from, "mps") != 0 ||
                    strcmp(manifest.to, "ros") != 0;
        if (!valid) valid = find_scheduled_step(root, request, &aos, &los) != 0;
        cJSON_Delete(root);
        if (valid != 0 || aos == 0u || los <= aos ||
                digest_hex(digest, prepared.sequence_digest) != 0) return -1;
        prepared.aos_utc_s = aos;
        prepared.los_utc_s = los;
    }
    prepared.test_code = command->test_code;
    prepared.command_db_version = request->command_db_version;
    prepared.parameter = request->parameter;
    prepared.send_at_utc_s = request->send_at_utc_s;
    prepared.risk = command->risk;
    for (index = 0u; index < sizeof(author); ++index)
        prepared.author[index] = author[index];
    prepared.validated = 1;
    prepared.test_only = 1;
    *ticket = prepared;
    return 0;
}

int gs_ros_tc_approve_test(gs_test_tc_ticket_t *ticket,
                           const gs_test_tc_identity_t *identity,
                           uint64_t approved_at_utc_s)
{
    char actor[GS_MPS_ACTOR_SIZE] = {0};
    size_t index;
    if (ticket == NULL || identity == NULL || ticket->validated != 1 ||
            ticket->approved != 0 || ticket->test_only != 1 ||
            approved_at_utc_s == 0u ||
            approved_at_utc_s > ticket->send_at_utc_s ||
            copy_token(actor, sizeof(actor), identity->actor) != 0) return -1;
    if (ticket->risk == GS_TEST_TC_NORMAL) {
        if (identity->role != GS_TEST_TC_OPERATOR ||
                strcmp(actor, ticket->author) != 0) return -1;
    } else if (ticket->risk == GS_TEST_TC_CAUTION) {
        if (identity->role != GS_TEST_TC_PEER ||
                strcmp(actor, ticket->author) == 0) return -1;
    } else if (ticket->risk == GS_TEST_TC_DANGEROUS) {
        if (identity->role != GS_TEST_TC_LEAD ||
                identity->reauthenticated != 1 ||
                strcmp(actor, ticket->author) == 0) return -1;
    } else return -1;
    for (index = 0u; index < sizeof(actor); ++index)
        ticket->approved_by[index] = actor[index];
    ticket->approved_at_utc_s = approved_at_utc_s;
    ticket->approved_role = identity->role;
    ticket->approval_reauthenticated = identity->reauthenticated;
    ticket->approved = 1;
    return 0;
}
