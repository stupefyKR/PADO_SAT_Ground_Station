#ifndef GS_SIM_TM_FRAME_H
#define GS_SIM_TM_FRAME_H

#include <stddef.h>
#include <stdint.h>

/* Lab-only format. These values are not a spacecraft ICD or flight SCID. */
#define GS_SIM_TM_HEADER_SIZE 18u
#define GS_SIM_TM_MAX_PAYLOAD 256u
#define GS_SIM_TM_MAX_FRAME (GS_SIM_TM_HEADER_SIZE + GS_SIM_TM_MAX_PAYLOAD + 4u)
#define GS_SIM_TM_SCID 0x7e57u
#define GS_SIM_MSG_TM 0x54u
#define GS_SIM_MSG_END 0x45u

typedef enum {
    GS_SIM_TM_VALID = 0,
    GS_SIM_TM_BAD_LENGTH = 1,
    GS_SIM_TM_BAD_MAGIC = 2,
    GS_SIM_TM_BAD_VERSION = 3,
    GS_SIM_TM_BAD_KIND = 4,
    GS_SIM_TM_BAD_SCID = 5,
    GS_SIM_TM_BAD_CRC = 6
} gs_sim_tm_result_t;

int gs_sim_tm_build(uint32_t sequence, const unsigned char *payload, size_t payload_length,
                    unsigned char *frame, size_t capacity, size_t *frame_length);
gs_sim_tm_result_t gs_sim_tm_validate(const unsigned char *frame, size_t frame_length,
                                       uint32_t *sequence);
const char *gs_sim_tm_result_name(gs_sim_tm_result_t result);

#endif
