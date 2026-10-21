#include "sequence_export.h"
#include "sequence_verify.h"
#include "intake.h"
#include "gs/exchange.h"
#include "gs/hash.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <process.h>
#define test_pid _getpid
#else
#include <unistd.h>
#define test_pid getpid
#endif

static int failures;

static void expect(int condition, const char *label)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", label);
        ++failures;
    }
}

static int write_bytes(const char *path, const char *data, size_t length)
{
    FILE *file;
    int failed;
#ifdef _WIN32
    if (fopen_s(&file, path, "wb") != 0) return -1;
#else
    file = fopen(path, "wb");
#endif
    if (file == NULL) return -1;
    failed = fwrite(data, 1u, length, file) != length;
    if (fclose(file) != 0) failed = 1;
    return failed ? -1 : 0;
}

static const char *basename_of(const char *path)
{
    const char *slash = strrchr(path, '/');
    const char *backslash = strrchr(path, '\\');
    if (backslash != NULL && (slash == NULL || backslash > slash)) slash = backslash;
    return slash == NULL ? path : slash + 1;
}

static int make_manifest(const char *path, const char *data_path,
                         const char *type, const char *from, const char *to,
                         unsigned int version)
{
    char digest[GS_SHA256_HEX_SIZE];
    char json[1024];
    int length;
    if (gs_sha256_file(data_path, digest) != 0) return -1;
    length = snprintf(json, sizeof(json),
        "{\"file\":\"%s\",\"type\":\"%s\",\"from\":\"%s\","
        "\"to\":\"%s\",\"version\":%u,"
        "\"created\":\"2026-10-21T00:00:00Z\",\"sha256\":\"%s\"}",
        basename_of(data_path), type, from, to, version, digest);
    if (length < 0 || (size_t)length >= sizeof(json)) return -1;
    return write_bytes(path, json, (size_t)length);
}

static void test_sequence(const char *directory, gs_mps_plan_store_t *store,
                          gs_mps_pass_catalog_t *passes,
                          gs_mps_command_catalog_t *commands)
{
    gs_mps_test_profile_t profile = {0u, 8u, 2u, 1};
    gs_mps_test_profile_t wrong_profile = {0u, 16u, 2u, 1};
    gs_mps_test_approval_t approval = {0};
    gs_mps_test_approval_t empty = {0};
    gs_mps_edit_t edit = {0};
    char sequence[GS_MPS_TEST_SEQUENCE_MAX_BYTES + 1u];
    char manifest[4096];
    char name[80];
    char data_temp[600], manifest_temp[600], data_final[600], manifest_final[600];
    uint32_t revision = 0u;
    gs_mps_pass_t pass = {"P-TEST-01", 1000, 1100, 10u, 10u};
    expect(gs_mps_pass_catalog_add(passes, &pass) == 0, "stage4 pass");
    expect(gs_mps_plan_create(store, passes, pass.pass_id, commands,
                              "planner.one") == 0, "stage4 draft");
    edit.kind = GS_MPS_EDIT_ADD;
    edit.step_id = 1u;
    edit.step.step_id = 1u;
    (void)snprintf(edit.step.command_id, sizeof(edit.step.command_id), "TEST_A");
    edit.step.offset_s = 20u;
    edit.step.duration_s = 10u;
    edit.step.effect_set = 2u;
    edit.step.uplink_bytes = 10u;
    expect(gs_mps_plan_edit(store, 1u, passes, commands, &edit,
                            "planner.one", &revision) == 0, "first test step");
    edit.step_id = 2u;
    edit.step.step_id = 2u;
    (void)snprintf(edit.step.command_id, sizeof(edit.step.command_id), "TEST_B");
    edit.step.offset_s = 40u;
    edit.step.require_set = 2u;
    edit.step.effect_set = 0u;
    expect(gs_mps_plan_edit(store, revision, passes, commands, &edit,
                            "planner.one", &revision) == 0, "second test step");
    expect(gs_mps_sequence_export_test(store, passes, commands, &empty, "TEST_ONLY_bad.json",
            "2026-10-21T00:00:00Z", sequence, sizeof(sequence),
            manifest, sizeof(manifest)) != 0, "unapproved export denied");
    expect(gs_mps_sequence_approve_test(store, revision - 1u, passes, commands,
            &profile, "lead.one", 1, 1, 100u, &approval) != 0,
           "stale draft approval denied");
    expect(gs_mps_sequence_approve_test(store, revision, passes, commands,
            &profile, "planner.one", 1, 1, 100u, &approval) != 0,
           "author cannot approve own draft");
    expect(gs_mps_sequence_approve_test(store, revision, passes, commands,
            &profile, "lead.one", 1, 0, 100u, &approval) != 0,
           "approval requires reauthentication");
    profile.max_commands = 1u;
    expect(gs_mps_sequence_approve_test(store, revision, passes, commands,
            &profile, "lead.one", 1, 1, 100u, &approval) != 0,
           "conflicting draft approval denied");
    profile.max_commands = 2u;
    expect(gs_mps_sequence_approve_test(store, revision, passes, commands,
            &profile, "lead.one", 1, 1, 100u, &approval) == 0,
           "clean test draft approved by other lead");
    (void)snprintf(name, sizeof(name), "TEST_ONLY_%u.json", (unsigned int)test_pid());
    expect(gs_mps_sequence_export_test(store, passes, commands, &approval, name,
            "2026-10-21T00:00:00Z", sequence, sizeof(sequence),
            manifest, sizeof(manifest)) == 0, "test sequence and manifest export");
    (void)snprintf(data_temp, sizeof(data_temp), "%s/%s.tmp", directory, name);
    (void)snprintf(data_final, sizeof(data_final), "%s/%s", directory, name);
    (void)snprintf(manifest_temp, sizeof(manifest_temp), "%s/%s.manifest.tmp", directory, name);
    (void)snprintf(manifest_final, sizeof(manifest_final), "%s/%s.manifest.json", directory, name);
    expect(write_bytes(data_temp, sequence, strlen(sequence)) == 0 &&
           write_bytes(manifest_temp, manifest, strlen(manifest)) == 0,
           "write temporary test handoff");
    expect(gs_exchange_publish_pair(data_temp, data_final, manifest_temp,
                                    manifest_final) == 0,
           "publish sequence then manifest");
    expect(gs_ros_verify_test_sequence(data_final, manifest_final, passes,
                                       commands, &profile) == 0,
           "ROS independently verifies test sequence");
    expect(gs_mps_sequence_export_test(store, passes, commands, &approval,
            "TC_SEQUENCE.json", "2026-10-21T00:00:00Z", sequence,
            sizeof(sequence), manifest, sizeof(manifest)) != 0,
           "operational filename denied");
    {
        gs_mps_test_approval_t forged = approval;
        forged.plan.step_count = GS_MPS_MAX_STEPS + 1u;
        expect(gs_mps_sequence_export_test(store, passes, commands, &forged,
                name, "2026-10-21T00:00:00Z", sequence, sizeof(sequence),
                manifest, sizeof(manifest)) != 0,
               "forged oversized approval denied");
    }
    expect(gs_ros_verify_test_sequence(data_final, manifest_final, passes,
                                       commands, &wrong_profile) != 0,
           "ROS rejects changed trusted profile");
    {
        char altered[GS_MPS_TEST_SEQUENCE_MAX_BYTES + 1u];
        char *command;
        (void)snprintf(altered, sizeof(altered), "%s", sequence);
        command = strstr(altered, "TEST_A");
        expect(command != NULL, "locate synthetic command for semantic test");
        if (command != NULL) {
            command[0] = 'R';
            command[1] = 'E';
            command[2] = 'A';
            command[3] = 'L';
            expect(write_bytes(data_final, altered, strlen(altered)) == 0 &&
                   make_manifest(manifest_final, data_final,
                                 "TEST_ONLY_SEQUENCE", "mps", "ros",
                                 approval.plan.revision) == 0 &&
                   gs_ros_verify_test_sequence(data_final, manifest_final,
                                               passes, commands, &profile) != 0,
                   "ROS rejects semantic change despite matching manifest hash");
            expect(write_bytes(data_final, sequence, strlen(sequence)) == 0 &&
                   write_bytes(manifest_final, manifest, strlen(manifest)) == 0,
                   "restore approved test handoff");
        }
    }
    ++commands->version;
    expect(gs_ros_verify_test_sequence(data_final, manifest_final, passes,
                                       commands, &profile) != 0,
           "ROS rejects command DB version mismatch");
    --commands->version;
    expect(write_bytes(data_final, "tampered", 8u) == 0 &&
           gs_ros_verify_test_sequence(data_final, manifest_final, passes,
                                       commands, &profile) != 0,
           "ROS rejects changed sequence hash");
    edit.kind = GS_MPS_EDIT_REMOVE;
    edit.step_id = 2u;
    expect(gs_mps_plan_edit(store, revision, passes, commands, &edit,
                            "planner.one", &revision) == 0 &&
           gs_mps_sequence_export_test(store, passes, commands, &approval,
                name, "2026-10-21T00:00:00Z", sequence,
                sizeof(sequence), manifest, sizeof(manifest)) != 0,
           "approval expires after a new draft revision");
    (void)remove(data_final);
    (void)remove(manifest_final);
}

static void test_intake(const char *directory, gs_mps_plan_store_t *store,
                        gs_mps_pass_catalog_t *passes)
{
    char request_path[600], request_up[600], request_down[600];
    char uplink_path[600], uplink_up[600], uplink_down[600];
    char digest[GS_SHA256_HEX_SIZE];
    gs_mps_mission_request_t request = {0};
    gs_mps_uplink_intake_t uplink = {0};
    gs_mps_identity_t planner = {"planner.two", GS_MPS_ROLE_PLANNER, 0};
    gs_mps_identity_t lead = {"lead.one", GS_MPS_ROLE_LEAD, 1};
    gs_mps_identity_t unverified = {"lead.one", GS_MPS_ROLE_LEAD, 0};
    gs_mps_identity_t submitter = {"submitter.one", GS_MPS_ROLE_LEAD, 1};
    (void)snprintf(request_path, sizeof(request_path), "%s/MISSION_REQUEST_%u.dat",
                   directory, (unsigned int)test_pid());
    (void)snprintf(request_up, sizeof(request_up), "%s.up.json", request_path);
    (void)snprintf(request_down, sizeof(request_down), "%s.down.json", request_path);
    expect(write_bytes(request_path, "opaque request", 14u) == 0 &&
           make_manifest(request_up, request_path, "MISSION_REQUEST",
                         "sdc", "dgs-archive", 1u) == 0 &&
           make_manifest(request_down, request_path, "MISSION_REQUEST",
                         "dgs-archive", "mps", 1u) == 0,
           "prepare two-hop mission request");
    expect(gs_mps_request_receive_test(request_path, request_up, request_down,
            "REQ-01", "P-TEST-01", passes, &request) == 0,
           "register verified mission request");
    expect(gs_mps_request_decide_test(&request, store,
            GS_MPS_INTAKE_ACCEPTED, &lead) != 0,
           "lead cannot act as planner");
    expect(gs_mps_request_decide_test(&request, store,
            GS_MPS_INTAKE_ACCEPTED, &planner) == 0 &&
           request.status == GS_MPS_INTAKE_ACCEPTED &&
           request.linked_plan_revision == store->revision_count,
           "link request to current plan without creating TC");
    expect(gs_mps_request_decide_test(&request, store,
            GS_MPS_INTAKE_REJECTED, &planner) != 0,
           "request cannot be decided twice");
    expect(gs_mps_request_receive_test(request_path, request_down, request_up,
            "REQ-02", "P-TEST-01", passes, &request) != 0,
           "reject reversed source chain");
    expect(make_manifest(request_down, request_path, "MISSION_REQUEST",
                         "dgs-archive", "mps", 2u) == 0 &&
           gs_mps_request_receive_test(request_path, request_up, request_down,
               "REQ-02", "P-TEST-01", passes, &request) != 0 &&
           make_manifest(request_down, request_path, "MISSION_REQUEST",
                         "dgs-archive", "mps", 1u) == 0,
           "reject inconsistent transfer versions");
    expect(gs_mps_request_receive_test(request_path, request_up, request_down,
            "REQ-02", "P-TEST-01", passes, &request) == 0 &&
           gs_mps_request_decide_test(&request, store,
            GS_MPS_INTAKE_REJECTED, &planner) == 0,
           "request rejection recorded");
    expect(gs_mps_request_receive_test(request_path, request_up, request_down,
            "REQ-03", "P-TEST-01", passes, &request) == 0 &&
           write_bytes(request_path, "changed request", 15u) == 0 &&
           gs_mps_request_decide_test(&request, store,
                GS_MPS_INTAKE_ACCEPTED, &planner) != 0,
           "request changed after registration denied");
    (void)snprintf(uplink_path, sizeof(uplink_path), "%s/UPLINK_%u.bin",
                   directory, (unsigned int)test_pid());
    (void)snprintf(uplink_up, sizeof(uplink_up), "%s.up.json", uplink_path);
    (void)snprintf(uplink_down, sizeof(uplink_down), "%s.down.json", uplink_path);
    expect(write_bytes(uplink_path, "test uplink bytes", 17u) == 0 &&
           make_manifest(uplink_up, uplink_path, "UPLINK_FILE",
                         "sdc", "dgs-archive", 2u) == 0 &&
           make_manifest(uplink_down, uplink_path, "UPLINK_FILE",
                         "dgs-archive", "mps", 2u) == 0 &&
           gs_sha256_file(uplink_path, digest) == 0,
           "prepare two-hop uplink file");
    expect(gs_mps_uplink_receive_test(uplink_path, uplink_up, uplink_down,
            "0000000000000000000000000000000000000000000000000000000000000000",
            "firmware-test", 1, 0, NULL, "submitter.one", &uplink) != 0,
           "reject independent hash mismatch");
    expect(gs_mps_uplink_receive_test(uplink_path, uplink_up, uplink_down,
            digest, "firmware-test", 1, 0, NULL, "submitter.one", &uplink) == 0 &&
           uplink.version == 2u, "register version and purpose");
    expect(gs_mps_uplink_decide_test(&uplink, GS_MPS_INTAKE_ACCEPTED,
                                     &unverified) != 0,
           "reject missing reauthentication");
    expect(gs_mps_uplink_decide_test(&uplink, GS_MPS_INTAKE_ACCEPTED,
                                     &submitter) != 0,
           "reject self approval");
    expect(gs_mps_uplink_decide_test(&uplink, GS_MPS_INTAKE_ACCEPTED,
                                     &lead) != 0,
           "high risk file requires FlatSat record");
    uplink.flatsat_verified = 1;
    (void)snprintf(uplink.flatsat_record_id,
                   sizeof(uplink.flatsat_record_id), "FS-TEST-01");
    expect(gs_mps_uplink_decide_test(&uplink, GS_MPS_INTAKE_ACCEPTED,
                                     &lead) == 0 &&
           uplink.status == GS_MPS_INTAKE_ACCEPTED,
           "lead approves verified high risk file");
    expect(gs_mps_uplink_decide_test(&uplink, GS_MPS_INTAKE_REJECTED,
                                     &lead) != 0,
           "uplink cannot be decided twice");
    expect(gs_mps_uplink_receive_test(uplink_path, uplink_up, uplink_down,
            digest, "firmware-test", 0, 0, NULL, "submitter.one", &uplink) == 0,
           "register second intake for change detection");
    expect(write_bytes(uplink_path, "changed", 7u) == 0 &&
           gs_mps_uplink_decide_test(&uplink, GS_MPS_INTAKE_ACCEPTED,
                                     &lead) != 0,
           "reject changed bytes after registration");
    (void)remove(request_path);
    (void)remove(request_up);
    (void)remove(request_down);
    (void)remove(uplink_path);
    (void)remove(uplink_up);
    (void)remove(uplink_down);
}

int main(int argc, char **argv)
{
    gs_mps_plan_store_t *store;
    gs_mps_pass_catalog_t passes = {0};
    gs_mps_command_catalog_t commands = {1u, 0u, 0};
    if (argc != 2) return 2;
    store = calloc(1u, sizeof(*store));
    if (store == NULL) return 2;
    test_sequence(argv[1], store, &passes, &commands);
    test_intake(argv[1], store, &passes);
    free(store);
    return failures == 0 ? 0 : 1;
}
