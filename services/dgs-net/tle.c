#include "tle.h"

#include <string.h>

static int digits(const char *text, size_t start, size_t count)
{
    size_t index;
    for (index = start; index < start + count; ++index)
        if (text[index] < '0' || text[index] > '9') return 0;
    return 1;
}

static int checksum(const char *line)
{
    unsigned int sum = 0u;
    size_t index;
    if (!digits(line, 68u, 1u)) return 0;
    for (index = 0u; index < 68u; ++index) {
        if (line[index] >= '0' && line[index] <= '9')
            sum += (unsigned int)(line[index] - '0');
        else if (line[index] == '-') ++sum;
        else if ((unsigned char)line[index] < 32u ||
                 (unsigned char)line[index] > 126u) return 0;
    }
    return sum % 10u == (unsigned int)(line[68] - '0');
}

int gs_dgs_net_check_tle(const char *text, size_t length, unsigned int expected_satellite)
{
    const char *second;
    unsigned int sat = 0u;
    size_t index;
    if (text == NULL || expected_satellite == 0u ||
            expected_satellite > 99999u ||
            (length != 139u && length != 140u) ||
            text[0] != '1' || text[1] != ' ' || text[69] != '\n') return -1;
    second = text + 70u;
    if (second[0] != '2' || second[1] != ' ' ||
            (length == 140u && text[139] != '\n') ||
            !digits(text, 2u, 5u) || !digits(second, 2u, 5u) ||
            memcmp(text + 2u, second + 2u, 5u) != 0 ||
            !digits(text, 18u, 2u) || !digits(text, 20u, 3u) ||
            !checksum(text) || !checksum(second)) return -1;
    for (index = 2u; index < 7u; ++index)
        sat = sat * 10u + (unsigned int)(text[index] - '0');
    return sat == expected_satellite ? 0 : -1;
}
