#include "tle.h"

#include <stddef.h>
#include <stdint.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    (void)gs_dgs_net_check_tle((const char *)data, size, 12345u);
    return 0;
}
