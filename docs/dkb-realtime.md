# Bananza Real-time Frame Timing

Bananza 1.0.0 uses whole-vsync integer stepping by default (dividing 60 Hz by 1, 2, or 3), which causes
simulation pacing fluctuations on PC hardware when the display refresh rate or system performance does not
precisely match console vsync.

When "Game speed follows the clock" (`TOTK_REALTIME=1`) is enabled in the configuration or the in-game UI menu,
Bananza adjusts simulation timing to host-measured frame presentation intervals. The setting is opt-in and
defaults to 0 (off).

## Architecture

The real-time timing set (`recomp/patches-dkb100.txt`) uses 9 switchable patch sites and the host pacer
in `runtime/frame_rate.c`:

1. **Host Pacer Integration (`runtime/frame_rate.c`)**:
   - The host maintains a moving average of frame presentation intervals (`seconds`).
   - `RT_PATCH_PAGE + 8`: Stores the exact 60 Hz ratio `seconds * 60.0f` as an IEEE 754 32-bit float.
   - `RT_PATCH_PAGE + 12`: Stores the rounded integer simulation frame rate `(uint32_t)lroundf(1.0f / seconds)`.

2. **Controller Common Exit Hook (`main+0x1ead24`)**:
   - In `al::FrameRateController::calcVsync` (`sub_71001EACE8`), all execution paths converge at `0x1ead24`
     before notifying listener subroutines.
   - The patch reads `RT_PATCH_PAGE + 12` using `x8` as temporary scratch and updates the controller's
     frame rate fields at `[x20, #0x50]` and `[x20, #0x54]`.
   - **Scratch Register Liveness**: `x8` is caller-saved scratch under AAPCS64. `0x1ead24` is immediately
     followed by `bl sub_71001EAF30`, whose very first instruction is `ldr w8, [x0, #0xe0]` (`b940e008`),
     clobbering `w8` before any read.

3. **Present Interval Request (`main+0x1ead2c`)**:
   - In shipping code, `[x20, #0x40]` holds the controller's computed integer vsync divider (1 for 60 Hz,
     2 for 30 Hz), which is passed to `sub_71001EAED0` to call `nvnWindowSetPresentInterval`.
   - Under `realtime`, `mov w1, #1` requests interval 1 so the window swapchain is not constrained to a
     2-vsync lock, allowing the host pacer to pace frames smoothly.
   - When `realtime` is disabled, the original `ldr w1, [x20, #0x40]` executes untouched.

4. **Clock Producers (`1bc834`, `2d9fc8`, `2da120`, `34e99c`, `34ec1c`, `34ec68`, `69fc60`)**:
   - The engine's delta time producers convert integer FPS to a step ratio via `fdiv s0, s1, s0` (`60.0 / fps`).
   - The patch replaces this division with a direct load of the measured floating-point ratio from
     `RT_PATCH_PAGE + 8`. Register `x9` is preserved across the page load.

## Validation

- **Synthetic Differential Verification (`tests/dkb_realtime_test.py`)**:
  Exercises all 9 replacement sites lifted through `recomp/lifter.py` against synthetic
  register and stack states across 15 to 360 FPS at both -O0 and -O2 (9,688 test cases, 0 failures).
- **Hardware Gameplay Testing (Windows x86-64, NVIDIA GPU)**:
  - Verified live in-game across 30 FPS, 60 FPS, and variable/unlocked frame rates with `TOTK_REALTIME=1`.
  - Confirmed consistent 1.0x simulation pace without speedups or slow-motion stalls ("accordion" effect eliminated).
  - Verified toggling `TOTK_REALTIME=0` cleanly reverts to vanilla integer vsync stepping without side effects.
- **Repository Content Compliance**:
  Passed `tools/check_content.py --staged`.

