#define _POSIX_C_SOURCE 200809L

#include "tm_sim_support.h"
#include "gs/log.h"
#include "tm_frame.h"

#include <fcntl.h>
#include <stdint.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

int gs_sim_record_append(const char *path, unsigned char tag,
                         const unsigned char *data, size_t length)
{
    unsigned char record[5u + GS_SIM_TM_MAX_FRAME];
    struct stat status;
    size_t done = 0u;
    size_t index;
    size_t record_length = 5u + length;
    int fd;
    int result = -1;
    if (path == NULL || tag == 0u || length > GS_SIM_TM_MAX_FRAME ||
            (length > 0u && data == NULL)) return -1;
    record[0] = tag;
    record[1] = (unsigned char)(length >> 24u);
    record[2] = (unsigned char)(length >> 16u);
    record[3] = (unsigned char)(length >> 8u);
    record[4] = (unsigned char)length;
    for (index = 0u; index < length; ++index) record[5u + index] = data[index];
    fd = open(path, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) return -1;
    if (fstat(fd, &status) != 0 || !S_ISREG(status.st_mode) ||
            status.st_uid != geteuid() || status.st_nlink != 1 ||
            (status.st_mode & 0077) != 0) goto done;
    while (done < record_length) {
        ssize_t count = write(fd, record + done, record_length - done);
        if (count <= 0) goto done;
        done += (size_t)count;
    }
    if (fsync(fd) == 0) result = 0;
done:
    if (close(fd) != 0) result = -1;
    return result;
}

int gs_sim_log(FILE *out, const char *service, const char *zone,
               const char *event, const char *detail)
{
    char timestamp[32];
    struct tm utc;
    time_t now = time(NULL);
    if (now == (time_t)-1 || gmtime_r(&now, &utc) == NULL ||
            strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &utc) == 0u)
        return -1;
    return gs_log_write(out, timestamp, service, zone, event, detail);
}
