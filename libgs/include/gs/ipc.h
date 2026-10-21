#ifndef GS_IPC_H
#define GS_IPC_H

#include <stddef.h>

#define GS_IPC_MAX_BYTES 65536u

/* POSIX Unix-domain socket only. One complete JSON object per frame. */
int gs_ipc_send_json(int fd, const char *json, size_t length);
int gs_ipc_recv_json(int fd, char *buffer, size_t capacity, size_t *length);

#endif
