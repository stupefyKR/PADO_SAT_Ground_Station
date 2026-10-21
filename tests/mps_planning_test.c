#include "conflict.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures;

static void expect(int condition, const char *label)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", label);
        ++failures;
    }
}

static gs_mps_step_t step(uint32_t id, uint32_t offset)
{
    gs_mps_step_t value = {0};
    value.step_id = id;
    (void)snprintf(value.command_id, sizeof(value.command_id), "TEST_%u", id);
    value.offset_s = offset;
    value.duration_s = 10u;
    value.resource_mask = 1u;
    value.uplink_bytes = 10u;
    return value;
}

static gs_mps_plan_revision_t clean_plan(void)
{
    gs_mps_plan_revision_t plan = {0};
    plan.revision = 1u;
    plan.command_db_version = 1u;
    plan.pass = (gs_mps_pass_t){"P-TEST-01", 1000, 1100, 10u, 10u};
    plan.steps[0] = step(1u, 20u);
    plan.steps[0].effect_set = 2u;
    plan.steps[1] = step(2u, 40u);
    plan.steps[1].require_set = 2u;
    plan.step_count = 2u;
    plan.non_operational = 1;
    return plan;
}

static void test_versions(void)
{
    gs_mps_plan_store_t *store = calloc(1u, sizeof(*store));
    gs_mps_pass_catalog_t passes = {0};
    gs_mps_pass_t pass = {"P-TEST-01", 1000, 1100, 10u, 10u};
    gs_mps_command_catalog_t commands = {1u, 0u, 0};
    gs_mps_edit_t edit = {0};
    gs_mps_plan_revision_t current = {0};
    gs_mps_plan_revision_t previous = {0};
    uint32_t revision = 0u;
    expect(store != NULL, "allocate bounded history");
    if (store == NULL) return;
    expect(gs_mps_pass_catalog_add(&passes, &pass) == 0, "pass fixture");
    expect(gs_mps_plan_create(store, &passes, pass.pass_id, &commands,
                              "planner.one") == 0, "create non-operational draft");
    expect(gs_mps_plan_create(store, &passes, pass.pass_id, &commands,
                              "planner.one") != 0, "reject duplicate create");
    expect(gs_mps_plan_latest(store, &current) == 0 &&
           current.revision == 1u && current.non_operational == 1 &&
           current.command_db_version == 1u &&
           strcmp(current.pass.pass_id, pass.pass_id) == 0,
           "draft bound to pass and command DB version");
    edit.kind = GS_MPS_EDIT_ADD;
    edit.step_id = 2u;
    edit.step = step(2u, 40u);
    expect(gs_mps_plan_edit(store, 1u, &passes, &commands, &edit,
                            "planner.one", &revision) == 0 && revision == 2u,
           "first edit creates revision");
    edit.step_id = 1u;
    edit.step = step(1u, 20u);
    expect(gs_mps_plan_edit(store, 1u, &passes, &commands, &edit,
                            "planner.two", &revision) != 0,
           "reject concurrent stale revision");
    expect(gs_mps_plan_edit(store, 2u, &passes, &commands, &edit,
                            "planner.two", &revision) == 0 && revision == 3u,
           "accept fresh revision");
    expect(gs_mps_plan_latest(store, &current) == 0 &&
           current.step_count == 2u && current.steps[0].step_id == 1u &&
           strcmp(current.edited_by, "planner.two") == 0,
           "sort steps and retain editor");
    expect(gs_mps_plan_revision(store, 2u, &previous) == 0 &&
           previous.step_count == 1u && previous.steps[0].step_id == 2u,
           "previous revision remains immutable");
    edit.kind = GS_MPS_EDIT_REPLACE;
    edit.step_id = 1u;
    edit.step = step(1u, 60u);
    expect(gs_mps_plan_edit(store, 3u, &passes, &commands, &edit,
                            "planner.one", &revision) == 0 && revision == 4u,
           "replace step");
    expect(gs_mps_plan_latest(store, &current) == 0 &&
           current.steps[1].step_id == 1u, "resort replaced step");
    edit.kind = GS_MPS_EDIT_REMOVE;
    expect(gs_mps_plan_edit(store, 4u, &passes, &commands, &edit,
                            "planner.one", &revision) == 0 && revision == 5u,
           "remove step");
    expect(gs_mps_plan_latest(store, &current) == 0 &&
           current.step_count == 1u && current.steps[0].step_id == 2u,
           "removed step absent");
    commands.version = 2u;
    expect(gs_mps_plan_edit(store, 5u, &passes, &commands, &edit,
                            "planner.one", &revision) != 0,
           "reject changed command DB version");
    commands.version = 1u;
    passes.passes[0].los_utc_s = 1200;
    expect(gs_mps_plan_edit(store, 5u, &passes, &commands, &edit,
                            "planner.one", &revision) != 0,
           "reject changed pass window");
    passes.passes[0] = pass;
    commands.operational = 1;
    expect(gs_mps_plan_edit(store, 5u, &passes, &commands, &edit,
                            "planner.one", &revision) != 0,
           "reject operational catalogue without ICD");
    commands.operational = 0;
    edit.kind = GS_MPS_EDIT_ADD;
    edit.step_id = 3u;
    edit.step = step(3u, 70u);
    edit.step.duration_s = 0u;
    expect(gs_mps_plan_edit(store, 5u, &passes, &commands, &edit,
                            "planner.one", &revision) != 0,
           "reject malformed step without consuming revision");
    expect(store->revision_count == 5u &&
           gs_mps_plan_revision(store, 6u, &previous) != 0,
           "history unchanged after rejected edits");
    edit.kind = GS_MPS_EDIT_REPLACE;
    edit.step_id = 2u;
    edit.step = step(2u, 40u);
    while (store->revision_count < GS_MPS_MAX_REVISIONS) {
        if (gs_mps_plan_edit(store, (uint32_t)store->revision_count,
                             &passes, &commands, &edit, "planner.one",
                             &revision) != 0) {
            expect(0, "fill bounded revision history");
            break;
        }
    }
    expect(gs_mps_plan_edit(store, revision, &passes, &commands, &edit,
                            "planner.one", &revision) != 0,
           "reject revision history overflow");
    free(store);
}

static void test_rules(void)
{
    gs_mps_plan_revision_t plan = clean_plan();
    gs_mps_test_profile_t profile = {0u, 8u, 2u, 1};
    gs_mps_analysis_t analysis = {UINT32_MAX, 0};
    expect(gs_mps_analyze_test_plan(&plan, &profile, &analysis) == 0 &&
           analysis.violations == 0u && analysis.non_operational == 1,
           "clean synthetic plan passes six rules without operational clearance");
    plan.steps[0].offset_s = 5u;
    expect(gs_mps_check_time_window(&plan) == 1, "window start guard violation");
    plan.steps[0].offset_s = 85u;
    plan.steps[0].duration_s = 10u;
    expect(gs_mps_check_time_window(&plan) == 1, "window end guard violation");
    plan = clean_plan();
    plan.steps[1].offset_s = 25u;
    expect(gs_mps_check_overlap(&plan) == 1, "execution overlap violation");
    expect(gs_mps_check_resource(&plan) == 1, "shared resource overlap violation");
    plan.steps[1].resource_mask = 2u;
    expect(gs_mps_check_resource(&plan) == 0,
           "different resources do not cause resource conflict");
    plan = clean_plan();
    plan.steps[0].effect_set = 0u;
    expect(gs_mps_check_precondition(&plan, 0u) == 1,
           "unmet precondition violation");
    plan = clean_plan();
    plan.steps[0].uplink_bytes = 81u;
    expect(gs_mps_check_data_volume(&plan, 8u) == 1,
           "uplink exceeds usable pass capacity");
    plan.steps[0].uplink_bytes = 60u;
    plan.steps[1].uplink_bytes = 20u;
    expect(gs_mps_check_data_volume(&plan, 8u) == 0,
           "uplink at exact usable pass capacity");
    plan.steps[0].uplink_bytes = UINT64_MAX;
    expect(gs_mps_check_data_volume(&plan, 8u) == 1,
           "reject summed uplink byte overflow");
    plan.steps[0].uplink_bytes = 60u;
    expect(gs_mps_check_command_count(&plan, 1u) == 1,
           "command count limit violation");
    profile.max_commands = 1u;
    expect(gs_mps_analyze_test_plan(&plan, &profile, &analysis) == 0 &&
           analysis.violations == GS_MPS_CONFLICT_COUNT,
           "analysis identifies exact violated rule");
    profile.test_only = 0;
    expect(gs_mps_analyze_test_plan(&plan, &profile, &analysis) != 0,
           "refuse unapproved operational profile");
    plan.non_operational = 0;
    expect(gs_mps_check_overlap(&plan) != 0,
           "refuse operational plan without approved command contract");
}

int main(void)
{
    test_versions();
    test_rules();
    return failures == 0 ? 0 : 1;
}
