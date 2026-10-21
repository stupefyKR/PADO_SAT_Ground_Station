#ifndef GS_DGS_NET_TLE_H
#define GS_DGS_NET_TLE_H

#include <stddef.h>

/* Syntax and NORAD checksum only; not orbital truth or source authentication. */
int gs_dgs_net_check_tle(const char *text, size_t length, unsigned int expected_satellite);

#endif
