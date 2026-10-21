#include "test_signer.h"

#include <string.h>

int gs_ros_sign_test_tc(const gs_test_tc_ticket_t *ticket,
                        uint32_t test_scid, uint32_t sequence_number,
                        const unsigned char *key, size_t key_length,
                        unsigned char frame[GS_TEST_TC_FRAME_SIZE])
{
    static const unsigned char magic[8] = {'P','A','D','O','T','C','5','!'};
    size_t index;
    if (frame != NULL)
        for (index = 0u; index < GS_TEST_TC_FRAME_SIZE; ++index)
            frame[index] = 0u;
    if (ticket == NULL || frame == NULL || key == NULL ||
            ticket->validated != 1 || ticket->approved != 1 ||
            ticket->test_only != 1 || ticket->test_code == 0u ||
            ticket->command_db_version == 0u ||
            ticket->approved_at_utc_s == 0u ||
            ticket->approved_at_utc_s > ticket->send_at_utc_s ||
            ticket->send_at_utc_s < ticket->aos_utc_s ||
            ticket->send_at_utc_s >= ticket->los_utc_s ||
            test_scid == 0u || sequence_number == 0u ||
            ticket->author[0] == '\0' || ticket->approved_by[0] == '\0' ||
            memchr(ticket->author, '\0', sizeof(ticket->author)) == NULL ||
            memchr(ticket->approved_by, '\0',
                   sizeof(ticket->approved_by)) == NULL)
        return -1;
    if ((ticket->risk == GS_TEST_TC_NORMAL &&
         (ticket->approved_role != GS_TEST_TC_OPERATOR ||
          strcmp(ticket->author, ticket->approved_by) != 0)) ||
        (ticket->risk == GS_TEST_TC_CAUTION &&
         (ticket->approved_role != GS_TEST_TC_PEER ||
          strcmp(ticket->author, ticket->approved_by) == 0)) ||
        (ticket->risk == GS_TEST_TC_DANGEROUS &&
         (ticket->approved_role != GS_TEST_TC_LEAD ||
          ticket->approval_reauthenticated != 1 ||
          strcmp(ticket->author, ticket->approved_by) == 0))) return -1;
    if (ticket->risk < GS_TEST_TC_NORMAL ||
            ticket->risk > GS_TEST_TC_DANGEROUS) return -1;
    for (index = 0u; index < sizeof(magic); ++index) frame[index] = magic[index];
    frame[8] = 1u;
    frame[9] = (unsigned char)ticket->risk;
    frame[10] = 0u;
    frame[11] = 0u;
    gs_test_tc_put32(frame + 12u, test_scid);
    gs_test_tc_put32(frame + 16u, ticket->test_code);
    gs_test_tc_put32(frame + 20u, sequence_number);
    gs_test_tc_put64(frame + 24u, ticket->send_at_utc_s);
    gs_test_tc_put64(frame + 32u, ticket->aos_utc_s);
    gs_test_tc_put64(frame + 40u, ticket->los_utc_s);
    gs_test_tc_put32(frame + 48u, (uint32_t)ticket->parameter);
    gs_test_tc_put32(frame + 52u, ticket->command_db_version);
    for (index = 0u; index < 32u; ++index)
        frame[56u + index] = ticket->sequence_digest[index];
    if (gs_test_hmac_sha256(key, key_length, frame,
                            GS_TEST_TC_HEADER_SIZE,
                            frame + GS_TEST_TC_HEADER_SIZE) != 0) {
        for (index = 0u; index < GS_TEST_TC_FRAME_SIZE; ++index) frame[index] = 0u;
        return -1;
    }
    return 0;
}
