#ifndef GS_TM_SIM_SUPPORT_H
#define GS_TM_SIM_SUPPORT_H

#include <stddef.h>
#include <stdio.h>

/* Append a tagged, length-prefixed record to a private regular file. */
int gs_sim_record_append(const char *path, unsigned char tag,
                         const unsigned char *data, size_t length);
int gs_sim_log(FILE *out, const char *service, const char *zone,
               const char *event, const char *detail);

#endif
