cmake_minimum_required(VERSION 3.20)
project(pado_sat_ground_system LANGUAGES C)

option(GS_ENABLE_SANITIZERS "Enable available address/undefined-behavior sanitizers" OFF)

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

# This temporary anchor gives stage 1 a real static library build and link check.
add_library(gs STATIC libgs/src/bootstrap.c)
target_include_directories(gs PUBLIC libgs/include)
target_link_libraries(gs PUBLIC gs_warnings)

include(CTest)
if(BUILD_TESTING)
    add_executable(gs_build_smoke tests/build_smoke.c)
    target_link_libraries(gs_build_smoke PRIVATE gs)
    add_test(NAME gs_build_smoke COMMAND gs_build_smoke)
endif()
