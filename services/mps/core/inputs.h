#ifndef GS_MPS_INPUTS_H
#define GS_MPS_INPUTS_H

#include <stddef.h>
#include <stdint.h>

#define GS_MPS_COMMAND_DB_MAX_BYTES 4096u
#define GS_MPS_MAX_PASSES 128u
#define GS_MPS_PASS_ID_SIZE 64u

typedef struct {
    unsigned int version;
    unsigned int command_count;
    int operational;
} gs_mps_command_catalog_t;

typedef struct {
    char pass_id[GS_MPS_PASS_ID_SIZE];
    int64_t aos_utc_s;
    int64_t los_utc_s;
    uint32_t guard_before_s;
    uint32_t guard_after_s;
} gs_mps_pass_t;

typedef struct {
    gs_mps_pass_t passes[GS_MPS_MAX_PASSES];
    size_t count;
} gs_mps_pass_catalog_t;

/* Only the current non-operational DB envelope is accepted until the ICD is approved. */
int gs_mps_command_catalog_parse(const char *json, size_t length,
                                 gs_mps_command_catalog_t *out);

/* Ordered pass model; the FDS importer validates the N13 file pair. */
int gs_mps_pass_catalog_add(gs_mps_pass_catalog_t *catalog,
                            const gs_mps_pass_t *pass);
int gs_mps_pass_catalog_get(const gs_mps_pass_catalog_t *catalog, size_t index,
                            gs_mps_pass_t *out);

#endif
