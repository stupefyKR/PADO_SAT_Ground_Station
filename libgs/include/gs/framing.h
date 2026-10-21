#ifndef GS_FRAMING_H
#define GS_FRAMING_H

#include <openssl/ssl.h>
#include <stddef.h>

#define GS_TLS_FRAME_MAX_BYTES 1048576u

/* All certificates and keys are runtime files outside the repository. */
SSL_CTX *gs_tls_context_new(const char *certificate_path, const char *key_path,
                            const char *ca_path, int server);
SSL *gs_tls_session_new(SSL_CTX *context, int fd, int server,
                        const char *expected_peer_ip);
int gs_tls_send_frame(SSL *session, const unsigned char *data, size_t length);
int gs_tls_recv_frame(SSL *session, unsigned char *buffer, size_t capacity,
                      size_t *length);

#endif
