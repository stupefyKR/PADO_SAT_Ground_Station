#include "gs/exchange.h"
#include "gs/hash.h"
#include "gs/log.h"
#include "gs/manifest.h"
#include "cJSON.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;

static void expect(int condition, const char *name)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", name);
        ++failures;
    }
}

static int write_file(const char *path, const char *contents)
{
    FILE *file;
    size_t length = strlen(contents);
    int result;
#ifdef _WIN32
    if (fopen_s(&file, path, "wb") != 0) return -1;
#else
    file = fopen(path, "wb");
#endif
    if (file == NULL) return -1;
    result = fwrite(contents, 1u, length, file) == length ? 0 : -1;
    if (fclose(file) != 0) result = -1;
    return result;
}

static void test_hash(void)
{
    char digest[GS_SHA256_HEX_SIZE];
    const char *abc = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
    expect(gs_sha256_bytes((const unsigned char *)"abc", 3u, digest) == 0, "hash bytes");
    expect(strcmp(digest, abc) == 0, "hash known vector");
    expect(gs_sha256_file("stage1-data.tmp", digest) == 0, "hash file");
    expect(strcmp(digest, abc) == 0, "hash file vector");
    expect(gs_sha256_file("absent-file", digest) != 0, "missing hash file");
}

static void make_manifest(char *buffer, size_t capacity, const char *file,
                          const char *type, const char *sha)
{
    int written = snprintf(buffer, capacity,
        "{\"file\":\"%s\",\"type\":\"%s\",\"from\":\"mps\","
        "\"to\":\"ros\",\"version\":3,\"created\":\"2026-10-21T01:00:00Z\","
        "\"sha256\":\"%s\"}", file, type, sha);
    expect(written > 0 && (size_t)written < capacity, "manifest fixture size");
}

static void test_manifest(void)
{
    const char *const allowed[] = {"MISSION_SCHEDULE"};
    const char *digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
    char json[1024];
    gs_manifest_t manifest;
    make_manifest(json, sizeof(json), "stage1-data.tmp", "MISSION_SCHEDULE", digest);
    expect(gs_manifest_parse(json, strlen(json), &manifest) == 0, "valid manifest");
    expect(gs_manifest_verify(&manifest, "mps", "ros", allowed, 1u,
                              "stage1-data.tmp") == 0, "valid manifest payload");
    expect(gs_manifest_verify(&manifest, "ros", "mps", allowed, 1u,
                              "stage1-data.tmp") != 0, "wrong route");
    expect(gs_manifest_verify(&manifest, "mps", "ros", allowed, 0u,
                              "stage1-data.tmp") != 0, "disallowed type");
    expect(gs_manifest_verify(&manifest, "mps", "ros", allowed, 1u,
                              "absent-file") != 0, "missing payload");
    manifest.file[0] = 'X';
    expect(gs_manifest_verify(&manifest, "mps", "ros", allowed, 1u,
                              "stage1-data.tmp") != 0, "manifest filename mismatch");
    manifest.file[0] = 's';
    manifest.sha256[0] = '0';
    expect(gs_manifest_verify(&manifest, "mps", "ros", allowed, 1u,
                              "stage1-data.tmp") != 0, "hash mismatch");
    make_manifest(json, sizeof(json), "../escape", "MISSION_SCHEDULE", digest);
    expect(gs_manifest_parse(json, strlen(json), &manifest) != 0, "path traversal");
    make_manifest(json, sizeof(json), "stage1-data.tmp", "BAD TYPE", digest);
    expect(gs_manifest_parse(json, strlen(json), &manifest) != 0, "invalid type");
    make_manifest(json, sizeof(json), "stage1-data.tmp", "MISSION_SCHEDULE", "abc");
    expect(gs_manifest_parse(json, strlen(json), &manifest) != 0, "short digest");
    make_manifest(json, sizeof(json), "stage1-data.tmp", "MISSION_SCHEDULE", digest);
    expect(gs_manifest_parse(json, strlen(json) - 1u, &manifest) != 0, "truncated JSON");
    {
        char *date = strstr(json, "2026-10-21");
        expect(date != NULL, "date fixture");
        if (date != NULL) {
            memcpy(date, "2026-02-31", 10u);
            expect(gs_manifest_parse(json, strlen(json), &manifest) != 0, "invalid date");
        }
    }
    expect(gs_manifest_parse("{}{}", 4u, &manifest) != 0, "trailing JSON");
    {
        const char *duplicate = "{\"file\":\"a\",\"file\":\"b\"}";
        expect(gs_manifest_parse(duplicate, strlen(duplicate), &manifest) != 0,
               "duplicate field");
    }
    expect(gs_manifest_parse("\0", 1u, &manifest) != 0, "embedded NUL");
    expect(gs_manifest_parse("{\"file\":\"a\\u0000b\"}",
                             strlen("{\"file\":\"a\\u0000b\"}"), &manifest) != 0,
           "escaped Unicode NUL");
    {
        char oversized[GS_MANIFEST_MAX_BYTES + 1u];
        memset(oversized, 'x', sizeof(oversized));
        expect(gs_manifest_parse(oversized, sizeof(oversized), &manifest) != 0,
               "oversized manifest");
    }
}

static void test_log(void)
{
    char line[2048];
    FILE *file = tmpfile();
    cJSON *object;
    expect(file != NULL, "open log file");
    if (file == NULL) return;
    expect(gs_log_write(file, "2026-10-21T01:00:00Z", "ros-core", "Z2",
                        "received", "line1\nline2") == 0, "write log");
    expect(fseek(file, 0, SEEK_SET) == 0, "rewind log");
    expect(fgets(line, sizeof(line), file) != NULL, "read log");
    object = cJSON_Parse(line);
    expect(cJSON_IsObject(object), "log JSON object");
    expect(cJSON_IsString(cJSON_GetObjectItemCaseSensitive(object, "detail")), "log detail");
    if (cJSON_IsString(cJSON_GetObjectItemCaseSensitive(object, "detail"))) {
        expect(strcmp(cJSON_GetObjectItemCaseSensitive(object, "detail")->valuestring,
                      "line1\nline2") == 0, "log escaping");
    }
    cJSON_Delete(object);
    expect(fgets(line, sizeof(line), file) == NULL, "single log line");
    expect(fclose(file) == 0, "close log");
}

static void test_exchange(void)
{
    const char *const allowed[] = {"MISSION_SCHEDULE"};
    const char *digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
    char json[1024];
    make_manifest(json, sizeof(json), "stage1-data.ready", "MISSION_SCHEDULE", digest);
    expect(write_file("stage1-manifest.partial", json) == 0, "write manifest temp");
    expect(gs_exchange_publish_pair("stage1-data.tmp", "stage1-data.ready",
                                    "stage1-manifest.partial", "stage1-manifest.ready") == 0,
           "publish pair");
    expect(gs_exchange_verify_pair("stage1-data.ready", "stage1-manifest.ready",
                                   "mps", "ros", allowed, 1u) == 0, "verify exchange pair");
    expect(gs_exchange_verify_pair("stage1-data.ready", "stage1-manifest.ready",
                                   "ros", "mps", allowed, 1u) != 0, "reject wrong sender");
    expect(gs_exchange_verify_pair("stage1-data.ready", "absent-file",
                                   "mps", "ros", allowed, 1u) != 0, "reject absent manifest");
    expect(gs_exchange_publish("stage1-data.ready", "stage1-manifest.ready") != 0,
           "cannot overwrite published file");
    expect(gs_exchange_publish("absent-file", "stage1-other.ready") != 0,
           "cannot publish missing file");
    expect(write_file("stage1-data.ready", "tampered") == 0, "tamper payload");
    expect(gs_exchange_verify_pair("stage1-data.ready", "stage1-manifest.ready",
                                   "mps", "ros", allowed, 1u) != 0, "reject tampered payload");
    expect(remove("stage1-data.ready") == 0, "remove published data");
    expect(remove("stage1-manifest.ready") == 0, "remove published manifest");
}

int main(void)
{
    expect(write_file("stage1-data.tmp", "abc") == 0, "write payload fixture");
    test_hash();
    test_manifest();
    test_log();
    test_exchange();
    if (failures != 0) fprintf(stderr, "%d tests failed\n", failures);
    return failures == 0 ? 0 : 1;
}
