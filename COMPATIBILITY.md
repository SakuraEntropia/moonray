# System compatibility fixes (macOS 27 / Apple Silicon / clang 21 / CMake 4.4)

The user's machine is an **M5 MacBook Air, macOS 27, Xcode 26.6 (CLT active),
AppleClang 21, CMake 4.4.3, Blender 5.2 Alpha**. MoonRay officially supports
macOS 14/15 with Xcode 15/16, so several adjustments were needed.

## Repository layout

- `OpenMoonRay/moonray` (the repo the user asked to clone) is the **render
  engine component** and uses DreamWorks' internal rez/SCons build system
  (`package.py`, `SDKScript`) that only works inside the studio infrastructure.
  It is checked out at the workspace root.
- `OpenMoonRay/openmoonray` is the **official superproject** that references
  this engine repo as the `moonray/moonray` Git submodule and carries the
  public CMake build + macOS support. It is checked out at `openmoonray/`
  and is what we build.

## Fixes applied

### 1. Generator mismatch in the dependency superbuild
`building/macOS/CMakeLists.txt` hardcodes `make ${JOBS_ARG}` as the build
command for several ExternalProjects. Configuring the superbuild with Ninja
(which we wanted) made the inner builds generate `build.ninja` while `make`
ran → "No targets specified and no makefile found" on Blosc.
**Fix:** configure the superbuild with the default Unix Makefiles generator
(the documented path; the superbuild itself only orchestrates stamps).

### 2. Qt 5.12.12 cannot build with modern toolchains (and is unneeded)
Qt 5.12 predates clang 15+ and fails on current macOS SDKs. The Blender
integration only needs the `moonray` CLI, not `moonray_gui`.
**Fix:** added `option(SKIP_QT)` to `building/macOS/CMakeLists.txt` and guard
the `qt5` ExternalProject; build with `-DSKIP_QT=ON`. The main build is
configured with `-DBUILD_QT_APPS=NO`.

### 3. Memory-bounded parallelism
24 GB RAM is not enough for `-j10` on the biggest deps (Boost/USD).
**Fix:** added `MAX_BUILD_JOBS` (default 6) cap in the superbuild.

### 4. Xcode generator unusable (CLT-only developer dir)
The official `macos-release` preset uses the Xcode generator, which requires
`xcodebuild`, but this machine's active developer directory is the Command
Line Tools. Switching to full Xcode needs sudo, which is unavailable.
**Fix:** `CMakeUserPresets.json` adds `macos-release-ninja` (inherits the
official preset, overrides the generator to Ninja). AppleClang from CLT is
used for the whole build.

### 5. Blender 5.x API removals in the add-on
Blender 5.2 removed `Mesh.loops`, `Mesh.calc_normals_split()` and
`MeshUVLoopLayer.data` (renamed to `corners`/`uv`).
**Fix:** `blender_addon/exporter.py` uses the new API with fallbacks for
Blender 4.x.

### 6. `installs/{bin,lib,include}` must exist before the superbuild runs
The Lua dependency's install step copies `lua`/`luac` into
`${InstallRoot}/bin` without creating the directory ("cp: .../bin: Not a
directory" failure). The official docs Step 1 pre-creates these folders.
**Fix:** `mkdir -p installs/{bin,lib,include}` before building deps.

### 7. Skip the unit tests in the main build
`moonray/CMakeLists.txt` gates `add_subdirectory(tests)` on
`CMAKE_PROJECT_NAME STREQUAL PROJECT_NAME AND BUILD_TESTING`, which is true
for the top-level superproject build (and `include(CTest)` defaults
`BUILD_TESTING` to ON). Building the test suite would multiply compile time
and requires CppUnit to behave under clang 21.
**Fix:** `-DBUILD_TESTING=OFF` in `CMakeUserPresets.json`.

### 8. Unreliable GitHub clones on this network
Full clones repeatedly died with "fetch-pack: invalid index-pack output" /
"RPC failed; curl 56", and ExternalProject hung on the dead clone.
**Fix:** `GIT_SHALLOW TRUE` + `GIT_PROGRESS TRUE` on every git-based
dependency in the superbuild, plus global git hardening
(`http.postBuffer`, `http.version HTTP/1.1`, low-speed timeout).
Note: changing ExternalProject arguments invalidates its stamps, so already
built deps were re-run once (object caches made this cheap).

### 9. Blender 5.2 alpha RenderEngine API regressions
- Engine `__init__` is called with an argument and any *instance attribute*
  access on the engine raises `ReferenceError: StructRNA ... has been
  removed` (method calls like `report`/`update_stats`/`test_break`/
  `begin_result` still work).
- After the render, Blender calls `render()` a second time on the
  already-released engine struct.
**Fix:** the engine stores NO instance state (locals only, class-level
constants), `__init__(self, *args)` ignores the argument, and `render()`
catches `ReferenceError` from the phantom second invocation.

## Status

- Dependency superbuild: running in the background (Blosc, Boost, JsonCpp,
  Lua, MicroHttpd, OpenSubdiv, OpenEXR, TBB installed; OpenVDB building).
  One-shot completion script: `finish_build_and_test.sh`.
- Main build: `build_moonray.sh` runs `cmake --preset macos-release-ninja` +
  `cmake --build --preset macos-release-ninja`.
- Add-on: complete and tested (export 15/15 checks, registration, engine
  mock end-to-end, renderer unit test); installed into Blender via
  `install_addon.sh`.
