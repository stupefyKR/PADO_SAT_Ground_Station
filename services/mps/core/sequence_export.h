#ifndef GS_MPS_SEQUENCE_EXPORT_H
#define GS_MPS_SEQUENCE_EXPORT_H

#include "conflict.h"

#include <stddef.h>
#include <stdint.h>

#define GS_MPS_TEST_SEQUENCE_MAX_BYTES 32768u

typedef struct {
    gs_mps_plan_revision_t plan;
    gs_mps_test_profile_t profile;
    char approved_by[GS_MPS_ACTOR_SIZE];
    uint32_t approved_at_utc_s;
    int test_only;
} gs_mps_test_approval_t;

/* Caller-supplied role and reauthentication are trusted only in this test seam. */
int gs_mps_sequence_approve_test(const gs_mps_plan_store_t *store,
                                 uint32_t expected_revision,
                                 const gs_mps_pass_catalog_t *passes,
                                 const gs_mps_command_catalog_t *commands,
                                 const gs_mps_test_profile_t *profile,
                                 const char *approver, int lead_role,
                                 int reauthenticated, uint32_t approved_at_utc_s,
                                 gs_mps_test_approval_t *out);

/* Exports TEST_ONLY_SEQUENCE and its manifest; never TC_SEQUENCE. */
int gs_mps_sequence_export_test(const gs_mps_plan_store_t *store,
                                const gs_mps_pass_catalog_t *passes,
                                const gs_mps_command_catalog_t *commands,
                                const gs_mps_test_approval_t *approval,
                                const char *filename, const char *created_utc,
                                char *sequence, size_t sequence_capacity,
                                char *manifest, size_t manifest_capacity);

#endif
