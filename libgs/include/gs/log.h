#ifndef GS_LOG_H
#define GS_LOG_H

#include <stdio.h>

/* One JSON object per line. Never pass secrets as fields. */
int gs_log_write(FILE *out, const char *utc_timestamp, const char *service,
                 const char *zone, const char *event, const char *detail);

#endif
