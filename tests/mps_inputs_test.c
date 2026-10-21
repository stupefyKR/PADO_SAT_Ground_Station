#include "inputs.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures = 0;

static void expect(int condition, const char *label)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", label);
        ++failures;
    }
}

static void test_command_catalog(const char *path)
{
    static const char valid[] =
        "{\"version\":1,\"operational\":false,\"status\":\"TBD(ICD)\","
        "\"source\":\"not supplied\",\"commands\":[]}";
    static const char active[] =
        "{\"version\":1,\"operational\":true,\"status\":\"TBD(ICD)\","
        "\"source\":\"not supplied\",\"commands\":[]}";
    static const char unknown[] =
        "{\"version\":1,\"operational\":false,\"status\":\"TBD(ICD)\","
        "\"source\":\"not supplied\",\"commands\":[{\"cmd\":\"TEST\"}]}";
    static const char wrong_status[] =
        "{\"version\":1,\"operational\":false,\"status\":\"ready\","
        "\"source\":\"not supplied\",\"commands\":[]}";
    static const char fractional[] =
        "{\"version\":1.5,\"operational\":false,\"status\":\"TBD(ICD)\","
        "\"source\":\"not supplied\",\"commands\":[]}";
    static const char duplicate[] =
        "{\"version\":1,\"version\":2,\"operational\":false,"
        "\"status\":\"TBD(ICD)\",\"source\":\"x\",\"commands\":[]}";
    static const char escaped[] =
        "{\"version\":1,\"operational\":false,\"status\":\"TBD(ICD)\","
        "\"source\":\"bad\\u0000source\",\"commands\":[]}";
    gs_mps_command_catalog_t catalog = {99u, 99u, 1};
    char data[GS_MPS_COMMAND_DB_MAX_BYTES + 1u];
    FILE *file = fopen(path, "rb");
    size_t length;
    expect(file != NULL, "open repository command database");
    if (file == NULL) return;
    length = fread(data, 1u, sizeof(data), file);
    expect(fclose(file) == 0, "close command database");
    expect(length < sizeof(data), "database size bounded");
    expect(gs_mps_command_catalog_parse(data, length, &catalog) == 0 &&
           catalog.version == 1u && catalog.command_count == 0u &&
           catalog.operational == 0, "repository DB is non-operational and empty");
    expect(gs_mps_command_catalog_parse(valid, sizeof(valid) - 1u, &catalog) == 0,
           "accept non-operational envelope");
    expect(gs_mps_command_catalog_parse(active, sizeof(active) - 1u, &catalog) != 0,
           "reject operational DB without approved item schema");
    expect(gs_mps_command_catalog_parse(unknown, sizeof(unknown) - 1u, &catalog) != 0,
           "reject unknown command entries");
    expect(gs_mps_command_catalog_parse(wrong_status, sizeof(wrong_status) - 1u,
                                         &catalog) != 0, "reject unapproved status");
    expect(gs_mps_command_catalog_parse(fractional, sizeof(fractional) - 1u,
                                         &catalog) != 0, "reject fractional version");
    expect(gs_mps_command_catalog_parse(duplicate, sizeof(duplicate) - 1u, &catalog) != 0,
           "reject duplicate keys");
    expect(gs_mps_command_catalog_parse(escaped, sizeof(escaped) - 1u, &catalog) != 0,
           "reject escaped NUL");
    expect(gs_mps_command_catalog_parse(valid, 0u, &catalog) != 0,
           "reject empty input");
    expect(gs_mps_command_catalog_parse(valid, GS_MPS_COMMAND_DB_MAX_BYTES + 1u,
                                         &catalog) != 0, "reject oversized input");
    expect(gs_mps_command_catalog_parse(NULL, 1u, &catalog) != 0,
           "reject null input");
}

static void test_pass_catalog(void)
{
    gs_mps_pass_catalog_t catalog = {0};
    gs_mps_pass_t late = {"P-20261120-02", 2000, 2600, 30u, 30u};
    gs_mps_pass_t early = {"P-20261120-01", 1000, 1600, 30u, 30u};
    gs_mps_pass_t invalid;
    gs_mps_pass_t result = {0};
    expect(gs_mps_pass_catalog_add(&catalog, &late) == 0,
           "add bounded pass");
    expect(gs_mps_pass_catalog_add(&catalog, &early) == 0 && catalog.count == 2u,
           "sort passes by AOS");
    expect(gs_mps_pass_catalog_get(&catalog, 0u, &result) == 0 &&
           strcmp(result.pass_id, early.pass_id) == 0, "list first pass");
    expect(gs_mps_pass_catalog_add(&catalog, &early) != 0,
           "reject duplicate pass ID");
    invalid = early;
    invalid.pass_id[0] = '/';
    expect(gs_mps_pass_catalog_add(&catalog, &invalid) != 0,
           "reject invalid pass ID");
    invalid = early;
    memset(invalid.pass_id, 'A', sizeof(invalid.pass_id));
    expect(gs_mps_pass_catalog_add(&catalog, &invalid) != 0,
           "reject unterminated pass ID");
    invalid = early;
    invalid.los_utc_s = invalid.aos_utc_s;
    expect(gs_mps_pass_catalog_add(&catalog, &invalid) != 0,
           "reject reversed pass window");
    invalid = early;
    invalid.guard_before_s = 300u;
    invalid.guard_after_s = 300u;
    expect(gs_mps_pass_catalog_add(&catalog, &invalid) != 0,
           "reject no usable pass window");
    expect(gs_mps_pass_catalog_get(&catalog, 2u, &result) != 0,
           "reject out-of-range lookup");
    expect(gs_mps_pass_catalog_add(NULL, &early) != 0,
           "reject null catalog");
    catalog.count = GS_MPS_MAX_PASSES;
    expect(gs_mps_pass_catalog_add(&catalog, &late) != 0,
           "reject full pass catalog");
}

int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    test_command_catalog(argv[1]);
    test_pass_catalog();
    return failures == 0 ? 0 : 1;
}
