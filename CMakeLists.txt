cmake_minimum_required(VERSION 3.20)
project(pado_sat_ground_system LANGUAGES C)

option(GS_ENABLE_SANITIZERS "Enable available address/undefined-behavior sanitizers" OFF)
option(GS_BUILD_MANIFEST_FUZZER "Build libFuzzer manifest parser target with Clang" OFF)
option(GS_BUILD_MPS_CATALOG_FUZZER "Build libFuzzer MPS catalog target with Clang" OFF)
option(GS_BUILD_TM_FUZZER "Build libFuzzer simulator TM target with Clang" OFF)
option(GS_BUILD_TLE_FUZZER "Build libFuzzer TLE target with Clang" OFF)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

add_library(gs_warnings INTERFACE)
if(MSVC)
    target_compile_options(gs_warnings INTERFACE /W4 /WX /GS /sdl)
    target_link_options(gs_warnings INTERFACE /DYNAMICBASE /NXCOMPAT)
    if(GS_ENABLE_SANITIZERS)
        # MSVC provides AddressSanitizer but not UndefinedBehaviorSanitizer.
        target_compile_options(gs_warnings INTERFACE /fsanitize=address /Zi)
        target_link_options(gs_warnings INTERFACE /DEBUG)
    endif()
elseif(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(gs_warnings INTERFACE
        -Wall -Wextra -Werror -fstack-protector-strong -D_FORTIFY_SOURCE=2 -fPIE)
    target_link_options(gs_warnings INTERFACE -pie -Wl,-z,relro,-z,now)
    if(GS_ENABLE_SANITIZERS)
        target_compile_options(gs_warnings INTERFACE -fsanitize=address,undefined -fno-omit-frame-pointer)
        target_link_options(gs_warnings INTERFACE -fsanitize=address,undefined)
    endif()
else()
    message(FATAL_ERROR "Unsupported C compiler: security hardening flags have not been mapped")
endif()

add_library(cjson STATIC third_party/cJSON.c)
target_include_directories(cjson PUBLIC third_party)
target_link_libraries(cjson PRIVATE gs_warnings)

add_library(gs STATIC
    libgs/src/exchange.c
    libgs/src/hash.c
    libgs/src/log.c
    libgs/src/manifest.c)
target_include_directories(gs PUBLIC libgs/include)
target_link_libraries(gs PUBLIC gs_warnings cjson)
if(WIN32)
    target_link_libraries(gs PUBLIC bcrypt)
else()
    find_package(OpenSSL REQUIRED COMPONENTS Crypto SSL)
    find_package(PkgConfig REQUIRED)
    pkg_check_modules(LIBSSH REQUIRED IMPORTED_TARGET libssh)
    target_sources(gs PRIVATE libgs/src/framing.c libgs/src/ipc.c libgs/src/sftp.c)
    target_link_libraries(gs PUBLIC OpenSSL::Crypto OpenSSL::SSL PkgConfig::LIBSSH)
endif()

add_library(gs_mps STATIC services/mps/core/inputs.c
    services/mps/core/plan_store.c services/mps/core/conflict.c
    services/mps/core/sequence_export.c services/mps/core/intake.c)
target_include_directories(gs_mps PUBLIC services/mps/core)
target_link_libraries(gs_mps PUBLIC gs)

add_library(gs_ros_sequence_verify STATIC services/ros/core/sequence_verify.c)
target_include_directories(gs_ros_sequence_verify PUBLIC services/ros/core
    services/mps/core)
target_link_libraries(gs_ros_sequence_verify PUBLIC gs)

add_library(gs_test_tc_wire STATIC services/common/test_tc_wire.c)
target_include_directories(gs_test_tc_wire PUBLIC services/common)
target_link_libraries(gs_test_tc_wire PUBLIC gs)
add_library(gs_ros_tc_workflow STATIC services/ros/core/tc_workflow.c)
target_include_directories(gs_ros_tc_workflow PUBLIC services/ros/core)
target_link_libraries(gs_ros_tc_workflow PUBLIC gs_ros_sequence_verify)
add_library(gs_ros_test_signer STATIC services/ros/signer/test_signer.c)
target_include_directories(gs_ros_test_signer PUBLIC services/ros/signer)
target_link_libraries(gs_ros_test_signer PUBLIC gs_ros_tc_workflow gs_test_tc_wire)
add_library(gs_dgs_tc_gate STATIC services/dgs-gs/tc_gate.c)
target_include_directories(gs_dgs_tc_gate PUBLIC services/dgs-gs)
target_link_libraries(gs_dgs_tc_gate PUBLIC gs_test_tc_wire)
add_library(gs_fake_sat STATIC sim/fake_sat.c)
target_include_directories(gs_fake_sat PUBLIC sim)
target_link_libraries(gs_fake_sat PUBLIC gs_test_tc_wire)

add_library(gs_stage6_exchange STATIC services/common/stage6_routes.c
    services/dgs-net/tle.c services/dgs-archive/catalog.c)
target_include_directories(gs_stage6_exchange PUBLIC services/common
    services/dgs-net services/dgs-archive)
target_link_libraries(gs_stage6_exchange PUBLIC gs)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_executable(gs_stage9_core_probe services/webapp/core_probe.c
        services/mps/core/inputs.c sim/tm_frame.c libgs/src/ipc.c)
    target_include_directories(gs_stage9_core_probe PRIVATE services/mps/core sim libgs/include)
    target_link_libraries(gs_stage9_core_probe PRIVATE cjson gs_warnings m)

    add_library(gs_tm_sim STATIC sim/tm_frame.c services/common/tm_sim_support.c)
    target_include_directories(gs_tm_sim PUBLIC sim services/common)
    target_link_libraries(gs_tm_sim PUBLIC gs)

    add_executable(gs_fake_kari sim/fake_kari.c)
    target_link_libraries(gs_fake_kari PRIVATE gs_tm_sim)
    add_executable(gs_dgs_gs_sim services/dgs-gs/main.c)
    target_link_libraries(gs_dgs_gs_sim PRIVATE gs_tm_sim)
    add_executable(gs_ros_core_sim services/ros/core/main.c)
    target_link_libraries(gs_ros_core_sim PRIVATE gs_tm_sim)
endif()

include(CTest)
if(BUILD_TESTING)
    add_executable(gs_stage1_tests tests/stage1_test.c)
    target_link_libraries(gs_stage1_tests PRIVATE gs)
    add_test(NAME gs_stage1_tests COMMAND gs_stage1_tests)
    add_executable(gs_mps_inputs_tests tests/mps_inputs_test.c)
    target_link_libraries(gs_mps_inputs_tests PRIVATE gs_mps)
    add_test(NAME gs_mps_inputs_tests COMMAND gs_mps_inputs_tests
             ${CMAKE_SOURCE_DIR}/interfaces/command_db/commands.json)
    add_executable(gs_mps_planning_tests tests/mps_planning_test.c)
    target_link_libraries(gs_mps_planning_tests PRIVATE gs_mps)
    add_test(NAME gs_mps_planning_tests COMMAND gs_mps_planning_tests)
    add_executable(gs_stage4_tests tests/stage4_test.c)
    target_link_libraries(gs_stage4_tests PRIVATE gs_mps gs_ros_sequence_verify)
    add_test(NAME gs_stage4_tests COMMAND gs_stage4_tests ${CMAKE_CURRENT_BINARY_DIR})
    add_executable(gs_stage5_tests tests/stage5_test.c)
    target_link_libraries(gs_stage5_tests PRIVATE gs_mps gs_ros_tc_workflow
        gs_ros_test_signer gs_dgs_tc_gate gs_fake_sat)
    add_test(NAME gs_stage5_tests COMMAND gs_stage5_tests ${CMAKE_CURRENT_BINARY_DIR})
    add_executable(gs_stage6_tests tests/stage6_test.c)
    target_link_libraries(gs_stage6_tests PRIVATE gs_stage6_exchange)
    add_test(NAME gs_stage6_tests COMMAND gs_stage6_tests ${CMAKE_CURRENT_BINARY_DIR}
             ${CMAKE_SOURCE_DIR}/interfaces/n2_chart.json)
    if(UNIX)
        find_package(Python3 REQUIRED COMPONENTS Interpreter)
        add_executable(gs_stage2_tests tests/stage2_test.c)
        target_link_libraries(gs_stage2_tests PRIVATE gs)
        add_test(NAME gs_stage2_tests COMMAND ${Python3_EXECUTABLE}
                 ${CMAKE_SOURCE_DIR}/tests/run_stage2.py $<TARGET_FILE:gs_stage2_tests>)
        add_executable(gs_sftp_tests tests/sftp_test.c)
        target_link_libraries(gs_sftp_tests PRIVATE gs_stage6_exchange)
        add_executable(gs_stage6_sftp_tests tests/stage6_sftp_test.c)
        target_link_libraries(gs_stage6_sftp_tests PRIVATE gs_stage6_exchange)
        add_test(NAME gs_sftp_tests COMMAND ${Python3_EXECUTABLE}
                 ${CMAKE_SOURCE_DIR}/tests/run_sftp.py $<TARGET_FILE:gs_sftp_tests>
                 $<TARGET_FILE:gs_stage6_sftp_tests> ${CMAKE_SOURCE_DIR}/interfaces/n2_chart.json)
        add_executable(gs_container_net_probe tests/container/network_probe.c)
        target_link_libraries(gs_container_net_probe PRIVATE gs)
        if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
            add_executable(gs_sim_tm_tests tests/sim_tm_test.c)
            target_link_libraries(gs_sim_tm_tests PRIVATE gs_tm_sim)
            add_test(NAME gs_sim_tm_tests COMMAND gs_sim_tm_tests)
            add_test(NAME gs_stage3_tests COMMAND ${Python3_EXECUTABLE}
                     ${CMAKE_SOURCE_DIR}/tests/run_stage3.py
                     $<TARGET_FILE:gs_fake_kari>
                     $<TARGET_FILE:gs_dgs_gs_sim>
                     $<TARGET_FILE:gs_ros_core_sim>)
        endif()
    endif()
endif()

if(GS_BUILD_MPS_CATALOG_FUZZER)
    if(NOT CMAKE_C_COMPILER_ID STREQUAL "Clang")
        message(FATAL_ERROR "The MPS catalog fuzzer requires Clang/libFuzzer")
    endif()
    add_executable(gs_mps_catalog_fuzzer tests/fuzz_mps_catalog.c)
    target_link_libraries(gs_mps_catalog_fuzzer PRIVATE gs_mps)
    target_compile_options(gs_mps_catalog_fuzzer PRIVATE -fsanitize=fuzzer)
    target_link_options(gs_mps_catalog_fuzzer PRIVATE -fsanitize=fuzzer)
endif()

if(GS_BUILD_MANIFEST_FUZZER)
    if(NOT CMAKE_C_COMPILER_ID STREQUAL "Clang")
        message(FATAL_ERROR "The manifest fuzzer requires Clang/libFuzzer")
    endif()
    add_executable(gs_manifest_fuzzer tests/fuzz_manifest.c)
    target_link_libraries(gs_manifest_fuzzer PRIVATE gs)
    target_compile_options(gs_manifest_fuzzer PRIVATE -fsanitize=fuzzer)
    target_link_options(gs_manifest_fuzzer PRIVATE -fsanitize=fuzzer)
endif()

if(GS_BUILD_TM_FUZZER)
    if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux" OR
            NOT CMAKE_C_COMPILER_ID STREQUAL "Clang")
        message(FATAL_ERROR "The simulator TM fuzzer requires Linux Clang/libFuzzer")
    endif()
    add_executable(gs_tm_fuzzer tests/fuzz_tm_frame.c)
    target_link_libraries(gs_tm_fuzzer PRIVATE gs_tm_sim)
    target_compile_options(gs_tm_fuzzer PRIVATE -fsanitize=fuzzer)
    target_link_options(gs_tm_fuzzer PRIVATE -fsanitize=fuzzer)
endif()

if(GS_BUILD_TLE_FUZZER)
    if(NOT CMAKE_C_COMPILER_ID STREQUAL "Clang")
        message(FATAL_ERROR "The TLE fuzzer requires Clang/libFuzzer")
    endif()
    add_executable(gs_tle_fuzzer tests/fuzz_tle.c)
    target_link_libraries(gs_tle_fuzzer PRIVATE gs_stage6_exchange)
    target_compile_options(gs_tle_fuzzer PRIVATE -fsanitize=fuzzer)
    target_link_options(gs_tle_fuzzer PRIVATE -fsanitize=fuzzer)
endif()
