#ifndef GS_EXCHANGE_H
#define GS_EXCHANGE_H

#include <stddef.h>

/* Paths must be on the same filesystem. Destination must not already exist. */
int gs_exchange_publish(const char *temporary_path, const char *published_path);
/* Publish data first, manifest last. Orphan data after failure is not processed. */
int gs_exchange_publish_pair(const char *temporary_data, const char *published_data,
                             const char *temporary_manifest, const char *published_manifest);
int gs_exchange_verify_pair(const char *published_data, const char *published_manifest,
                            const char *expected_from, const char *expected_to,
                            const char *const *allowed_types, size_t allowed_count);

#endif
