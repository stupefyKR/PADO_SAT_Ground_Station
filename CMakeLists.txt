cmake_minimum_required(VERSION 3.20)
project(pado_sat_ground_system LANGUAGES C)

option(GS_ENABLE_SANITIZERS "Enable available address/undefined-behavior sanitizers" OFF)
option(GS_BUILD_MANIFEST_FUZZER "Build libFuzzer manifest parser target with Clang" OFF)
option(GS_BUILD_MPS_CATALOG_FUZZER "Build libFuzzer MPS catalog target with Clang" OFF)

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

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
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
    if(UNIX)
        find_package(Python3 REQUIRED COMPONENTS Interpreter)
        add_executable(gs_stage2_tests tests/stage2_test.c)
        target_link_libraries(gs_stage2_tests PRIVATE gs)
        add_test(NAME gs_stage2_tests COMMAND ${Python3_EXECUTABLE}
                 ${CMAKE_SOURCE_DIR}/tests/run_stage2.py $<TARGET_FILE:gs_stage2_tests>)
        add_executable(gs_sftp_tests tests/sftp_test.c)
        target_link_libraries(gs_sftp_tests PRIVATE gs)
        add_test(NAME gs_sftp_tests COMMAND ${Python3_EXECUTABLE}
                 ${CMAKE_SOURCE_DIR}/tests/run_sftp.py $<TARGET_FILE:gs_sftp_tests>)
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
