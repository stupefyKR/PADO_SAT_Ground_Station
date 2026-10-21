#include "gs/manifest.h"

#include <stddef.h>
#include <stdint.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    gs_manifest_t manifest;
    if (size <= GS_MANIFEST_MAX_BYTES) {
        (void)gs_manifest_parse((const char *)data, size, &manifest);
    }
    return 0;
}
