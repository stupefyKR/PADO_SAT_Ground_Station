#include "fds_schedule.h"
#include "tc_selection.h"
#include "gs/hash.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures;

static const char schedule_header[] =
    "schema,station_id,band,pass_id,aos_utc,los_utc,peak_utc,"
    "max_elevation_deg,minimum_elevation_deg,clipped_start,clipped_end\n";
static const char row_yonsei[] =
    "fds.contact-schedule.v1,yonsei,UHF,yonsei-001,2026-10-23T10:00:00.125Z,"
    "2026-10-23T10:08:00.250Z,2026-10-23T10:04:00.000Z,42.5,10.0,false,false\n";
static const char row_kari[] =
    "fds.contact-schedule.v1,kari,S_BAND,kari-001,2026-10-23T09:00:00.000Z,"
    "2026-10-23T09:10:00.000Z,2026-10-23T09:05:00.000Z,65.0,10.0,false,false\n";

static void expect(int condition, const char *label)
{
    if (!condition) {
        (void)fprintf(stderr, "FAIL: %s\n", label);
        ++failures;
    }
}

static FILE *open_write(const char *path)
{
    FILE *file = NULL;
#ifdef _WIN32
    if (fopen_s(&file, path, "wb") != 0) return NULL;
#else
    file = fopen(path, "wb");
#endif
    return file;
}

static void make_manifest(const char *csv, const char *kind, const char *recipient,
                          char *buffer, size_t capacity)
{
    char sha[GS_SHA256_HEX_SIZE];
    int written;
    if (gs_sha256_bytes((const unsigned char *)csv, strlen(csv), sha) != 0) {
        expect(0, "fixture hash");
        return;
    }
    written = snprintf(buffer, capacity,
                       "{\"file\":\"contact_schedule.csv\",\"type\":\"%s\","
                       "\"from\":\"fds\",\"to\":\"%s\",\"version\":1,"
                       "\"created\":\"2026-10-23T10:00:00Z\",\"sha256\":\"%s\"}",
                       kind, recipient, sha);
    expect(written > 0 && (size_t)written < capacity, "manifest fixture capacity");
}

static void test_schedule(const char *directory)
{
    char csv[2048];
    char altered[2048];
    char manifest[512];
    char path[1024];
    char manifest_path[1024];
    gs_mps_fds_schedule_t *schedule = calloc(1u, sizeof(*schedule));
    gs_mps_tc_selection_store_t *selection = calloc(1u, sizeof(*selection));
    gs_mps_tc_selection_revision_t *latest = calloc(1u, sizeof(*latest));
    uint32_t revision = 0u;
    FILE *file;
    expect(schedule != NULL && selection != NULL && latest != NULL,
           "allocate bounded MPS test state");
    if (schedule == NULL || selection == NULL || latest == NULL) goto done;
    (void)snprintf(csv, sizeof(csv), "%s%s%s", schedule_header, row_yonsei, row_kari);
    make_manifest(csv, "CONTACT_SCHEDULE", "mps", manifest, sizeof(manifest));
    expect(gs_mps_fds_schedule_parse(csv, strlen(csv), manifest, strlen(manifest),
                                      schedule) == 0 &&
           schedule->passes.count == 2u && schedule->non_operational == 1,
           "accept FDS N13 snapshot");
    expect(strcmp(schedule->passes.passes[0].pass_id, "kari-001") == 0 &&
           strcmp(schedule->info[0].station_id, "kari") == 0 &&
           strcmp(schedule->passes.passes[1].pass_id, "yonsei-001") == 0 &&
           schedule->info[1].max_elevation_deg == 42.5,
           "sort two ground station passes and retain metadata");
    expect(schedule->passes.passes[1].aos_utc_s == 1792749600LL &&
           schedule->passes.passes[1].los_utc_s == 1792750081LL,
           "round fractional FDS boundaries outward to whole seconds");
    (void)snprintf(path, sizeof(path), "%s/contact_schedule.csv", directory);
    (void)snprintf(manifest_path, sizeof(manifest_path),
                   "%s/contact_schedule.csv.manifest.json", directory);
    file = open_write(path);
    expect(file != NULL, "open local schedule fixture");
    if (file != NULL) {
        expect(fwrite(csv, 1u, strlen(csv), file) == strlen(csv), "write schedule fixture");
        expect(fclose(file) == 0, "close schedule fixture");
    }
    file = open_write(manifest_path);
    expect(file != NULL, "open local manifest fixture");
    if (file != NULL) {
        expect(fwrite(manifest, 1u, strlen(manifest), file) == strlen(manifest),
               "write manifest fixture");
        expect(fclose(file) == 0, "close manifest fixture");
    }
    expect(gs_mps_fds_schedule_load_pair(path, manifest_path, schedule) == 0,
           "load completed local N13 file pair");
    (void)remove(path);
    (void)remove(manifest_path);

    expect(gs_mps_tc_selection_create(selection, schedule, "yonsei-001") == 0,
           "create non-operational TC selection for imported pass");
    expect(gs_mps_tc_selection_edit(selection, 1u, schedule, GS_MPS_TC_INSERT, 0u,
                                     "TC_POWER_STATUS", &revision) == 0 && revision == 2u,
           "append opaque candidate TC ID");
    expect(gs_mps_tc_selection_edit(selection, 2u, schedule, GS_MPS_TC_INSERT, 0u,
                                     "TC_PAYLOAD_PREP", &revision) == 0 && revision == 3u,
           "insert ordered candidate before existing entry");
    expect(gs_mps_tc_selection_latest(selection, latest) == 0 &&
           latest->non_operational == 1 && latest->candidate_count == 2u &&
           strcmp(latest->candidate_ids[0], "TC_PAYLOAD_PREP") == 0 &&
           strcmp(latest->candidate_ids[1], "TC_POWER_STATUS") == 0,
           "read ordered provisional selection");
    expect(gs_mps_tc_selection_edit(selection, 2u, schedule, GS_MPS_TC_REMOVE, 0u,
                                     NULL, &revision) != 0,
           "reject stale revision");
    expect(gs_mps_tc_selection_edit(selection, 3u, schedule, GS_MPS_TC_REPLACE, 1u,
                                     "TC_HEALTH_CHECK", &revision) == 0,
           "replace selected candidate");
    expect(gs_mps_tc_selection_edit(selection, 4u, schedule, GS_MPS_TC_REMOVE, 0u,
                                     NULL, &revision) == 0,
           "remove selected candidate");
    expect(gs_mps_tc_selection_latest(selection, latest) == 0 &&
           latest->candidate_count == 1u &&
           strcmp(latest->candidate_ids[0], "TC_HEALTH_CHECK") == 0 &&
           selection->history[2].candidate_count == 2u,
           "keep immutable selection revisions");
    expect(gs_mps_tc_selection_edit(selection, 5u, schedule, GS_MPS_TC_INSERT, 1u,
                                     "TC BAD", &revision) != 0,
           "reject invalid candidate reference");
    schedule->sha256[0] = schedule->sha256[0] == '0' ? '1' : '0';
    expect(gs_mps_tc_selection_edit(selection, 5u, schedule, GS_MPS_TC_INSERT, 1u,
                                     "TC_MORE", &revision) != 0,
           "reject changed FDS schedule hash");
    make_manifest(csv, "CONTACT_SCHEDULE", "mps", manifest, sizeof(manifest));
    expect(gs_mps_fds_schedule_parse(csv, strlen(csv), manifest, strlen(manifest),
                                      schedule) == 0, "restore schedule fixture");
    memcpy(altered, csv, strlen(csv) + 1u);
    altered[strlen(schedule_header) + 4u] = 'X';
    expect(gs_mps_fds_schedule_parse(altered, strlen(altered), manifest,
                                      strlen(manifest), schedule) != 0,
           "reject changed schedule bytes");
    make_manifest(csv, "PASS_INFORMATION", "mps", manifest, sizeof(manifest));
    expect(gs_mps_fds_schedule_parse(csv, strlen(csv), manifest,
                                      strlen(manifest), schedule) != 0,
           "reject wrong N13 type");
    make_manifest(csv, "CONTACT_SCHEDULE", "ros", manifest, sizeof(manifest));
    expect(gs_mps_fds_schedule_parse(csv, strlen(csv), manifest,
                                      strlen(manifest), schedule) != 0,
           "reject wrong receiver");
    (void)snprintf(altered, sizeof(altered), "%s%s%s", schedule_header,
                   row_yonsei, row_yonsei);
    make_manifest(altered, "CONTACT_SCHEDULE", "mps", manifest, sizeof(manifest));
    expect(gs_mps_fds_schedule_parse(altered, strlen(altered), manifest,
                                      strlen(manifest), schedule) != 0,
           "reject duplicate pass ID");
    (void)snprintf(altered, sizeof(altered), "%s%s", schedule_header, row_yonsei);
    {
        char *date = strstr(altered, "2026-10-23T10:00:00.125Z");
        if (date != NULL) date[5] = '2';
    }
    make_manifest(altered, "CONTACT_SCHEDULE", "mps", manifest, sizeof(manifest));
    expect(gs_mps_fds_schedule_parse(altered, strlen(altered), manifest,
                                      strlen(manifest), schedule) != 0,
           "reject invalid Gregorian UTC");
done:
    free(schedule);
    free(selection);
    free(latest);
}

int main(int argc, char **argv)
{
    if (argc != 2 && argc != 4) return 2;
    test_schedule(argv[1]);
    if (argc == 4) {
        gs_mps_fds_schedule_t *actual = calloc(1u, sizeof(*actual));
        expect(actual != NULL, "allocate actual FDS import state");
        if (actual != NULL) {
            expect(gs_mps_fds_schedule_load_pair(argv[2], argv[3], actual) == 0 &&
                   actual->non_operational == 1 &&
                   actual->passes.count <= GS_MPS_MAX_PASSES,
                   "import actual Java FDS schedule and manifest");
            free(actual);
        }
    }
    return failures == 0 ? 0 : 1;
}
