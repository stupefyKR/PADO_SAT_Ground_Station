#include "inputs.h"

#include <stddef.h>
#include <stdint.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    gs_mps_command_catalog_t catalog = {0};
    (void)gs_mps_command_catalog_parse((const char *)data, size, &catalog);
    return 0;
}
