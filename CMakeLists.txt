cmake_minimum_required(VERSION 3.20)
project(pado_sat_ground_system LANGUAGES C)

option(GS_ENABLE_SANITIZERS "Enable available address/undefined-behavior sanitizers" OFF)
option(GS_BUILD_MANIFEST_FUZZER "Build libFuzzer manifest parser target with Clang" OFF)

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
    find_package(OpenSSL REQUIRED COMPONENTS Crypto)
    target_link_libraries(gs PUBLIC OpenSSL::Crypto)
endif()

include(CTest)
if(BUILD_TESTING)
    add_executable(gs_stage1_tests tests/stage1_test.c)
    target_link_libraries(gs_stage1_tests PRIVATE gs)
    add_test(NAME gs_stage1_tests COMMAND gs_stage1_tests)
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
