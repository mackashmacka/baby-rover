# ---------------------------------------------------------------------------
# Baby Rover — Pico 2 W motor-characterisation bench firmware
#
#   cmake -S firmware -B firmware/build && cmake --build firmware/build
#   => firmware/build/rover_bench.uf2
#
# Requires PICO_SDK_PATH to point at a pico-sdk checkout. See firmware/README.md.
# ---------------------------------------------------------------------------
cmake_minimum_required(VERSION 3.13)

# The board must be set BEFORE pico_sdk_init(): it selects the RP2350 build,
# the flash size, and the CYW43 pin definitions. "pico2_w" is the Pico 2 W.
# Getting this wrong produces a binary that flashes and does nothing.
set(PICO_BOARD pico2_w CACHE STRING "Target board")

# Pull in the SDK. Using the SDK's own import script rather than a copy
# vendored into this repo, so it cannot go stale against the installed SDK.
if(NOT DEFINED ENV{PICO_SDK_PATH})
    message(FATAL_ERROR
        "PICO_SDK_PATH is not set.\n"
        "  git clone --branch master --recurse-submodules https://github.com/raspberrypi/pico-sdk ~/pico-sdk\n"
        "  export PICO_SDK_PATH=~/pico-sdk\n"
        "See firmware/README.md.")
endif()
include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(rover_bench C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()

# ---------------------------------------------------------------------------
# Build identity
#
# Every characterisation run is stamped with the firmware version and the git
# SHA it was built from (PING returns them, and tools/rover_bench/manifest.py
# records them next to the data). A dataset whose firmware cannot be
# identified cannot be reproduced, and an unreproducible measurement is an
# anecdote. "-dirty" means the working tree had uncommitted changes, so the
# SHA alone does not describe what actually ran.
# ---------------------------------------------------------------------------
set(FW_VERSION "0.1.0")
set(FW_GIT_SHA "unknown")

find_package(Git QUIET)
if(GIT_FOUND)
    execute_process(
        COMMAND ${GIT_EXECUTABLE} rev-parse --short=12 HEAD
        WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}
        OUTPUT_VARIABLE _sha
        OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_QUIET
        RESULT_VARIABLE _sha_rc)
    if(_sha_rc EQUAL 0 AND _sha)
        set(FW_GIT_SHA "${_sha}")
        execute_process(
            COMMAND ${GIT_EXECUTABLE} status --porcelain --untracked-files=no
            WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}
            OUTPUT_VARIABLE _dirty
            OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET)
        if(_dirty)
            set(FW_GIT_SHA "${FW_GIT_SHA}-dirty")
        endif()
    endif()
endif()
message(STATUS "rover_bench ${FW_VERSION} @ ${FW_GIT_SHA}")

# ---------------------------------------------------------------------------
# Target
# ---------------------------------------------------------------------------
add_executable(rover_bench
    src/main.c
    src/motor.c
    src/encoder.c
    src/instrument.c
    src/protocol.c      # host-pure — also built by the host test suite
    src/control.c       # host-pure — also built by the host test suite
)

# Assemble quadrature.pio and generate quadrature.pio.h in the build tree.
pico_generate_pio_header(rover_bench ${CMAKE_CURRENT_LIST_DIR}/src/quadrature.pio)

target_include_directories(rover_bench PRIVATE ${CMAKE_CURRENT_LIST_DIR}/src)

target_compile_definitions(rover_bench PRIVATE
    FW_VERSION="${FW_VERSION}"
    FW_GIT_SHA="${FW_GIT_SHA}"
    FW_BOARD="pico2w"

    # A USB CDC write with nothing reading the other end must not stall the
    # firmware for the SDK's default half a second. The control loop runs in
    # an interrupt and would survive it, but replies would arrive in bursts
    # and every host-side timeout measurement would be nonsense.
    PICO_STDIO_USB_STDOUT_TIMEOUT_US=10000
)

target_compile_options(rover_bench PRIVATE
    -Wall -Wextra -Wshadow -Wconversion -Wdouble-promotion
)

target_link_libraries(rover_bench
    pico_stdlib
    hardware_pwm
    hardware_pio
    hardware_uart
    hardware_watchdog   # RESET reboots through the watchdog
    hardware_clocks     # PWM wrap is derived from the live clk_sys
    hardware_sync       # save_and_disable_interrupts() around the parser
)

# USB CDC is the command channel; UART0 is reserved for the link to the Pi
# (or the FTDI standing in for it) and is driven directly by main.c. If stdio
# also claimed uart0, the two would interleave and neither would be readable.
pico_enable_stdio_usb(rover_bench 1)
pico_enable_stdio_uart(rover_bench 0)

# .uf2, .elf.map, .bin, .dis — the .uf2 is what gets dragged onto the board.
pico_add_extra_outputs(rover_bench)
