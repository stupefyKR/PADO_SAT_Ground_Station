#include "stage6_routes.h"

#include <string.h>

#define COUNT(values) (sizeof(values) / sizeof((values)[0]))

static const char *const tle_sv[] = {"NORAD_TLE", "STATE_VECTOR"};
static const char *const pointing[] = {"ANTENNA_POINTING", "PASS_REQUEST"};
static const char *const raw[] = {"CREST_RAW", "SIREN_RAW"};
static const char *const ack_soh[] = {"ACK", "BUS_SOH"};
static const char *const gnss[] = {"GNSS"};
static const char *const mps_ros[] = {"MISSION_SCHEDULE", "TC_SEQUENCE", "APPROVED_UPLINK"};
static const char *const schedule[] = {"CONTACT_SCHEDULE", "ORBITAL_EVENT"};
static const char *const ros_sdc[] = {"SC_ATTITUDE", "BUS_SOH"};
static const char *const fds_sdc[] = {"ORBIT_EPHEMERIS", "PASS_INFORMATION"};
static const char *const sdc_in[] = {"MISSION_REQUEST", "UPLINK_FILE"};
static const char *const sdc_out[] = {"CREST_RAW", "SIREN_RAW", "SC_ATTITUDE",
                                      "BUS_SOH", "ORBIT_EPHEMERIS", "PASS_INFORMATION"};

static const gs_stage6_route_t routes[] = {
    {"N05", "dgs-net", "fds", "fds", "dgs-net", GS_STAGE6_PULL, tle_sv, COUNT(tle_sv)},
    {"N08", "fds", "dgs-gs", "fds", "dgs-gs", GS_STAGE6_PUSH, pointing, COUNT(pointing)},
    {"N09", "dgs-gs", "dgs-archive", "dgs-archive", "dgs-gs", GS_STAGE6_PULL, raw, COUNT(raw)},
    {"N10", "ros", "mps", "ros", "mps", GS_STAGE6_PUSH, ack_soh, COUNT(ack_soh)},
    {"N11", "ros", "fds", "ros", "fds", GS_STAGE6_PUSH, gnss, COUNT(gnss)},
    {"N12", "mps", "ros", "mps", "ros", GS_STAGE6_PUSH, mps_ros, COUNT(mps_ros)},
    {"N13", "fds", "mps", "fds", "mps", GS_STAGE6_PUSH, schedule, COUNT(schedule)},
    {"N14", "ros", "dgs-archive", "ros", "dgs-archive", GS_STAGE6_PUSH, ros_sdc, COUNT(ros_sdc)},
    {"N15", "fds", "dgs-archive", "fds", "dgs-archive", GS_STAGE6_PUSH, fds_sdc, COUNT(fds_sdc)},
    {"N16", "sdc", "dgs-archive", "sdc", "dgs-archive", GS_STAGE6_PUSH, sdc_in, COUNT(sdc_in)},
    {"N17", "dgs-archive", "sdc", "sdc", "dgs-archive", GS_STAGE6_PULL, sdc_out, COUNT(sdc_out)},
    {"N18", "dgs-archive", "mps", "dgs-archive", "mps", GS_STAGE6_PUSH, sdc_in, COUNT(sdc_in)}
};

const gs_stage6_route_t *gs_stage6_route(const char *id)
{
    size_t index;
    if (id == NULL) return NULL;
    for (index = 0u; index < COUNT(routes); ++index)
        if (strcmp(id, routes[index].id) == 0) return &routes[index];
    return NULL;
}

int gs_stage6_route_allows(const char *id, const char *data_from,
                           const char *data_to, const char *type,
                           const char *connection_from,
                           const char *connection_to,
                           gs_stage6_direction_t direction)
{
    const gs_stage6_route_t *route = gs_stage6_route(id);
    size_t index;
    if (route == NULL || data_from == NULL || data_to == NULL || type == NULL ||
            connection_from == NULL || connection_to == NULL ||
            strcmp(route->data_from, data_from) != 0 ||
            strcmp(route->data_to, data_to) != 0 ||
            strcmp(route->connection_from, connection_from) != 0 ||
            strcmp(route->connection_to, connection_to) != 0 ||
            route->direction != direction) return -1;
    for (index = 0u; index < route->type_count; ++index)
        if (strcmp(route->types[index], type) == 0) return 0;
    return -1;
}

int gs_stage6_upload(gs_sftp_client_t *client, const char *id,
                     const char *data_path, const char *manifest_path,
                     const char *remote_directory)
{
    const gs_stage6_route_t *route = gs_stage6_route(id);
    if (route == NULL || route->direction != GS_STAGE6_PUSH || client == NULL)
        return -1;
#ifdef _WIN32
    (void)data_path;
    (void)manifest_path;
    (void)remote_directory;
    return -1;
#else
    return gs_sftp_upload_pair(client, data_path, manifest_path,
                               remote_directory, route->data_from,
                               route->data_to, route->types, route->type_count);
#endif
}

int gs_stage6_download(gs_sftp_client_t *client, const char *id,
                       const char *remote_directory, const char *filename,
                       const char *local_directory, size_t max_data_bytes)
{
    const gs_stage6_route_t *route = gs_stage6_route(id);
    if (route == NULL || route->direction != GS_STAGE6_PULL || client == NULL ||
            max_data_bytes == 0u) return -1;
#ifdef _WIN32
    (void)remote_directory;
    (void)filename;
    (void)local_directory;
    return -1;
#else
    return gs_sftp_download_pair(client, remote_directory, filename,
                                 local_directory, route->data_from,
                                 route->data_to, route->types,
                                 route->type_count, max_data_bytes);
#endif
}
