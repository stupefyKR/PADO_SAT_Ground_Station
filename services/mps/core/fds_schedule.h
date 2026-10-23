#ifndef GS_MPS_FDS_SCHEDULE_H
#define GS_MPS_FDS_SCHEDULE_H

#include "inputs.h"

#include <stddef.h>

#define GS_MPS_FDS_SCHEDULE_MAX_BYTES 131072u

typedef struct {
    char pass_id[GS_MPS_PASS_ID_SIZE];
    char station_id[16];
    char band[16];
    int64_t peak_utc_s;
    double max_elevation_deg;
    double minimum_elevation_deg;
    int clipped_start;
    int clipped_end;
} gs_mps_fds_pass_info_t;

typedef struct {
    gs_mps_pass_catalog_t passes;
    gs_mps_fds_pass_info_t info[GS_MPS_MAX_PASSES];
    char sha256[65];
    int non_operational;
} gs_mps_fds_schedule_t;

/* Parse one immutable N13 body/manifest byte snapshot. No network or TC path. */
int gs_mps_fds_schedule_parse(const char *csv, size_t csv_length,
                              const char *manifest_json, size_t manifest_length,
                              gs_mps_fds_schedule_t *out);
/* Local completed N13 file pair loader; SFTP peer authentication is upstream. */
int gs_mps_fds_schedule_load_pair(const char *csv_path,
                                  const char *manifest_path,
                                  gs_mps_fds_schedule_t *out);

#endif
