#include "gs/log.h"
#include "cJSON.h"

#include <string.h>

static int add_string(cJSON *object, const char *key, const char *value)
{
    if (value == NULL || strlen(value) > 512u) return -1;
    return cJSON_AddStringToObject(object, key, value) != NULL ? 0 : -1;
}

int gs_log_write(FILE *out, const char *utc_timestamp, const char *service,
                 const char *zone, const char *event, const char *detail)
{
    cJSON *object;
    char *line;
    int result = -1;
    if (out == NULL) return -1;
    object = cJSON_CreateObject();
    if (object == NULL) return -1;
    if (add_string(object, "timestamp", utc_timestamp) != 0 ||
            add_string(object, "service", service) != 0 ||
            add_string(object, "zone", zone) != 0 ||
            add_string(object, "event", event) != 0 ||
            add_string(object, "detail", detail) != 0) goto done;
    line = cJSON_PrintUnformatted(object);
    if (line == NULL) goto done;
    if (fprintf(out, "%s\n", line) >= 0 && fflush(out) == 0) result = 0;
    cJSON_free(line);
done:
    cJSON_Delete(object);
    return result;
}
