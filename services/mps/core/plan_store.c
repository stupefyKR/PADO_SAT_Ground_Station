#include "plan_store.h"

#include <string.h>

static int bounded_id(const char *value, size_t capacity, int underscore)
{
    size_t index;
    if (value == NULL) return 0;
    for (index = 0u; index < capacity; ++index) {
        const char c = value[index];
        if (c == '\0') return index != 0u;
        if ((c < 'A' || c > 'Z') && (underscore || c < 'a' || c > 'z') &&
                (c < '0' || c > '9') && c != '-' &&
                (!underscore || c != '_')) return 0;
    }
    return 0;
}

static int bounded_actor(const char *actor)
{
    size_t index;
    if (actor == NULL) return 0;
    for (index = 0u; index < GS_MPS_ACTOR_SIZE; ++index) {
        const unsigned char c = (unsigned char)actor[index];
        if (c == 0u) return index != 0u;
        if (c < 33u || c > 126u) return 0;
    }
    return 0;
}

static void copy_actor(char destination[GS_MPS_ACTOR_SIZE], const char *actor)
{
    size_t index;
    for (index = 0u; index < GS_MPS_ACTOR_SIZE; ++index) {
        destination[index] = actor[index];
        if (actor[index] == '\0') break;
    }
}

static int find_pass(const gs_mps_pass_catalog_t *passes, const char *id,
                     gs_mps_pass_t *out)
{
    size_t index;
    if (passes == NULL || id == NULL || out == NULL ||
            passes->count > GS_MPS_MAX_PASSES) return -1;
    for (index = 0u; index < passes->count; ++index) {
        if (strcmp(passes->passes[index].pass_id, id) == 0) {
            *out = passes->passes[index];
            return 0;
        }
    }
    return -1;
}

static int same_pass(const gs_mps_pass_t *left, const gs_mps_pass_t *right)
{
    return strcmp(left->pass_id, right->pass_id) == 0 &&
           left->aos_utc_s == right->aos_utc_s &&
           left->los_utc_s == right->los_utc_s &&
           left->guard_before_s == right->guard_before_s &&
           left->guard_after_s == right->guard_after_s;
}

static int valid_step(const gs_mps_step_t *step)
{
    return step->step_id != 0u &&
           bounded_id(step->command_id, GS_MPS_COMMAND_ID_SIZE, 1) &&
           step->duration_s != 0u &&
           (step->require_set & step->require_clear) == 0u &&
           (step->effect_set & step->effect_clear) == 0u;
}

static size_t step_index(const gs_mps_plan_revision_t *plan, uint32_t id)
{
    size_t index;
    for (index = 0u; index < plan->step_count; ++index)
        if (plan->steps[index].step_id == id) return index;
    return plan->step_count;
}

static void sort_steps(gs_mps_plan_revision_t *plan)
{
    size_t index;
    for (index = 1u; index < plan->step_count; ++index) {
        gs_mps_step_t item = plan->steps[index];
        size_t position = index;
        while (position > 0u &&
               (plan->steps[position - 1u].offset_s > item.offset_s ||
                (plan->steps[position - 1u].offset_s == item.offset_s &&
                 plan->steps[position - 1u].step_id > item.step_id))) {
            plan->steps[position] = plan->steps[position - 1u];
            --position;
        }
        plan->steps[position] = item;
    }
}

int gs_mps_plan_create(gs_mps_plan_store_t *store,
                       const gs_mps_pass_catalog_t *passes, const char *pass_id,
                       const gs_mps_command_catalog_t *commands, const char *actor)
{
    gs_mps_pass_t selected;
    gs_mps_plan_revision_t *first;
    if (store == NULL || commands == NULL || store->revision_count != 0u ||
            commands->version == 0u || commands->operational != 0 ||
            commands->command_count != 0u || !bounded_actor(actor) ||
            !bounded_id(pass_id, GS_MPS_PASS_ID_SIZE, 0) ||
            find_pass(passes, pass_id, &selected) != 0) return -1;
    first = &store->history[0];
    *first = (gs_mps_plan_revision_t){0};
    first->revision = 1u;
    first->command_db_version = commands->version;
    first->pass = selected;
    copy_actor(first->edited_by, actor);
    first->non_operational = 1;
    store->revision_count = 1u;
    return 0;
}

int gs_mps_plan_latest(const gs_mps_plan_store_t *store,
                       gs_mps_plan_revision_t *out)
{
    if (store == NULL || out == NULL || store->revision_count == 0u ||
            store->revision_count > GS_MPS_MAX_REVISIONS) return -1;
    *out = store->history[store->revision_count - 1u];
    return 0;
}

int gs_mps_plan_revision(const gs_mps_plan_store_t *store, uint32_t revision,
                         gs_mps_plan_revision_t *out)
{
    if (store == NULL || out == NULL || revision == 0u ||
            revision > store->revision_count ||
            store->revision_count > GS_MPS_MAX_REVISIONS) return -1;
    *out = store->history[revision - 1u];
    return 0;
}

int gs_mps_plan_edit(gs_mps_plan_store_t *store, uint32_t expected_revision,
                     const gs_mps_pass_catalog_t *passes,
                     const gs_mps_command_catalog_t *commands,
                     const gs_mps_edit_t *edit, const char *actor,
                     uint32_t *new_revision)
{
    gs_mps_plan_revision_t next;
    gs_mps_pass_t selected;
    size_t position;
    size_t index;
    if (store == NULL || commands == NULL || edit == NULL ||
            new_revision == NULL || !bounded_actor(actor) ||
            store->revision_count == 0u ||
            store->revision_count >= GS_MPS_MAX_REVISIONS ||
            expected_revision != store->revision_count) return -1;
    next = store->history[store->revision_count - 1u];
    if (next.non_operational != 1 || commands->operational != 0 ||
            commands->command_count != 0u ||
            commands->version != next.command_db_version ||
            find_pass(passes, next.pass.pass_id, &selected) != 0 ||
            !same_pass(&selected, &next.pass)) return -1;
    position = step_index(&next, edit->step_id);
    if (edit->kind == GS_MPS_EDIT_ADD) {
        if (next.step_count >= GS_MPS_MAX_STEPS ||
                position != next.step_count || edit->step_id != edit->step.step_id ||
                !valid_step(&edit->step)) return -1;
        next.steps[next.step_count++] = edit->step;
    } else if (edit->kind == GS_MPS_EDIT_REPLACE) {
        if (position == next.step_count || edit->step_id != edit->step.step_id ||
                !valid_step(&edit->step)) return -1;
        next.steps[position] = edit->step;
    } else if (edit->kind == GS_MPS_EDIT_REMOVE) {
        if (position == next.step_count) return -1;
        for (index = position; index + 1u < next.step_count; ++index)
            next.steps[index] = next.steps[index + 1u];
        --next.step_count;
        next.steps[next.step_count] = (gs_mps_step_t){0};
    } else return -1;
    sort_steps(&next);
    next.revision = (uint32_t)(store->revision_count + 1u);
    copy_actor(next.edited_by, actor);
    store->history[store->revision_count] = next;
    ++store->revision_count;
    *new_revision = next.revision;
    return 0;
}
