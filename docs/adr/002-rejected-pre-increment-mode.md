# ADR-002: Rejected — "pre-increment" mode (increment but use previous value)

**Status:** Rejected
**Date:** 2026-03-14

## Context

A user project (win-urbanvpn) uses CBNC in CONFIGURE mode to feed the build number into `project(VERSION ...)`. This means every `cmake --build` triggers a forced reconfigure (via stamp file deletion), which slows down IDE iteration during debugging — even when the developer doesn't care about the build number.

The proposed idea: add a mode that increments the counter (to track "effort" — how many builds happened) but returns the **previous** value to CMake, so the version doesn't change and reconfigure/rebuild overhead is avoided.

## Analysis

### The fundamental contradiction

To increment a counter, code must execute. In CONFIGURE mode, executing code requires a reconfigure. The reconfigure **is** the overhead. Therefore:

- If we skip reconfigure → nothing executes → counter doesn't increment → mode is useless.
- If we allow reconfigure → counter increments → overhead remains → mode doesn't help.

The mode cannot simultaneously avoid reconfigure overhead and perform an increment — these goals are mutually exclusive within CMake's configure/build architecture.

### "React to natural reconfigure" variant

Also considered: skip the forced stamp file deletion and only increment when CMake naturally reconfigures (e.g., when `CMakeLists.txt` changes). This fails because:

- Editing `.cpp` files triggers rebuild but **not** reconfigure.
- The counter would only increment on CMake file changes, which is almost never — defeating the purpose.

### BUILD mode as alternative

Switching the consumer project from CONFIGURE to BUILD mode would avoid reconfigure entirely. However, the consumer project feeds the build number into `project(VERSION ...)`, which sets `PROJECT_VERSION_*` variables used by:

- Windows `.rc` file templates (`FILEVERSION`, `PRODUCTVERSION`)
- `Defines.h.in` templates (runtime version display)
- macOS bundle properties
- `version-full.txt` for CI artifact naming
- Advanced Installer (reads version from compiled binary's RC metadata)

All of these consume `PROJECT_VERSION` at configure time. Switching to BUILD mode would require restructuring the entire version pipeline across ~10 files — disproportionate effort for the problem at hand.

## Decision

**Reject the pre-increment mode. Use `NO_INCREMENT` via a project-level CMake option instead.**

The consumer project defines:

```cmake
option(URB_NO_BUILD_INCREMENT "Skip build number increment (for IDE/debug)" OFF)
```

And conditionally passes `NO_INCREMENT` to `increment_build_number()`. IDE presets set `URB_NO_BUILD_INCREMENT=ON`; CI does not.

With `NO_INCREMENT`:
- No `client.py` call, no stamp file, no forced reconfigure
- Last known build number is read from the local counter file
- All downstream consumers (`project()`, `.rc`, `Defines.h`, `version-full.txt`) work unchanged

The trade-off — IDE builds don't contribute to the counter — is acceptable because tracking "effort" is a nice-to-have, not a requirement.

## Consequences

- No changes to CBNC. The existing `NO_INCREMENT` flag already provides everything needed.
- Consumer projects that want fast IDE iteration should use `NO_INCREMENT` via a project-level option.
- The "effort tracking" use case remains unsolved but is deprioritized as impractical within CMake's architecture.
