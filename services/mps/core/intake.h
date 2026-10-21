#ifndef GS_MPS_INTAKE_H
#define GS_MPS_INTAKE_H

#include "plan_store.h"

#include <stdint.h>

#define GS_MPS_REQUEST_ID_SIZE 64u
#define GS_MPS_PURPOSE_SIZE 128u
#define GS_MPS_FLATSAT_RECORD_SIZE 64u
#define GS_MPS_INTAKE_PATH_SIZE 512u

typedef enum {
    GS_MPS_ROLE_PLANNER = 1,
    GS_MPS_ROLE_LEAD = 2
} gs_mps_role_t;

typedef struct {
    const char *actor;
    gs_mps_role_t role;
    int reauthenticated;
} gs_mps_identity_t;

typedef enum {
    GS_MPS_INTAKE_PENDING = 0,
    GS_MPS_INTAKE_ACCEPTED = 1,
    GS_MPS_INTAKE_REJECTED = 2
} gs_mps_intake_status_t;

typedef struct {
    char data_path[GS_MPS_INTAKE_PATH_SIZE];
    char request_id[GS_MPS_REQUEST_ID_SIZE];
    char pass_id[GS_MPS_PASS_ID_SIZE];
    char sha256[65];
    unsigned int version;
    gs_mps_intake_status_t status;
    uint32_t linked_plan_revision;
    char decided_by[GS_MPS_ACTOR_SIZE];
    int test_only;
} gs_mps_mission_request_t;

typedef struct {
    char data_path[GS_MPS_INTAKE_PATH_SIZE];
    char sha256[65];
    char purpose[GS_MPS_PURPOSE_SIZE];
    char flatsat_record_id[GS_MPS_FLATSAT_RECORD_SIZE];
    char submitted_by[GS_MPS_ACTOR_SIZE];
    char decided_by[GS_MPS_ACTOR_SIZE];
    unsigned int version;
    int high_risk;
    int flatsat_verified;
    gs_mps_intake_status_t status;
    int test_only;
} gs_mps_uplink_intake_t;

/* Both manifests must describe the same bytes: SDC->archive and archive->MPS. */
int gs_mps_request_receive_test(const char *data_path,
                                 const char *upstream_manifest_path,
                                 const char *downstream_manifest_path,
                                 const char *request_id, const char *pass_id,
                                 const gs_mps_pass_catalog_t *passes,
                                 gs_mps_mission_request_t *out);
/* Accept links the opaque request to the current draft; it never creates TC. */
int gs_mps_request_decide_test(gs_mps_mission_request_t *request,
                                const gs_mps_plan_store_t *plans,
                                gs_mps_intake_status_t decision,
                                const gs_mps_identity_t *identity);

int gs_mps_uplink_receive_test(const char *data_path,
                                const char *upstream_manifest_path,
                                const char *downstream_manifest_path,
                                const char *independent_sha256,
                                const char *purpose, int high_risk,
                                int flatsat_verified, const char *flatsat_record_id,
                                const char *submitter,
                                gs_mps_uplink_intake_t *out);
int gs_mps_uplink_decide_test(gs_mps_uplink_intake_t *intake,
                               gs_mps_intake_status_t decision,
                               const gs_mps_identity_t *identity);

#endif
