#include "fds_schedule.h"

#include "gs/hash.h"
#include "gs/manifest.h"

#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char header[] =
    "schema,station_id,band,pass_id,aos_utc,los_utc,peak_utc,"
    "max_elevation_deg,minimum_elevation_deg,clipped_start,clipped_end";

static int valid_schedule_name(const char *name)
{
    size_t length = strlen(name);
    return strcmp(name, "contact_schedule.csv") == 0 ||
           (length > strlen("contact_schedule-fds-.csv") && length <= 200u &&
            strncmp(name, "contact_schedule-fds-", 21u) == 0 &&
            strcmp(name + length - 4u, ".csv") == 0);
}

static int digits(const char *text, size_t start, size_t count)
{
    size_t index;
    int value = 0;
    for (index = start; index < start + count; ++index) {
        if (text[index] < '0' || text[index] > '9') return -1;
        value = value * 10 + text[index] - '0';
    }
    return value;
}

static int leap(int year)
{
    return year % 400 == 0 || (year % 4 == 0 && year % 100 != 0);
}

static int64_t days_from_civil(int year, unsigned int month, unsigned int day)
{
    int era;
    unsigned int year_of_era;
    unsigned int day_of_year;
    unsigned int year_of_cycle;
    year -= month <= 2u;
    era = year / 400;
    year_of_era = (unsigned int)(year - era * 400);
    day_of_year = (153u * (month > 2u ? month - 3u : month + 9u) + 2u) / 5u + day - 1u;
    year_of_cycle = year_of_era * 365u + year_of_era / 4u - year_of_era / 100u + day_of_year;
    return (int64_t)era * 146097 + (int64_t)year_of_cycle - 719468;
}

static int utc_millis(const char *value, int64_t *out)
{
    static const int month_days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    int year, month, day, hour, minute, second, milli;
    int maximum;
    if (strlen(value) != 24u || value[4] != '-' || value[7] != '-' ||
            value[10] != 'T' || value[13] != ':' || value[16] != ':' ||
            value[19] != '.' || value[23] != 'Z') return -1;
    year = digits(value, 0u, 4u);
    month = digits(value, 5u, 2u);
    day = digits(value, 8u, 2u);
    hour = digits(value, 11u, 2u);
    minute = digits(value, 14u, 2u);
    second = digits(value, 17u, 2u);
    milli = digits(value, 20u, 3u);
    if (year < 1970 || month < 1 || month > 12 || hour < 0 || hour > 23 ||
            minute < 0 || minute > 59 || second < 0 || second > 59 || milli < 0)
        return -1;
    maximum = month_days[month - 1] + (month == 2 && leap(year));
    if (day < 1 || day > maximum) return -1;
    *out = (((days_from_civil(year, (unsigned int)month, (unsigned int)day) * 24 + hour) *
             60 + minute) * 60 + second) * 1000 + milli;
    return 0;
}

static int angle(const char *text, double *out)
{
    char *end;
    double value;
    errno = 0;
    value = strtod(text, &end);
    if (errno != 0 || end == text || *end != '\0' || !isfinite(value) ||
            value < 0.0 || value > 90.0) return -1;
    *out = value;
    return 0;
}

static int pass_row(char *line, gs_mps_fds_schedule_t *schedule)
{
    char *fields[11];
    size_t count = 0u;
    char *cursor = line;
    int64_t aos, los, peak;
    double maximum, minimum;
    gs_mps_pass_t pass = {0};
    gs_mps_fds_pass_info_t *info;
    size_t position;
    size_t index;
    while (count < 11u) {
        char *comma;
        fields[count++] = cursor;
        comma = strchr(cursor, ',');
        if (comma == NULL) break;
        *comma = '\0';
        cursor = comma + 1;
    }
    if (count != 11u || strchr(fields[10], ',') != NULL ||
            strcmp(fields[0], "fds.contact-schedule.v1") != 0 ||
            !((strcmp(fields[1], "yonsei") == 0 && strcmp(fields[2], "UHF") == 0) ||
              (strcmp(fields[1], "kari") == 0 && strcmp(fields[2], "S_BAND") == 0)) ||
            strlen(fields[3]) >= sizeof(pass.pass_id) ||
            strncmp(fields[3], fields[1], strlen(fields[1])) != 0 ||
            fields[3][strlen(fields[1])] != '-' ||
            utc_millis(fields[4], &aos) != 0 ||
            utc_millis(fields[5], &los) != 0 ||
            utc_millis(fields[6], &peak) != 0 ||
            angle(fields[7], &maximum) != 0 || angle(fields[8], &minimum) != 0 ||
            maximum < minimum || aos >= peak || peak >= los ||
            los - aos > 72LL * 3600LL * 1000LL ||
            (strcmp(fields[9], "true") != 0 && strcmp(fields[9], "false") != 0) ||
            (strcmp(fields[10], "true") != 0 && strcmp(fields[10], "false") != 0))
        return -1;
    memcpy(pass.pass_id, fields[3], strlen(fields[3]) + 1u);
    pass.aos_utc_s = aos / 1000;
    pass.los_utc_s = (los + 999) / 1000;
    if (gs_mps_pass_catalog_add(&schedule->passes, &pass) != 0) return -1;
    for (position = 0u; position < schedule->passes.count; ++position)
        if (strcmp(schedule->passes.passes[position].pass_id, pass.pass_id) == 0) break;
    for (index = schedule->passes.count - 1u; index > position; --index)
        schedule->info[index] = schedule->info[index - 1u];
    info = &schedule->info[position];
    *info = (gs_mps_fds_pass_info_t){0};
    memcpy(info->pass_id, pass.pass_id, strlen(pass.pass_id) + 1u);
    memcpy(info->station_id, fields[1], strlen(fields[1]) + 1u);
    memcpy(info->band, fields[2], strlen(fields[2]) + 1u);
    info->peak_utc_s = peak / 1000;
    info->max_elevation_deg = maximum;
    info->minimum_elevation_deg = minimum;
    info->clipped_start = strcmp(fields[9], "true") == 0;
    info->clipped_end = strcmp(fields[10], "true") == 0;
    return 0;
}

int gs_mps_fds_schedule_parse(const char *csv, size_t csv_length,
                              const char *manifest_json, size_t manifest_length,
                              gs_mps_fds_schedule_t *out)
{
    gs_manifest_t manifest;
    gs_mps_fds_schedule_t *parsed;
    char digest[GS_SHA256_HEX_SIZE];
    char *copy;
    char *cursor;
    char *end;
    size_t rows = 0u;
    int result = -1;
    if (csv == NULL || manifest_json == NULL || out == NULL ||
            csv_length == 0u || csv_length > GS_MPS_FDS_SCHEDULE_MAX_BYTES ||
            manifest_length == 0u || manifest_length > GS_MANIFEST_MAX_BYTES ||
            memchr(csv, '\0', csv_length) != NULL ||
            gs_manifest_parse(manifest_json, manifest_length, &manifest) != 0 ||
            !valid_schedule_name(manifest.file) ||
            strcmp(manifest.type, "CONTACT_SCHEDULE") != 0 ||
            strcmp(manifest.from, "fds") != 0 || strcmp(manifest.to, "mps") != 0 ||
            manifest.version != 1u ||
            gs_sha256_bytes((const unsigned char *)csv, csv_length, digest) != 0 ||
            strcmp(digest, manifest.sha256) != 0) return -1;
    copy = malloc(csv_length + 1u);
    parsed = calloc(1u, sizeof(*parsed));
    if (copy == NULL || parsed == NULL) goto done;
    memcpy(copy, csv, csv_length);
    copy[csv_length] = '\0';
    cursor = copy;
    end = strchr(cursor, '\n');
    if (end == NULL || (size_t)(end - cursor) != strlen(header) ||
            memcmp(cursor, header, strlen(header)) != 0) goto done;
    cursor = end + 1;
    while (*cursor != '\0') {
        end = strchr(cursor, '\n');
        if (end == NULL || end == cursor || end - cursor > 512 ||
                ++rows > GS_MPS_MAX_PASSES) goto done;
        if (end > cursor && end[-1] == '\r') end[-1] = '\0';
        *end = '\0';
        if (pass_row(cursor, parsed) != 0) goto done;
        cursor = end + 1;
    }
    if (parsed->passes.count > 1u &&
            parsed->passes.passes[parsed->passes.count - 1u].los_utc_s -
            parsed->passes.passes[0].aos_utc_s > 72LL * 3600LL + 2LL) goto done;
    memcpy(parsed->sha256, digest, sizeof(digest));
    parsed->non_operational = 1;
    *out = *parsed;
    result = 0;
done:
    free(copy);
    free(parsed);
    return result;
}

static int read_bounded(const char *path, size_t limit, char **data, size_t *length)
{
    FILE *file;
    char *buffer;
    size_t count;
    int failed;
    if (path == NULL || data == NULL || length == NULL) return -1;
#ifdef _WIN32
    if (fopen_s(&file, path, "rb") != 0) return -1;
#else
    file = fopen(path, "rb");
#endif
    if (file == NULL) return -1;
    buffer = malloc(limit + 1u);
    if (buffer == NULL) {
        (void)fclose(file);
        return -1;
    }
    count = fread(buffer, 1u, limit + 1u, file);
    failed = ferror(file) || count == 0u || count > limit;
    if (fclose(file) != 0) failed = 1;
    if (failed) {
        free(buffer);
        return -1;
    }
    *data = buffer;
    *length = count;
    return 0;
}

static int basename_is(const char *path, const char *expected)
{
    const char *name;
    const char *separator;
    if (path == NULL) return 0;
    name = path;
    separator = strrchr(name, '/');
    if (separator != NULL) name = separator + 1;
    separator = strrchr(name, '\\');
    if (separator != NULL) name = separator + 1;
    return strcmp(name, expected) == 0;
}

int gs_mps_fds_schedule_load_pair(const char *csv_path,
                                  const char *manifest_path,
                                  gs_mps_fds_schedule_t *out)
{
    char *csv = NULL;
    char *manifest = NULL;
    gs_manifest_t metadata;
    char expected_manifest_name[sizeof(metadata.file) + 16u];
    size_t csv_length, manifest_length;
    int result = -1;
    if (out == NULL || read_bounded(csv_path, GS_MPS_FDS_SCHEDULE_MAX_BYTES,
                                     &csv, &csv_length) != 0) return -1;
    if (read_bounded(manifest_path, GS_MANIFEST_MAX_BYTES,
                     &manifest, &manifest_length) == 0 &&
            gs_manifest_parse(manifest, manifest_length, &metadata) == 0 &&
            valid_schedule_name(metadata.file) &&
            snprintf(expected_manifest_name, sizeof(expected_manifest_name),
                     "%s.manifest.json", metadata.file) > 0 &&
            basename_is(csv_path, metadata.file) &&
            basename_is(manifest_path, expected_manifest_name))
        result = gs_mps_fds_schedule_parse(csv, csv_length, manifest,
                                           manifest_length, out);
    free(csv);
    free(manifest);
    return result;
}
