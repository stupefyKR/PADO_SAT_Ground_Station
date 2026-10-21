#include "catalog.h"
#include "stage6_routes.h"
#include "tle.h"

#include "gs/hash.h"
#include "gs/manifest.h"
#include "cJSON.h"

#include <stdio.h>
#include <string.h>

static int failures;

static void check(int condition, const char *name)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", name);
        ++failures;
    }
}

static int make_path(char *out, size_t capacity, const char *directory,
                     const char *filename)
{
    int length = snprintf(out, capacity, "%s/%s", directory, filename);
    return length > 0 && (size_t)length < capacity ? 0 : -1;
}

static int write_fixture(const char *data_path, const char *manifest_path,
                         const char *file_name, const char *data,
                         const char *type, const char *from,
                         const char *to)
{
    char digest[GS_SHA256_HEX_SIZE];
    char manifest[GS_MANIFEST_MAX_BYTES];
    FILE *file;
    int length;
    int failed;
#ifdef _WIN32
    if (fopen_s(&file, data_path, "wb") != 0) return -1;
#else
    file = fopen(data_path, "wb");
#endif
    if (file == NULL) return -1;
    failed = fwrite(data, 1u, strlen(data), file) != strlen(data);
    if (fclose(file) != 0) failed = 1;
    if (failed || gs_sha256_file(data_path, digest) != 0) return -1;
    length = snprintf(manifest, sizeof(manifest),
                      "{\"file\":\"%s\",\"type\":\"%s\",\"from\":\"%s\","
                      "\"to\":\"%s\",\"version\":1,"
                      "\"created\":\"2026-10-21T00:00:00Z\","
                      "\"sha256\":\"%s\"}",
                      file_name, type, from, to, digest);
    if (length <= 0 || (size_t)length >= sizeof(manifest)) return -1;
#ifdef _WIN32
    if (fopen_s(&file, manifest_path, "wb") != 0) return -1;
#else
    file = fopen(manifest_path, "wb");
#endif
    if (file == NULL) return -1;
    if (fwrite(manifest, 1u, (size_t)length, file) != (size_t)length) {
        (void)fclose(file);
        return -1;
    }
    return fclose(file) == 0 ? 0 : -1;
}

static void test_routes(void)
{
    static const char *const ids[] = {"N05", "N08", "N09", "N10", "N11", "N12",
                                      "N13", "N14", "N15", "N16", "N17", "N18"};
    size_t index;
    for (index = 0u; index < sizeof(ids) / sizeof(ids[0]); ++index) {
        const gs_stage6_route_t *route = gs_stage6_route(ids[index]);
        check(route != NULL, "known N2 route");
        if (route != NULL) {
            check(gs_stage6_route_allows(route->id, route->data_from,
                                         route->data_to, route->types[0],
                                         route->connection_from,
                                         route->connection_to,
                                         route->direction) == 0,
                  "approved route and data type");
            check(gs_stage6_route_allows(route->id, route->data_from,
                                         route->data_to, "TC",
                                         route->connection_from,
                                         route->connection_to,
                                         route->direction) != 0,
                  "reject unrelated type");
        }
    }
    check(gs_stage6_route("N03") == NULL, "external internet route excluded");
    check(gs_stage6_route("N04") == NULL, "unknown ICD route excluded");
    check(gs_stage6_route_allows("N05", "dgs-net", "fds", "NORAD_TLE",
                                 "dgs-net", "fds", GS_STAGE6_PULL) != 0,
          "FDS must initiate TLE pull");
    check(gs_stage6_route_allows("N16", "sdc", "dgs-archive", "MISSION_REQUEST",
                                 "sdc", "ros", GS_STAGE6_PUSH) != 0,
          "SDC cannot address ROS");
}

static void test_n2_chart(const char *path)
{
    static const char *const ids[] = {"N05", "N08", "N09", "N10", "N11", "N12",
                                      "N13", "N14", "N15", "N16", "N17", "N18"};
    char json[16384];
    FILE *file;
    size_t length;
    int read_failed;
    cJSON *root;
    const cJSON *flows;
    size_t route_index;
#ifdef _WIN32
    if (fopen_s(&file, path, "rb") != 0) file = NULL;
#else
    file = fopen(path, "rb");
#endif
    check(file != NULL, "N2 chart readable");
    if (file == NULL) return;
    length = fread(json, 1u, sizeof(json) - 1u, file);
    read_failed = ferror(file);
    if (fclose(file) != 0) read_failed = 1;
    check(!read_failed && length < sizeof(json) - 1u, "N2 chart bounded");
    if (read_failed || length >= sizeof(json) - 1u) return;
    json[length] = '\0';
    root = cJSON_ParseWithLengthOpts(json, length + 1u, NULL, 1);
    check(root != NULL, "N2 chart JSON");
    if (root == NULL) return;
    flows = cJSON_GetObjectItemCaseSensitive(root, "approved_flows");
    check(cJSON_IsArray(flows), "N2 approved flows");
    for (route_index = 0u; route_index < sizeof(ids) / sizeof(ids[0]); ++route_index) {
        const gs_stage6_route_t *route = gs_stage6_route(ids[route_index]);
        int matches = 0;
        int flow_index;
        for (flow_index = 0; flow_index < cJSON_GetArraySize(flows); ++flow_index) {
            const cJSON *flow = cJSON_GetArrayItem(flows, flow_index);
            const cJSON *id = cJSON_GetObjectItemCaseSensitive(flow, "id");
            const cJSON *data_from = cJSON_GetObjectItemCaseSensitive(flow, "data_from");
            const cJSON *data_to = cJSON_GetObjectItemCaseSensitive(flow, "data_to");
            const cJSON *connection_from = cJSON_GetObjectItemCaseSensitive(flow, "connection_from");
            const cJSON *connection_to = cJSON_GetObjectItemCaseSensitive(flow, "connection_to");
            const cJSON *transport = cJSON_GetObjectItemCaseSensitive(flow, "transport");
            const cJSON *port = cJSON_GetObjectItemCaseSensitive(flow, "port");
            const cJSON *types = cJSON_GetObjectItemCaseSensitive(flow, "types");
            size_t type_index;
            if (!cJSON_IsString(id) || strcmp(id->valuestring, ids[route_index]) != 0)
                continue;
            ++matches;
            check(route != NULL && cJSON_IsString(data_from) &&
                  cJSON_IsString(data_to) && cJSON_IsString(connection_from) &&
                  cJSON_IsString(connection_to) && cJSON_IsString(transport) &&
                  cJSON_IsNumber(port) && cJSON_IsArray(types),
                  "N2 route fields");
            if (route == NULL || !cJSON_IsString(data_from) ||
                    !cJSON_IsString(data_to) || !cJSON_IsString(connection_from) ||
                    !cJSON_IsString(connection_to) || !cJSON_IsString(transport) ||
                    !cJSON_IsNumber(port) || !cJSON_IsArray(types)) continue;
            check(strcmp(route->data_from, data_from->valuestring) == 0 &&
                  strcmp(route->data_to, data_to->valuestring) == 0 &&
                  strcmp(route->connection_from, connection_from->valuestring) == 0 &&
                  strcmp(route->connection_to, connection_to->valuestring) == 0 &&
                  strcmp(transport->valuestring, route->direction == GS_STAGE6_PULL ?
                         "SFTP_PULL" : "SFTP_PUSH") == 0 &&
                  port->valuedouble == 2222.0 &&
                  cJSON_GetArraySize(types) == (int)route->type_count,
                  "N2 route matches implementation");
            for (type_index = 0u; type_index < route->type_count; ++type_index) {
                const cJSON *type = cJSON_GetArrayItem(types, (int)type_index);
                check(cJSON_IsString(type) &&
                      strcmp(type->valuestring, route->types[type_index]) == 0,
                      "N2 type matches implementation");
            }
        }
        check(matches == 1, "N2 route appears once");
    }
    cJSON_Delete(root);
}

static void set_checksum(char line[70])
{
    unsigned int sum = 0u;
    size_t index;
    for (index = 0u; index < 68u; ++index) {
        if (line[index] >= '0' && line[index] <= '9')
            sum += (unsigned int)(line[index] - '0');
        else if (line[index] == '-') ++sum;
    }
    line[68] = (char)('0' + sum % 10u);
}

static void test_tle(void)
{
    char tle[140];
    memset(tle, ' ', sizeof(tle));
    tle[0] = '1'; tle[1] = ' ';
    memcpy(tle + 2u, "12345", 5u);
    memcpy(tle + 18u, "26123", 5u);
    tle[69] = '\n';
    tle[70] = '2'; tle[71] = ' ';
    memcpy(tle + 72u, "12345", 5u);
    set_checksum(tle);
    set_checksum(tle + 70u);
    check(gs_dgs_net_check_tle(tle, 139u, 12345u) == 0,
          "syntactically valid TLE");
    check(gs_dgs_net_check_tle(tle, 139u, 54321u) != 0,
          "satellite allowlist");
    tle[138] = tle[138] == '0' ? '1' : '0';
    check(gs_dgs_net_check_tle(tle, 139u, 12345u) != 0,
          "checksum mismatch");
    check(gs_dgs_net_check_tle(tle, 138u, 12345u) != 0,
          "truncated line");
}

static void test_archive(const char *directory)
{
    gs_archive_catalog_t catalog = {0};
    gs_manifest_t forward;
    gs_manifest_t parsed;
    char data_path[512];
    char manifest_path[512];
    char json[GS_MANIFEST_MAX_BYTES];
    FILE *file;
    int length;
    check(make_path(data_path, sizeof(data_path), directory, "stage6-request.bin") == 0,
          "request path");
    check(make_path(manifest_path, sizeof(manifest_path), directory,
                    "stage6-request.bin.manifest.json") == 0, "request manifest path");
    check(write_fixture(data_path, manifest_path, "stage6-request.bin",
                        "test-only-request", "MISSION_REQUEST", "sdc",
                        "dgs-archive") == 0, "request fixture");
    check(gs_archive_ingest(&catalog, "N16", data_path, manifest_path, 1024u) == 0,
          "SDC input catalogued");
    check(catalog.count == 1u, "catalog count");
    check(gs_archive_ingest(&catalog, "N16", data_path, manifest_path, 1024u) != 0,
          "duplicate rejected");
    check(gs_archive_prepare_forward(&catalog, 0u, "N17", &forward) != 0,
          "mission request not exported to SDC");
    check(gs_archive_prepare_forward(&catalog, 0u, "N18", &forward) == 0,
          "mission request forwarded to MPS");
    length = gs_archive_manifest_json(&forward, json, sizeof(json));
    check(length > 0 && gs_manifest_parse(json, (size_t)length, &parsed) == 0 &&
          strcmp(parsed.from, "dgs-archive") == 0 &&
          strcmp(parsed.to, "mps") == 0, "forward manifest interoperable");
    check(gs_archive_ingest(&catalog, "N16", data_path, manifest_path, 2u) != 0,
          "oversized input rejected");
    check(make_path(data_path, sizeof(data_path), directory, "stage6-raw.bin") == 0,
          "raw path");
    check(make_path(manifest_path, sizeof(manifest_path), directory,
                    "stage6-raw.bin.manifest.json") == 0, "raw manifest path");
    check(write_fixture(data_path, manifest_path, "stage6-raw.bin", "raw-data",
                        "CREST_RAW", "dgs-gs", "dgs-archive") == 0,
          "raw fixture");
    check(gs_archive_ingest(&catalog, "N09", data_path, manifest_path, 1024u) == 0,
          "raw data catalogued");
    check(gs_archive_prepare_forward(&catalog, 1u, "N17", &forward) == 0,
          "raw data available to SDC");
    check(gs_archive_prepare_forward(&catalog, 1u, "N18", &forward) != 0,
          "raw data not sent to MPS");
#ifdef _WIN32
    if (fopen_s(&file, data_path, "ab") != 0) file = NULL;
#else
    file = fopen(data_path, "ab");
#endif
    check(file != NULL, "tamper open");
    if (file != NULL) {
        check(fwrite("x", 1u, 1u, file) == 1u && fclose(file) == 0,
              "tamper data");
        check(gs_archive_prepare_forward(&catalog, 1u, "N17", &forward) != 0,
              "changed data denied");
    }
}

int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    test_routes();
    test_n2_chart(argv[2]);
    test_tle();
    test_archive(argv[1]);
    return failures == 0 ? 0 : 1;
}
