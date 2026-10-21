#include "tm_frame.h"

#include <string.h>

static const unsigned char magic[8] = {'P', 'S', 'T', 'S', 'I', 'M', '0', '3'};

static uint32_t read32(const unsigned char *data)
{
    return ((uint32_t)data[0] << 24u) | ((uint32_t)data[1] << 16u) |
           ((uint32_t)data[2] << 8u) | (uint32_t)data[3];
}

static void write32(unsigned char *data, uint32_t value)
{
    data[0] = (unsigned char)(value >> 24u);
    data[1] = (unsigned char)(value >> 16u);
    data[2] = (unsigned char)(value >> 8u);
    data[3] = (unsigned char)value;
}

static uint32_t crc32_ieee(const unsigned char *data, size_t length)
{
    uint32_t crc = 0xffffffffu;
    size_t index;
    for (index = 0u; index < length; ++index) {
        unsigned int bit;
        crc ^= (uint32_t)data[index];
        for (bit = 0u; bit < 8u; ++bit)
            crc = (crc >> 1u) ^ ((crc & 1u) != 0u ? 0xedb88320u : 0u);
    }
    return ~crc;
}

int gs_sim_tm_build(uint32_t sequence, const unsigned char *payload, size_t payload_length,
                    unsigned char *frame, size_t capacity, size_t *frame_length)
{
    size_t length;
    if (payload == NULL || frame == NULL || frame_length == NULL ||
            payload_length == 0u || payload_length > GS_SIM_TM_MAX_PAYLOAD) return -1;
    length = GS_SIM_TM_HEADER_SIZE + payload_length + 4u;
    if (capacity < length) return -1;
    memcpy(frame, magic, sizeof(magic));
    frame[8] = 1u;
    frame[9] = 1u;
    frame[10] = (unsigned char)(GS_SIM_TM_SCID >> 8u);
    frame[11] = (unsigned char)GS_SIM_TM_SCID;
    frame[12] = (unsigned char)(payload_length >> 8u);
    frame[13] = (unsigned char)payload_length;
    write32(frame + 14u, sequence);
    memcpy(frame + GS_SIM_TM_HEADER_SIZE, payload, payload_length);
    write32(frame + length - 4u, crc32_ieee(frame, length - 4u));
    *frame_length = length;
    return 0;
}

gs_sim_tm_result_t gs_sim_tm_validate(const unsigned char *frame, size_t frame_length,
                                       uint32_t *sequence)
{
    size_t payload_length;
    if (frame == NULL || frame_length < GS_SIM_TM_HEADER_SIZE + 5u ||
            frame_length > GS_SIM_TM_MAX_FRAME) return GS_SIM_TM_BAD_LENGTH;
    if (memcmp(frame, magic, sizeof(magic)) != 0) return GS_SIM_TM_BAD_MAGIC;
    if (frame[8] != 1u) return GS_SIM_TM_BAD_VERSION;
    if (frame[9] != 1u) return GS_SIM_TM_BAD_KIND;
    if (((unsigned int)frame[10] << 8u | (unsigned int)frame[11]) != GS_SIM_TM_SCID)
        return GS_SIM_TM_BAD_SCID;
    payload_length = ((size_t)frame[12] << 8u) | (size_t)frame[13];
    if (payload_length == 0u || payload_length > GS_SIM_TM_MAX_PAYLOAD ||
            frame_length != GS_SIM_TM_HEADER_SIZE + payload_length + 4u)
        return GS_SIM_TM_BAD_LENGTH;
    if (read32(frame + frame_length - 4u) != crc32_ieee(frame, frame_length - 4u))
        return GS_SIM_TM_BAD_CRC;
    if (sequence != NULL) *sequence = read32(frame + 14u);
    return GS_SIM_TM_VALID;
}

const char *gs_sim_tm_result_name(gs_sim_tm_result_t result)
{
    switch (result) {
    case GS_SIM_TM_VALID: return "valid";
    case GS_SIM_TM_BAD_LENGTH: return "length";
    case GS_SIM_TM_BAD_MAGIC: return "magic";
    case GS_SIM_TM_BAD_VERSION: return "version";
    case GS_SIM_TM_BAD_KIND: return "kind";
    case GS_SIM_TM_BAD_SCID: return "scid";
    case GS_SIM_TM_BAD_CRC: return "crc";
    }
    return "unknown";
}
