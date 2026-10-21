#include "tm_frame.h"

#include <stdio.h>
#include <string.h>

static int emit_prefix(size_t length)
{
    unsigned char header[4];
    header[0] = (unsigned char)(length >> 24u);
    header[1] = (unsigned char)(length >> 16u);
    header[2] = (unsigned char)(length >> 8u);
    header[3] = (unsigned char)length;
    return fwrite(header, 1u, sizeof(header), stdout) == sizeof(header) ? 0 : -1;
}

static int emit(const unsigned char *frame, size_t length)
{
    return emit_prefix(length) == 0 && fwrite(frame, 1u, length, stdout) == length ? 0 : -1;
}

int main(int argc, char **argv)
{
    static const unsigned char first[] = "SIM-TM-A";
    static const unsigned char second[] = "SIM-TM-B";
    unsigned char frame[GS_SIM_TM_MAX_FRAME];
    size_t length = 0u;
    if (argc != 2 || strcmp(argv[1], "--sim-only") != 0) {
        fprintf(stderr, "usage: fake_kari --sim-only\n");
        return 2;
    }
    if (gs_sim_tm_build(1u, first, sizeof(first) - 1u, frame,
                        sizeof(frame), &length) != 0 || emit(frame, length) != 0) return 1;
    frame[length - 1u] ^= 1u;
    if (emit(frame, length) != 0) return 1; /* CRC rejection */
    frame[length - 1u] ^= 1u;
    frame[10] ^= 1u;
    if (emit(frame, length) != 0) return 1; /* simulated SCID rejection */
    frame[10] ^= 1u;
    frame[0] ^= 1u;
    if (emit(frame, length) != 0) return 1; /* synchronization rejection */
    if (gs_sim_tm_build(4u, second, sizeof(second) - 1u, frame,
                        sizeof(frame), &length) != 0 || emit(frame, length) != 0) return 1;
    if (emit(frame, 4u) != 0 || emit_prefix(GS_SIM_TM_MAX_FRAME + 1u) != 0)
        return 1; /* short and oversized declarations */
    return fflush(stdout) == 0 ? 0 : 1;
}
