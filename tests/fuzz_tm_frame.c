#include "tm_frame.h"

#include <stddef.h>
#include <stdint.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    uint32_t sequence = 0u;
    (void)gs_sim_tm_validate(data, size, &sequence);
    return 0;
}
