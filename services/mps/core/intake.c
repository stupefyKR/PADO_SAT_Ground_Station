#include "intake.h"

#include "gs/hash.h"
#include "gs/manifest.h"

#include <stdio.h>
#include <string.h>

static int copy_text(char *out, size_t capacity, const char *value,
                     int identifier)
{
    size_t index;
    if (value == NULL) return -1;
    for (index = 0u; index < capacity; ++index) {
        const unsigned char c = (unsigned char)value[index];
        if (c == 0u) return index == 0u ? -1 : 0;
        if (c < 33u || c > 126u || (identifier &&
                !((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                  c == '-' || c == '_'))) return -1;
        out[index] = value[index];
    }
    return -1;
}

static int read_manifest(const char *path, gs_manifest_t *out)
{
    char json[GS_MANIFEST_MAX_BYTES + 1u];
    FILE *file;
    size_t length;
    int failed;
    if (path == NULL || out == NULL) return -1;
#ifdef _WIN32
    if (fopen_s(&file, path, "rb") != 0) return -1;
#else
    file = fopen(path, "rb");
#endif
    if (file == NULL) return -1;
    length = fread(json, 1u, sizeof(json), file);
    failed = ferror(file) || length == sizeof(json);
    if (fclose(file) != 0) failed = 1;
    return failed ? -1 : gs_manifest_parse(json, length, out);
}

static int verified_chain(const char *data_path, const char *upstream_path,
                          const char *downstream_path, const char *type,
                          gs_manifest_t *downstream)
{
    gs_manifest_t upstream;
    const char *const allowed[] = {type};
    if (read_manifest(upstream_path, &upstream) != 0 ||
            read_manifest(downstream_path, downstream) != 0 ||
            gs_manifest_verify(&upstream, "sdc", "dgs-archive",
                               allowed, 1u, data_path) != 0 ||
            gs_manifest_verify(downstream, "dgs-archive", "mps",
                               allowed, 1u, data_path) != 0 ||
            upstream.version != downstream->version ||
            strcmp(upstream.sha256, downstream->sha256) != 0 ||
            strcmp(upstream.file, downstream->file) != 0) return -1;
    return 0;
}

int gs_mps_request_receive_test(const char *data_path,
                                 const char *upstream_manifest_path,
                                 const char *downstream_manifest_path,
                                 const char *request_id, const char *pass_id,
                                 const gs_mps_pass_catalog_t *passes,
                                 gs_mps_mission_request_t *out)
{
    gs_mps_mission_request_t request = {0};
    gs_manifest_t manifest;
    size_t index;
    int found = 0;
    if (out == NULL || passes == NULL || passes->count > GS_MPS_MAX_PASSES ||
            copy_text(request.data_path, sizeof(request.data_path),
                      data_path, 0) != 0 ||
            copy_text(request.request_id, sizeof(request.request_id),
                      request_id, 1) != 0 ||
            copy_text(request.pass_id, sizeof(request.pass_id),
                      pass_id, 1) != 0 ||
            verified_chain(data_path, upstream_manifest_path,
                           downstream_manifest_path, "MISSION_REQUEST",
                           &manifest) != 0) return -1;
    for (index = 0u; index < passes->count; ++index)
        if (strcmp(passes->passes[index].pass_id, request.pass_id) == 0) found = 1;
    if (!found) return -1;
    request.version = manifest.version;
    for (index = 0u; index < sizeof(request.sha256); ++index)
        request.sha256[index] = manifest.sha256[index];
    request.test_only = 1;
    *out = request;
    return 0;
}

int gs_mps_request_decide_test(gs_mps_mission_request_t *request,
                                const gs_mps_plan_store_t *plans,
                                gs_mps_intake_status_t decision,
                                const gs_mps_identity_t *identity)
{
    gs_mps_plan_revision_t latest;
    char actor[GS_MPS_ACTOR_SIZE] = {0};
    char digest[GS_SHA256_HEX_SIZE];
    size_t index;
    if (request == NULL || identity == NULL ||
            request->test_only != 1 ||
            request->status != GS_MPS_INTAKE_PENDING ||
            identity->role != GS_MPS_ROLE_PLANNER ||
            (decision != GS_MPS_INTAKE_ACCEPTED &&
             decision != GS_MPS_INTAKE_REJECTED) ||
            copy_text(actor, sizeof(actor), identity->actor, 0) != 0) return -1;
    if (decision == GS_MPS_INTAKE_ACCEPTED) {
        if (gs_mps_plan_latest(plans, &latest) != 0 ||
                latest.non_operational != 1 ||
                strcmp(latest.pass.pass_id, request->pass_id) != 0 ||
                gs_sha256_file(request->data_path, digest) != 0 ||
                strcmp(digest, request->sha256) != 0) return -1;
        request->linked_plan_revision = latest.revision;
    }
    for (index = 0u; index < sizeof(actor); ++index) request->decided_by[index] = actor[index];
    request->status = decision;
    return 0;
}

int gs_mps_uplink_receive_test(const char *data_path,
                                const char *upstream_manifest_path,
                                const char *downstream_manifest_path,
                                const char *independent_sha256,
                                const char *purpose, int high_risk,
                                int flatsat_verified, const char *flatsat_record_id,
                                const char *submitter,
                                gs_mps_uplink_intake_t *out)
{
    gs_mps_uplink_intake_t intake = {0};
    gs_manifest_t manifest;
    char digest[GS_SHA256_HEX_SIZE];
    size_t index;
    if (out == NULL || high_risk < 0 || high_risk > 1 ||
            flatsat_verified < 0 || flatsat_verified > 1 ||
            copy_text(intake.data_path, sizeof(intake.data_path),
                      data_path, 0) != 0 ||
            copy_text(intake.purpose, sizeof(intake.purpose),
                      purpose, 0) != 0 ||
            copy_text(intake.submitted_by, sizeof(intake.submitted_by),
                      submitter, 0) != 0 ||
            (flatsat_record_id != NULL &&
             copy_text(intake.flatsat_record_id,
                       sizeof(intake.flatsat_record_id),
                       flatsat_record_id, 1) != 0) ||
            (flatsat_verified && intake.flatsat_record_id[0] == '\0') ||
            verified_chain(data_path, upstream_manifest_path,
                           downstream_manifest_path, "UPLINK_FILE",
                           &manifest) != 0 ||
            independent_sha256 == NULL || strlen(independent_sha256) != 64u ||
            gs_sha256_file(data_path, digest) != 0 ||
            strcmp(independent_sha256, digest) != 0) return -1;
    intake.version = manifest.version;
    intake.high_risk = high_risk;
    intake.flatsat_verified = flatsat_verified;
    for (index = 0u; index < sizeof(intake.sha256); ++index)
        intake.sha256[index] = digest[index];
    intake.test_only = 1;
    *out = intake;
    return 0;
}

int gs_mps_uplink_decide_test(gs_mps_uplink_intake_t *intake,
                               gs_mps_intake_status_t decision,
                               const gs_mps_identity_t *identity)
{
    char actor[GS_MPS_ACTOR_SIZE] = {0};
    char digest[GS_SHA256_HEX_SIZE];
    size_t index;
    if (intake == NULL || identity == NULL || intake->test_only != 1 ||
            intake->status != GS_MPS_INTAKE_PENDING ||
            identity->role != GS_MPS_ROLE_LEAD ||
            identity->reauthenticated != 1 ||
            (decision != GS_MPS_INTAKE_ACCEPTED &&
             decision != GS_MPS_INTAKE_REJECTED) ||
            copy_text(actor, sizeof(actor), identity->actor, 0) != 0 ||
            strcmp(actor, intake->submitted_by) == 0) return -1;
    if (decision == GS_MPS_INTAKE_ACCEPTED) {
        if ((intake->high_risk && (!intake->flatsat_verified ||
              intake->flatsat_record_id[0] == '\0')) ||
                gs_sha256_file(intake->data_path, digest) != 0 ||
                strcmp(digest, intake->sha256) != 0) return -1;
    }
    for (index = 0u; index < sizeof(actor); ++index) intake->decided_by[index] = actor[index];
    intake->status = decision;
    return 0;
}
