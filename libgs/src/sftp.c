#define _POSIX_C_SOURCE 200809L

#include "gs/sftp.h"
#include "gs/exchange.h"
#include "gs/hash.h"
#include "gs/manifest.h"

#include <libssh/libssh.h>
#include <libssh/sftp.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

struct gs_sftp_client {
    ssh_session ssh;
    sftp_session sftp;
};

static int hex_digest(const char *text, unsigned char output[32])
{
    size_t index;
    if (text == NULL || strlen(text) != 64u) return -1;
    for (index = 0u; index < 32u; ++index) {
        int high;
        int low;
        char a = text[index * 2u];
        char b = text[index * 2u + 1u];
        high = a >= '0' && a <= '9' ? a - '0' : a >= 'a' && a <= 'f' ? a - 'a' + 10 : -1;
        low = b >= '0' && b <= '9' ? b - '0' : b >= 'a' && b <= 'f' ? b - 'a' + 10 : -1;
        if (high < 0 || low < 0) return -1;
        output[index] = (unsigned char)(high * 16 + low);
    }
    return 0;
}

static int host_key_matches(ssh_session session, const char *expected_hex)
{
    unsigned char expected[32];
    unsigned char *actual = NULL;
    ssh_key key = NULL;
    size_t length = 0u;
    unsigned char difference = 0u;
    size_t index;
    int result = -1;
    if (hex_digest(expected_hex, expected) != 0 ||
            ssh_get_server_publickey(session, &key) != SSH_OK) return -1;
    if (ssh_get_publickey_hash(key, SSH_PUBLICKEY_HASH_SHA256, &actual, &length) != SSH_OK ||
            length != sizeof(expected)) goto done;
    for (index = 0u; index < sizeof(expected); ++index)
        difference |= (unsigned char)(expected[index] ^ actual[index]);
    if (difference == 0u) result = 0;
done:
    ssh_clean_pubkey_hash(&actual);
    ssh_key_free(key);
    return result;
}

gs_sftp_client_t *gs_sftp_connect(const char *host, unsigned int port, const char *user,
                                  const char *private_key_path, const char *host_key_sha256)
{
    gs_sftp_client_t *client;
    ssh_key key = NULL;
    long timeout = 10;
    if (host == NULL || user == NULL || private_key_path == NULL ||
            host_key_sha256 == NULL || port == 0u || port > 65535u) return NULL;
    client = calloc(1u, sizeof(*client));
    if (client == NULL) return NULL;
    client->ssh = ssh_new();
    if (client->ssh == NULL ||
            ssh_options_set(client->ssh, SSH_OPTIONS_HOST, host) != SSH_OK ||
            ssh_options_set(client->ssh, SSH_OPTIONS_USER, user) != SSH_OK ||
            ssh_options_set(client->ssh, SSH_OPTIONS_PORT, &port) != SSH_OK ||
            ssh_options_set(client->ssh, SSH_OPTIONS_TIMEOUT, &timeout) != SSH_OK ||
            ssh_connect(client->ssh) != SSH_OK ||
            host_key_matches(client->ssh, host_key_sha256) != 0 ||
            ssh_pki_import_privkey_file(private_key_path, NULL, NULL, NULL, &key) != SSH_OK ||
            ssh_userauth_publickey(client->ssh, NULL, key) != SSH_AUTH_SUCCESS) goto fail;
    ssh_key_free(key);
    client->sftp = sftp_new(client->ssh);
    if (client->sftp == NULL || sftp_init(client->sftp) != SSH_OK) goto fail;
    return client;
fail:
    ssh_key_free(key);
    gs_sftp_disconnect(client);
    return NULL;
}

void gs_sftp_disconnect(gs_sftp_client_t *client)
{
    if (client == NULL) return;
    if (client->sftp != NULL) sftp_free(client->sftp);
    if (client->ssh != NULL) {
        ssh_disconnect(client->ssh);
        ssh_free(client->ssh);
    }
    free(client);
}

static int valid_filename(const char *name)
{
    const unsigned char *cursor;
    size_t length;
    if (name == NULL) return 0;
    length = strlen(name);
    if (length == 0u || length > 200u || strcmp(name, ".") == 0 ||
            strcmp(name, "..") == 0) return 0;
    for (cursor = (const unsigned char *)name; *cursor != '\0'; ++cursor) {
        if (!(*cursor >= 'A' && *cursor <= 'Z') &&
                !(*cursor >= 'a' && *cursor <= 'z') &&
                !(*cursor >= '0' && *cursor <= '9') &&
                *cursor != '.' && *cursor != '_' && *cursor != '-') return 0;
    }
    return 1;
}

static int make_path(char *out, size_t capacity, const char *directory,
                     const char *filename, const char *suffix)
{
    int written;
    if (out == NULL || directory == NULL || filename == NULL || suffix == NULL ||
            directory[0] != '/' || strstr(directory, "..") != NULL ||
            !valid_filename(filename)) return -1;
    written = snprintf(out, capacity, "%s/%s%s", directory, filename, suffix);
    return written >= 0 && (size_t)written < capacity ? 0 : -1;
}

static int remote_absent(sftp_session session, const char *path)
{
    sftp_attributes attributes = sftp_stat(session, path);
    if (attributes != NULL) {
        sftp_attributes_free(attributes);
        return 0;
    }
    return sftp_get_error(session) == SSH_FX_NO_SUCH_FILE;
}

static int upload_file(sftp_session session, const char *source, const char *destination)
{
    struct stat details;
    unsigned char buffer[32768];
    FILE *local = NULL;
    sftp_file remote = NULL;
    size_t count;
    int result = -1;
    if (lstat(source, &details) != 0 || !S_ISREG(details.st_mode)) return -1;
    local = fopen(source, "rb");
    if (local == NULL) return -1;
    remote = sftp_open(session, destination, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (remote == NULL) goto done;
    while ((count = fread(buffer, 1u, sizeof(buffer), local)) != 0u) {
        size_t done = 0u;
        while (done < count) {
            ssize_t written = sftp_write(remote, buffer + done, count - done);
            if (written <= 0) goto done;
            done += (size_t)written;
        }
    }
    if (!ferror(local)) result = 0;
done:
    if (remote != NULL && sftp_close(remote) != SSH_OK) result = -1;
    if (fclose(local) != 0) result = -1;
    if (result != 0) (void)sftp_unlink(session, destination);
    return result;
}

static int load_manifest(const char *path, gs_manifest_t *manifest)
{
    char json[GS_MANIFEST_MAX_BYTES + 1u];
    FILE *file = fopen(path, "rb");
    size_t count;
    int result;
    if (file == NULL) return -1;
    count = fread(json, 1u, sizeof(json), file);
    result = ferror(file) || count == sizeof(json) ? -1 : 0;
    if (fclose(file) != 0) result = -1;
    return result == 0 ? gs_manifest_parse(json, count, manifest) : -1;
}

int gs_sftp_upload_pair(gs_sftp_client_t *client, const char *data_path,
                        const char *manifest_path, const char *remote_directory,
                        const char *expected_from, const char *expected_to,
                        const char *const *allowed_types, size_t allowed_count)
{
    gs_manifest_t manifest;
    char data_final[512], data_part[512], manifest_final[512], manifest_part[512];
    if (client == NULL || data_path == NULL || manifest_path == NULL ||
            load_manifest(manifest_path, &manifest) != 0 ||
            gs_manifest_verify(&manifest, expected_from, expected_to,
                               allowed_types, allowed_count, data_path) != 0 ||
            make_path(data_final, sizeof(data_final), remote_directory, manifest.file, "") != 0 ||
            make_path(data_part, sizeof(data_part), remote_directory, manifest.file, ".partial") != 0 ||
            make_path(manifest_final, sizeof(manifest_final), remote_directory,
                      manifest.file, ".manifest.json") != 0 ||
            make_path(manifest_part, sizeof(manifest_part), remote_directory,
                      manifest.file, ".manifest.json.partial") != 0) return -1;
    if (!remote_absent(client->sftp, data_final) ||
            !remote_absent(client->sftp, manifest_final)) return -1;
    if (upload_file(client->sftp, data_path, data_part) != 0) return -1;
    if (upload_file(client->sftp, manifest_path, manifest_part) != 0) {
        (void)sftp_unlink(client->sftp, data_part);
        return -1;
    }
    if (sftp_rename(client->sftp, data_part, data_final) != SSH_OK) goto fail;
    if (sftp_rename(client->sftp, manifest_part, manifest_final) != SSH_OK) goto fail;
    return 0;
fail:
    (void)sftp_unlink(client->sftp, data_part);
    (void)sftp_unlink(client->sftp, manifest_part);
    return -1;
}

static int download_file(sftp_session session, const char *source, const char *destination,
                         size_t max_bytes)
{
    unsigned char buffer[32768];
    sftp_file remote = NULL;
    int local = -1;
    size_t total = 0u;
    int result = -1;
    if (max_bytes == 0u) return -1;
    remote = sftp_open(session, source, O_RDONLY, 0);
    if (remote == NULL) return -1;
    local = open(destination, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (local < 0) goto done;
    for (;;) {
        ssize_t count = sftp_read(remote, buffer, sizeof(buffer));
        size_t written = 0u;
        if (count < 0 || (size_t)count > max_bytes - total) goto done;
        if (count == 0) break;
        while (written < (size_t)count) {
            ssize_t chunk = write(local, buffer + written, (size_t)count - written);
            if (chunk <= 0) goto done;
            written += (size_t)chunk;
        }
        total += (size_t)count;
    }
    if (fsync(local) == 0) result = 0;
done:
    if (local >= 0 && close(local) != 0) result = -1;
    if (sftp_close(remote) != SSH_OK) result = -1;
    if (result != 0) (void)unlink(destination);
    return result;
}

static int verify_download(const char *data_path, const char *manifest_path,
                           const char *filename, const char *expected_from,
                           const char *expected_to, const char *const *allowed_types,
                           size_t allowed_count)
{
    gs_manifest_t manifest;
    char digest[GS_SHA256_HEX_SIZE];
    size_t index;
    int type_allowed = 0;
    if (load_manifest(manifest_path, &manifest) != 0 ||
            strcmp(manifest.file, filename) != 0 ||
            strcmp(manifest.from, expected_from) != 0 ||
            strcmp(manifest.to, expected_to) != 0) return -1;
    for (index = 0u; index < allowed_count; ++index) {
        if (allowed_types[index] != NULL &&
                strcmp(manifest.type, allowed_types[index]) == 0) type_allowed = 1;
    }
    if (!type_allowed || gs_sha256_file(data_path, digest) != 0) return -1;
    return strcmp(digest, manifest.sha256) == 0 ? 0 : -1;
}

int gs_sftp_download_pair(gs_sftp_client_t *client, const char *remote_directory,
                          const char *filename, const char *local_directory,
                          const char *expected_from, const char *expected_to,
                          const char *const *allowed_types, size_t allowed_count,
                          size_t max_data_bytes)
{
    char remote_data[512], remote_manifest[512], local_data[512];
    char local_manifest[512], data_part[512], manifest_part[512];
    int result = -1;
    if (client == NULL || expected_from == NULL || expected_to == NULL ||
            allowed_types == NULL || local_directory == NULL ||
            make_path(remote_data, sizeof(remote_data), remote_directory, filename, "") != 0 ||
            make_path(remote_manifest, sizeof(remote_manifest), remote_directory,
                      filename, ".manifest.json") != 0 ||
            make_path(local_data, sizeof(local_data), local_directory, filename, "") != 0 ||
            make_path(local_manifest, sizeof(local_manifest), local_directory,
                      filename, ".manifest.json") != 0 ||
            make_path(data_part, sizeof(data_part), local_directory, filename, ".partial") != 0 ||
            make_path(manifest_part, sizeof(manifest_part), local_directory,
                      filename, ".manifest.json.partial") != 0) return -1;
    if (download_file(client->sftp, remote_manifest, manifest_part,
                      GS_MANIFEST_MAX_BYTES) != 0) return -1;
    if (download_file(client->sftp, remote_data, data_part, max_data_bytes) == 0 &&
            verify_download(data_part, manifest_part, filename, expected_from,
                            expected_to, allowed_types, allowed_count) == 0)
        result = gs_exchange_publish_pair(data_part, local_data, manifest_part, local_manifest);
    if (result != 0) {
        (void)unlink(data_part);
        (void)unlink(manifest_part);
    }
    return result;
}
