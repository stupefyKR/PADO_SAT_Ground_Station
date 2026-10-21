#include "gs/framing.h"

#include <limits.h>
#include <stdint.h>

SSL_CTX *gs_tls_context_new(const char *certificate_path, const char *key_path,
                            const char *ca_path, int server)
{
    SSL_CTX *context;
    if (certificate_path == NULL || key_path == NULL || ca_path == NULL) return NULL;
    context = SSL_CTX_new(server ? TLS_server_method() : TLS_client_method());
    if (context == NULL) return NULL;
    if (SSL_CTX_set_min_proto_version(context, TLS1_3_VERSION) != 1 ||
            SSL_CTX_use_certificate_file(context, certificate_path, SSL_FILETYPE_PEM) != 1 ||
            SSL_CTX_use_PrivateKey_file(context, key_path, SSL_FILETYPE_PEM) != 1 ||
            SSL_CTX_check_private_key(context) != 1 ||
            SSL_CTX_load_verify_locations(context, ca_path, NULL) != 1) {
        SSL_CTX_free(context);
        return NULL;
    }
    SSL_CTX_set_verify(context, SSL_VERIFY_PEER | (server ? SSL_VERIFY_FAIL_IF_NO_PEER_CERT : 0),
                       NULL);
    return context;
}

SSL *gs_tls_session_new(SSL_CTX *context, int fd, int server,
                        const char *expected_peer_ip)
{
    SSL *session;
    if (context == NULL || fd < 0 || expected_peer_ip == NULL) return NULL;
    session = SSL_new(context);
    if (session == NULL) return NULL;
    if (SSL_set_fd(session, fd) != 1 ||
            X509_VERIFY_PARAM_set1_ip_asc(SSL_get0_param(session), expected_peer_ip) != 1 ||
            (server ? SSL_accept(session) : SSL_connect(session)) != 1 ||
            SSL_get_verify_result(session) != X509_V_OK ||
            SSL_get0_peer_certificate(session) == NULL) {
        SSL_free(session);
        return NULL;
    }
    return session;
}

static int ssl_write_all(SSL *session, const unsigned char *data, size_t length)
{
    size_t done = 0u;
    while (done < length) {
        int count = SSL_write(session, data + done, (int)(length - done));
        if (count <= 0) return -1;
        done += (size_t)count;
    }
    return 0;
}

static int ssl_read_all(SSL *session, unsigned char *data, size_t length)
{
    size_t done = 0u;
    while (done < length) {
        int count = SSL_read(session, data + done, (int)(length - done));
        if (count <= 0) return -1;
        done += (size_t)count;
    }
    return 0;
}

int gs_tls_send_frame(SSL *session, const unsigned char *data, size_t length)
{
    unsigned char header[4];
    if (session == NULL || data == NULL || length == 0u ||
            length > GS_TLS_FRAME_MAX_BYTES || length > INT_MAX) return -1;
    header[0] = (unsigned char)(length >> 24u);
    header[1] = (unsigned char)(length >> 16u);
    header[2] = (unsigned char)(length >> 8u);
    header[3] = (unsigned char)length;
    if (ssl_write_all(session, header, sizeof(header)) != 0) return -1;
    return ssl_write_all(session, data, length);
}

int gs_tls_recv_frame(SSL *session, unsigned char *buffer, size_t capacity,
                      size_t *length)
{
    unsigned char header[4];
    uint32_t frame_length;
    if (session == NULL || buffer == NULL || length == NULL) return -1;
    if (ssl_read_all(session, header, sizeof(header)) != 0) return -1;
    frame_length = ((uint32_t)header[0] << 24u) | ((uint32_t)header[1] << 16u) |
                   ((uint32_t)header[2] << 8u) | (uint32_t)header[3];
    if (frame_length == 0u || frame_length > GS_TLS_FRAME_MAX_BYTES ||
            frame_length > capacity) return -1;
    if (ssl_read_all(session, buffer, frame_length) != 0) return -1;
    *length = frame_length;
    return 0;
}
