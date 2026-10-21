#include "tm_frame.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;

static void expect(int condition, const char *name)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", name);
        ++failures;
    }
}

int main(void)
{
    static const unsigned char payload[] = {'a', 'b', 'c'};
    unsigned char frame[GS_SIM_TM_MAX_FRAME];
    unsigned char changed[GS_SIM_TM_MAX_FRAME];
    size_t length = 0u;
    uint32_t sequence = 0u;
    expect(gs_sim_tm_build(42u, payload, sizeof(payload), frame,
                           sizeof(frame), &length) == 0 && length == 25u,
           "build bounded simulator frame");
    expect(gs_sim_tm_validate(frame, length, &sequence) == GS_SIM_TM_VALID &&
           sequence == 42u, "validate simulator frame and sequence");
    expect(gs_sim_tm_validate(frame, length - 1u, NULL) == GS_SIM_TM_BAD_LENGTH,
           "reject truncated frame");
    expect(gs_sim_tm_validate(frame, GS_SIM_TM_MAX_FRAME + 1u, NULL) ==
           GS_SIM_TM_BAD_LENGTH, "reject oversized frame");
    expect(gs_sim_tm_validate(NULL, length, NULL) == GS_SIM_TM_BAD_LENGTH,
           "reject null frame");
    memcpy(changed, frame, length);
    changed[0] ^= 1u;
    expect(gs_sim_tm_validate(changed, length, NULL) == GS_SIM_TM_BAD_MAGIC,
           "reject synchronization mismatch");
    memcpy(changed, frame, length);
    changed[8] = 2u;
    expect(gs_sim_tm_validate(changed, length, NULL) == GS_SIM_TM_BAD_VERSION,
           "reject version mismatch");
    memcpy(changed, frame, length);
    changed[9] = 2u;
    expect(gs_sim_tm_validate(changed, length, NULL) == GS_SIM_TM_BAD_KIND,
           "reject frame kind mismatch");
    memcpy(changed, frame, length);
    changed[10] ^= 1u;
    expect(gs_sim_tm_validate(changed, length, NULL) == GS_SIM_TM_BAD_SCID,
           "reject simulator SCID mismatch");
    memcpy(changed, frame, length);
    changed[13] = 4u;
    expect(gs_sim_tm_validate(changed, length, NULL) == GS_SIM_TM_BAD_LENGTH,
           "reject declared payload length mismatch");
    memcpy(changed, frame, length);
    changed[length - 1u] ^= 1u;
    expect(gs_sim_tm_validate(changed, length, NULL) == GS_SIM_TM_BAD_CRC,
           "reject CRC mismatch");
    expect(gs_sim_tm_build(1u, payload, sizeof(payload), frame, length - 1u,
                           &length) != 0, "reject insufficient destination capacity");
    expect(gs_sim_tm_build(1u, payload, 0u, frame, sizeof(frame), &length) != 0,
           "reject empty payload");
    expect(gs_sim_tm_build(1u, payload, GS_SIM_TM_MAX_PAYLOAD + 1u,
                           frame, sizeof(frame), &length) != 0,
           "reject oversized payload before read");
    return failures == 0 ? 0 : 1;
}
