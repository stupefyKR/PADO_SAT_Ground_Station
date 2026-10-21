#include "tc_workflow.h"
#include "test_signer.h"
#include "tc_gate.h"
#include "fake_sat.h"
#include "sequence_export.h"

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

static int write_bytes(const char *path, const char *bytes, size_t length)
{
    FILE *file;
    int failed;
#ifdef _WIN32
    if (fopen_s(&file, path, "wb") != 0) return -1;
#else
    file = fopen(path, "wb");
#endif
    if (file == NULL) return -1;
    failed = fwrite(bytes, 1u, length, file) != length;
    if (fclose(file) != 0) failed = 1;
    return failed ? -1 : 0;
}

static int make_sequence(const char *directory, char data_path[600],
                         char manifest_path[600],
                         gs_mps_plan_store_t *store,
                         gs_mps_pass_catalog_t *passes,
                         gs_mps_command_catalog_t *commands,
                         gs_mps_test_profile_t *profile)
{
    gs_mps_pass_t pass = {"P-TEST-05", 1000, 1100, 10u, 10u};
    gs_mps_edit_t edit = {0};
    gs_mps_test_approval_t approval = {0};
    char sequence[GS_MPS_TEST_SEQUENCE_MAX_BYTES + 1u];
    char manifest[4096];
    char name[80];
    uint32_t revision = 0u;
    int written;
    written = snprintf(name, sizeof(name), "TEST_ONLY_STAGE5_%u.json",
                       (unsigned int)test_pid());
    if (written < 0 || (size_t)written >= sizeof(name)) return -1;
    written = snprintf(data_path, 600u, "%s/TEST_ONLY_STAGE5_%u.json",
                       directory, (unsigned int)test_pid());
    if (written < 0 || written >= 600) return -1;
    written = snprintf(manifest_path, 600u,
                       "%s/TEST_ONLY_STAGE5_%u.json.manifest.json",
                       directory, (unsigned int)test_pid());
    if (written < 0 || written >= 600) return -1;
    if (gs_mps_pass_catalog_add(passes, &pass) != 0 ||
            gs_mps_plan_create(store, passes, pass.pass_id, commands,
                               "operator.one") != 0) return -1;
    edit.kind = GS_MPS_EDIT_ADD;
    edit.step_id = 1u;
    edit.step.step_id = 1u;
    (void)snprintf(edit.step.command_id, sizeof(edit.step.command_id),
                   "TEST_A");
    edit.step.offset_s = 20u;
    edit.step.duration_s = 10u;
    edit.step.require_set = 1u;
    edit.step.uplink_bytes = 10u;
    if (gs_mps_plan_edit(store, 1u, passes, commands, &edit,
                         "operator.one", &revision) != 0 ||
            gs_mps_sequence_approve_test(store, revision, passes, commands,
                                         profile, "mps.lead", 1, 1, 100u,
                                         &approval) != 0 ||
            gs_mps_sequence_export_test(store, passes, commands, &approval,
                                        name, "2026-10-21T00:00:00Z",
                                        sequence, sizeof(sequence),
                                        manifest, sizeof(manifest)) != 0 ||
            write_bytes(data_path, sequence, strlen(sequence)) != 0 ||
            write_bytes(manifest_path, manifest, strlen(manifest)) != 0)
        return -1;
    return 0;
}

static void check_validation(const char *data_path, const char *manifest_path,
                             const gs_mps_pass_catalog_t *passes,
                             const gs_mps_command_catalog_t *commands,
                             const gs_mps_test_profile_t *profile,
                             gs_test_tc_catalog_t *catalog,
                             gs_test_tc_request_t *request,
                             gs_test_tc_ticket_t *ticket)
{
    gs_test_tc_request_t invalid = *request;
    (void)snprintf(invalid.command_id, sizeof(invalid.command_id), "TEST_UNKNOWN");
    expect(gs_ros_tc_validate_test(data_path, manifest_path, passes, commands,
            profile, catalog, &invalid, ticket) != 0,
           "unknown command denied");
    invalid = *request;
    invalid.parameter = 11;
    expect(gs_ros_tc_validate_test(data_path, manifest_path, passes, commands,
            profile, catalog, &invalid, ticket) != 0,
           "parameter range denied");
    invalid = *request;
    invalid.step_id = 2u;
    expect(gs_ros_tc_validate_test(data_path, manifest_path, passes, commands,
            profile, catalog, &invalid, ticket) != 0,
           "schedule mismatch denied");
    invalid = *request;
    invalid.send_at_utc_s = 1040u;
    expect(gs_ros_tc_validate_test(data_path, manifest_path, passes, commands,
            profile, catalog, &invalid, ticket) != 0,
           "time window mismatch denied");
    invalid = *request;
    invalid.recent_state = 0u;
    expect(gs_ros_tc_validate_test(data_path, manifest_path, passes, commands,
            profile, catalog, &invalid, ticket) != 0,
           "recent state mismatch denied");
    invalid = *request;
    invalid.command_db_version++;
    expect(gs_ros_tc_validate_test(data_path, manifest_path, passes, commands,
            profile, catalog, &invalid, ticket) != 0,
           "command DB version mismatch denied");
    expect(gs_ros_tc_validate_test(data_path, manifest_path, passes, commands,
            profile, catalog, request, ticket) == 0,
           "five ROS checks accept synthetic scheduled command");
}

static void check_pipeline(const char *data_path, const char *manifest_path,
                           const gs_mps_pass_catalog_t *passes,
                           const gs_mps_command_catalog_t *commands,
                           const gs_mps_test_profile_t *profile)
{
    gs_test_tc_catalog_t catalog = {0};
    gs_test_tc_request_t request = {0};
    gs_test_tc_ticket_t ticket = {0};
    gs_test_tc_identity_t author = {{0}, GS_TEST_TC_OPERATOR, 0};
    gs_test_tc_identity_t peer = {{0}, GS_TEST_TC_PEER, 0};
    gs_test_tc_identity_t lead = {{0}, GS_TEST_TC_LEAD, 1};
    gs_test_tc_identity_t no_reauth = {{0}, GS_TEST_TC_LEAD, 0};
    gs_test_tc_gate_t gate = {77u, 1u, 1u, 5u, 0u, 0u, 0u, 1};
    gs_fake_sat_t sat = {77u, 0u, 1};
    gs_fake_sat_ack_t ack = {0};
    unsigned char key[32];
    unsigned char frame[GS_TEST_TC_FRAME_SIZE];
    unsigned char altered[GS_TEST_TC_FRAME_SIZE];
    unsigned char vector_input[GS_TEST_TC_HEADER_SIZE];
    unsigned char vector_tag[GS_TEST_TC_TAG_SIZE];
    static const unsigned char expected_tag[GS_TEST_TC_TAG_SIZE] = {
        0xa5u,0x66u,0x8bu,0xc1u,0xe0u,0xfdu,0x54u,0x16u,
        0x58u,0x04u,0x8cu,0xf2u,0x95u,0xdfu,0xdeu,0x60u,
        0x8du,0xd5u,0xaau,0xfeu,0x6au,0x1au,0x2bu,0xbdu,
        0x7du,0x10u,0xbcu,0x52u,0x46u,0x31u,0x5fu,0xd0u
    };
    size_t index;
    for (index = 0u; index < sizeof(key); ++index)
        key[index] = (unsigned char)(index + 1u);
    for (index = 0u; index < sizeof(vector_input); ++index)
        vector_input[index] = (unsigned char)index;
    expect(gs_test_hmac_sha256(key, sizeof(key), vector_input,
            sizeof(vector_input), vector_tag) == 0 &&
           memcmp(vector_tag, expected_tag, sizeof(expected_tag)) == 0,
           "test HMAC matches independent SHA-256 reference");
    catalog.test_only = 1;
    catalog.command_db_version = 1u;
    catalog.count = 1u;
    (void)snprintf(catalog.commands[0].command_id,
                   sizeof(catalog.commands[0].command_id), "TEST_A");
    catalog.commands[0].test_code = 5u;
    catalog.commands[0].min_parameter = 1;
    catalog.commands[0].max_parameter = 10;
    catalog.commands[0].require_set = 1u;
    catalog.commands[0].risk = GS_TEST_TC_DANGEROUS;
    request.step_id = 1u;
    (void)snprintf(request.command_id, sizeof(request.command_id), "TEST_A");
    request.parameter = 5;
    request.recent_state = 1u;
    request.command_db_version = 1u;
    request.send_at_utc_s = 1020u;
    (void)snprintf(request.author, sizeof(request.author), "operator.one");
    (void)snprintf(author.actor, sizeof(author.actor), "operator.one");
    (void)snprintf(peer.actor, sizeof(peer.actor), "operator.two");
    (void)snprintf(lead.actor, sizeof(lead.actor), "lead.one");
    (void)snprintf(no_reauth.actor, sizeof(no_reauth.actor), "lead.one");
    check_validation(data_path, manifest_path, passes, commands, profile,
                     &catalog, &request, &ticket);
    expect(gs_ros_sign_test_tc(&ticket, 77u, 1u, key, sizeof(key), frame) != 0,
           "signer denies pending approval");
    expect(gs_ros_tc_approve_test(&ticket, &author, 100u) != 0,
           "dangerous TC self approval denied");
    expect(gs_ros_tc_approve_test(&ticket, &peer, 100u) != 0,
           "dangerous TC peer role denied");
    expect(gs_ros_tc_approve_test(&ticket, &no_reauth, 100u) != 0,
           "dangerous TC missing reauthentication denied");
    expect(gs_ros_tc_approve_test(&ticket, &lead, 1021u) != 0,
           "approval after scheduled send time denied");
    expect(gs_ros_tc_approve_test(&ticket, &lead, 100u) == 0,
           "dangerous TC separate lead approved");
    expect(gs_ros_tc_approve_test(&ticket, &lead, 101u) != 0,
           "duplicate approval denied");
    expect(gs_ros_sign_test_tc(&ticket, 77u, 1u, key, sizeof(key), frame) == 0,
           "isolated test signer produces tagged frame");
    expect(gs_dgs_tc_gate_check_test(&gate, frame, sizeof(frame), 0, 1020u) != 0,
           "gate denies unauthenticated source");
    expect(gs_dgs_tc_gate_check_test(&gate, frame, sizeof(frame) - 1u, 1,
                                     1020u) != 0,
           "gate denies malformed length");
    expect(gs_dgs_tc_gate_check_test(&gate, frame, sizeof(frame), 1, 1110u) != 0,
           "gate denies outside pass");
    for (index = 0u; index < sizeof(frame); ++index) altered[index] = frame[index];
    altered[15] ^= 1u;
    expect(gs_dgs_tc_gate_check_test(&gate, altered, sizeof(altered), 1,
                                     1020u) != 0,
           "gate denies wrong SCID");
    for (index = 0u; index < sizeof(frame); ++index) altered[index] = frame[index];
    altered[55] ^= 1u;
    expect(gs_dgs_tc_gate_check_test(&gate, altered, sizeof(altered), 1,
                                     1020u) != 0,
           "gate denies command DB version mismatch");
    expect(gs_dgs_tc_gate_check_test(&gate, frame, sizeof(frame), 1, 1020u) == 0,
           "gate admits approved synthetic frame");
    expect(gs_dgs_tc_gate_check_test(&gate, frame, sizeof(frame), 1, 1020u) != 0,
           "gate rate and replay block second frame");
    for (index = 0u; index < sizeof(frame); ++index) altered[index] = frame[index];
    altered[48] ^= 1u;
    expect(gs_fake_sat_receive_test(&sat, altered, sizeof(altered), key,
                                    sizeof(key), &ack) != 0,
           "fake satellite denies changed authenticated content");
    expect(gs_fake_sat_receive_test(&sat, frame, sizeof(frame), key,
                                    sizeof(key), &ack) == 0 &&
           ack.accepted == 1 && ack.sequence == 1u && ack.test_code == 5u,
           "fake satellite verifies tag and returns ACK");
    expect(gs_fake_sat_receive_test(&sat, frame, sizeof(frame), key,
                                    sizeof(key), &ack) != 0 &&
           ack.accepted == 0,
           "fake satellite denies replay");
    catalog.commands[0].risk = GS_TEST_TC_NORMAL;
    expect(gs_ros_tc_validate_test(data_path, manifest_path, passes, commands,
            profile, &catalog, &request, &ticket) == 0 &&
           gs_ros_tc_approve_test(&ticket, &author, 200u) == 0,
           "normal TC allows author confirmation");
    catalog.commands[0].risk = GS_TEST_TC_CAUTION;
    expect(gs_ros_tc_validate_test(data_path, manifest_path, passes, commands,
            profile, &catalog, &request, &ticket) == 0 &&
           gs_ros_tc_approve_test(&ticket, &author, 300u) != 0 &&
           gs_ros_tc_approve_test(&ticket, &peer, 300u) == 0,
           "caution TC requires different peer");
}

int main(int argc, char **argv)
{
    gs_mps_plan_store_t *store;
    gs_mps_pass_catalog_t passes = {0};
    gs_mps_command_catalog_t commands = {1u, 0u, 0};
    gs_mps_test_profile_t profile = {1u, 8u, 2u, 1};
    char data_path[600];
    char manifest_path[600];
    if (argc != 2) return 2;
    store = calloc(1u, sizeof(*store));
    if (store == NULL) return 2;
    if (make_sequence(argv[1], data_path, manifest_path, store, &passes,
                      &commands, &profile) != 0) {
        free(store);
        return 2;
    }
    check_pipeline(data_path, manifest_path, &passes, &commands, &profile);
    (void)remove(data_path);
    (void)remove(manifest_path);
    free(store);
    return failures == 0 ? 0 : 1;
}
